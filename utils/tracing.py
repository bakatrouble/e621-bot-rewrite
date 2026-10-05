"""OpenTelemetry -> Grafana Tempo tracing setup.

Tempo accepts OTLP over gRPC (default :4317) and HTTP (:4318), so we use
the standard OTLP exporter and rely on standard OTEL env vars where set:

  OTEL_EXPORTER_OTLP_ENDPOINT  (e.g. http://tempo:4317)
  OTEL_SERVICE_NAME            (overrides service_name argument)

Config file (`config.yaml`, `tracing:` section) provides defaults so local
runs without Tempo still work - when `enabled: false` or no endpoint is
reachable, setup is a no-op and `traced` becomes a pass-through.

Auth: if the endpoint URL embeds `https://user:pass@host` userinfo, it is
stripped from the gRPC channel target and sent as an `Authorization: Basic`
header instead (the OTLP exporter does not understand URL userinfo and would
otherwise fail to connect). This matches a Tempo behind a basic-auth proxy.

Note on h2c: if Tempo serves cleartext HTTP/2 (h2c), the endpoint scheme must
be `http://`, not `https://` - a TLS handshake against an h2c port fails.

Debugging: `main.py debug-tracing` (or `run_tracing_diagnostics()`) prints
the effective endpoint (password redacted), checks TCP connectivity, sends a
test span and force-flushes the pipeline, reporting exactly where delivery
breaks down.
"""

from __future__ import annotations

import atexit
import base64
import functools
import logging
import os
import socket
import time
from typing import Callable, ParamSpec, TypeVar
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger('tracing')

_tracer = None
_instrumented = False
_shutdown_registered = False
_shutdown_done = False

_state: dict = {
    'configured': False,
    'service_name': None,
    'endpoint': None,           # effective endpoint handed to the exporter
    'endpoint_raw': None,       # as configured, password redacted
    'auth': 'none',             # 'basic' | 'none'
    'environment': None,
    'sample_ratio': None,
    'enabled': None,
    'instrumentation': {},      # name -> 'ok' | 'skipped: <reason>'
    'exporter_config': None,    # kwargs to rebuild an equivalent exporter
}

P = ParamSpec('P')
T = TypeVar('T')


def _redact_endpoint(endpoint: str | None) -> str | None:
    """Same endpoint with any URL password replaced by `***` (safe to log)."""
    if not endpoint or '://' not in endpoint:
        return endpoint
    try:
        parsed = urlparse(endpoint)
        if parsed.password is None:
            return endpoint
        host = parsed.hostname or ''
        if parsed.port:
            host += f':{parsed.port}'
        netloc = f'{parsed.username}:***@{host}' if parsed.username else host
        return urlunparse((parsed.scheme, netloc, parsed.path or '', '', '', ''))
    except Exception:
        return endpoint


def _split_endpoint_auth(endpoint: str) -> tuple[str, tuple | None]:
    """Split `https://user:pass@host` into (clean endpoint, auth headers).

    The OTLP gRPC exporter uses the URL netloc verbatim as the channel
    target, so embedded userinfo breaks the connection. Returns headers
    suitable for the exporter's `headers` argument, or None when the URL
    carries no credentials.
    """
    if '://' not in endpoint:
        return endpoint, None
    try:
        from urllib.parse import unquote
        parsed = urlparse(endpoint)
        if not parsed.username:
            return endpoint, None
        token = base64.b64encode(
            f'{unquote(parsed.username)}:{unquote(parsed.password or "")}'.encode()
        ).decode()
        host = parsed.hostname or ''
        if parsed.port:
            host += f':{parsed.port}'
        clean = urlunparse((parsed.scheme, host, parsed.path or '', '', '', ''))
        return clean, (('authorization', f'Basic {token}'),)
    except Exception as e:
        logger.warning(f'could not parse credentials from tracing endpoint: {e}')
        return endpoint, None


def _split_host_port(endpoint: str) -> tuple[str, str, int]:
    """Return (scheme, host, port) for TCP checks; OTLP/gRPC defaults apply."""
    parsed = urlparse(endpoint if '://' in endpoint else f'http://{endpoint}')
    scheme = parsed.scheme or 'http'
    host = parsed.hostname or 'localhost'
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if scheme == 'https' else 4317
    return scheme, host, port


def setup_tracing(service_name: str, endpoint: str | None = None,
                  environment: str | None = None,
                  sample_ratio: float | None = None,
                  enabled: bool | None = None):
    """Initialise global TracerProvider + auto-instrumentations.

    Safe to call multiple times (once per process); subsequent calls
    with the same service name are no-ops. Must be called early in each
    entrypoint (api-server, worker, telegram-bot) because each runs as a
    separate OS process under supervisord.
    """
    global _tracer, _instrumented, _shutdown_registered

    # Lazy import so `import utils.tracing` never fails when OTel is missing.
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
        from opentelemetry.propagate import set_global_textmap
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        from opentelemetry.baggage.propagation import W3CBaggagePropagator
        from opentelemetry.propagators.composite import CompositePropagator
    except ImportError:
        logger.warning('opentelemetry packages not installed, tracing disabled')
        return None

    # Resolve settings: explicit args > config.yaml > env vars > defaults.
    cfg_endpoint = None
    cfg_environment = None
    cfg_ratio = None
    cfg_enabled = None
    try:
        from context import config as app_config
        tracing_cfg = getattr(app_config, 'tracing', None)
        if tracing_cfg is not None:
            cfg_endpoint = getattr(tracing_cfg, 'endpoint', None)
            cfg_environment = getattr(tracing_cfg, 'environment', None)
            cfg_ratio = getattr(tracing_cfg, 'sample_ratio', None)
            cfg_enabled = getattr(tracing_cfg, 'enabled', None)
    except Exception:
        pass

    if enabled is None:
        enabled = cfg_enabled if cfg_enabled is not None else True
    if endpoint is None:
        endpoint = (os.getenv('OTEL_EXPORTER_OTLP_ENDPOINT')
                    or cfg_endpoint
                    or 'http://localhost:4317')
    if environment is None:
        environment = (os.getenv('OTEL_ENVIRONMENT')
                       or os.getenv('DEPLOYMENT_ENVIRONMENT')
                       or cfg_environment
                       or 'development')
    if sample_ratio is None:
        raw = os.getenv('OTEL_TRACES_SAMPLER_ARG')
        try:
            sample_ratio = float(raw) if raw is not None else cfg_ratio
        except ValueError:
            sample_ratio = cfg_ratio
        if sample_ratio is None:
            sample_ratio = 1.0

    service_name = os.getenv('OTEL_SERVICE_NAME') or service_name

    if not enabled:
        logger.info('tracing disabled via config')
        return None

    if isinstance(trace.get_tracer_provider(), TracerProvider):
        # Already initialised in this process.
        from opentelemetry import trace as _trace
        _tracer = _trace.get_tracer(service_name)
        return _tracer

    try:
        resource = Resource.create({
            'service.name': service_name,
            'service.version': os.getenv('APP_VERSION', '0.1.0'),
            'deployment.environment': environment,
        })
        sampler = ParentBased(root=TraceIdRatioBased(max(0.0, min(1.0, sample_ratio))))
        provider = TracerProvider(resource=resource, sampler=sampler)

        clean_endpoint, auth_headers = _split_endpoint_auth(endpoint)
        insecure = not clean_endpoint.startswith('https://')
        exporter = OTLPSpanExporter(endpoint=clean_endpoint, insecure=insecure,
                                    headers=auth_headers, timeout=10)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        set_global_textmap(CompositePropagator([
            TraceContextTextMapPropagator(),
            W3CBaggagePropagator(),
        ]))
    except Exception as e:
        logger.warning(f'failed to configure OTLP exporter ({_redact_endpoint(endpoint)}): {e}')
        return None

    _state.update({
        'configured': True,
        'service_name': service_name,
        'endpoint': clean_endpoint,
        'endpoint_raw': _redact_endpoint(endpoint),
        'auth': 'basic' if auth_headers else 'none',
        'environment': environment,
        'sample_ratio': sample_ratio,
        'enabled': enabled,
        'exporter_config': {
            'endpoint': clean_endpoint,
            'insecure': insecure,
            'headers': auth_headers,
        },
    })

    if not _shutdown_registered:
        # BatchSpanProcessor exports on a ~5s schedule; short-lived processes
        # (management commands) would otherwise exit with spans still queued.
        atexit.register(_shutdown_at_exit)
        _shutdown_registered = True

    # Auto-instrumentations. Each is best-effort so a missing optional
    # dep never breaks startup.
    def _try(name: str, fn: Callable[[], None]):
        try:
            fn()
            _state['instrumentation'][name] = 'ok'
            logger.info(f'tracing instrumentation enabled: {name}')
        except Exception as e:
            _state['instrumentation'][name] = f'skipped: {e}'
            logger.debug(f'tracing instrumentation skipped ({name}): {e}')

    def _logging():
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument(set_logging_format=True)

    def _httpx():
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument(
            request_hook=_httpx_sanitize_hook,
            async_request_hook=_httpx_async_sanitize_hook,
        )

    def _redis():
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        RedisInstrumentor().instrument()

    def _asyncio():
        from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
        AsyncioInstrumentor().instrument()

    def _threading():
        from opentelemetry.instrumentation.threading import ThreadingInstrumentor
        ThreadingInstrumentor().instrument()

    def _asyncclick():
        from opentelemetry.instrumentation.asyncclick import AsyncClickInstrumentor
        AsyncClickInstrumentor().instrument()

    for name, fn in [('logging', _logging),
                     ('httpx', _httpx),
                     ('redis', _redis),
                     ('asyncio', _asyncio),
                     ('threading', _threading),
                     ('asyncclick', _asyncclick)]:
        _try(name, fn)

    _instrumented = True
    _tracer = trace.get_tracer(service_name)
    logger.info(f'tracing initialised: service={service_name} '
                f'endpoint={_redact_endpoint(endpoint)} auth={_state["auth"]} env={environment}')
    return _tracer


def _shutdown_at_exit():
    # Bounded flush first so a dead exporter can't hang process exit for
    # long; shutdown() then releases the batch worker thread.
    try:
        flush_tracing(timeout_millis=5000)
    except Exception:
        pass
    try:
        shutdown_tracing()
    except Exception:
        pass


def flush_tracing(timeout_millis: int = 30000) -> bool:
    """Force-export all queued spans. Returns False on timeout/failure."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return False
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        logger.warning('flush_tracing: no SDK TracerProvider configured')
        return False
    try:
        return bool(provider.force_flush(timeout_millis))
    except Exception as e:
        logger.warning(f'flush_tracing failed: {e}')
        return False


def shutdown_tracing() -> None:
    """Shut down the global provider (flushes + stops batch thread)."""
    global _shutdown_done
    if _shutdown_done:
        return
    _shutdown_done = True
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        try:
            provider.shutdown()
        except Exception as e:
            logger.warning(f'shutdown_tracing failed: {e}')


def get_tracing_status() -> dict:
    """Snapshot of the tracing configuration (passwords redacted)."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        provider = type(trace.get_tracer_provider()).__name__
        sdk = isinstance(trace.get_tracer_provider(), TracerProvider)
    except Exception:
        provider, sdk = 'unknown', False
    status = {
        **{k: (dict(v) if isinstance(v, dict) else v) for k, v in _state.items()
            if k != 'exporter_config'},  # holds auth material - never report it
        'provider': provider,
        'sdk_provider_active': sdk,
    }
    return status


def check_endpoint_connectivity(endpoint: str | None = None, timeout: float = 5.0) -> dict:
    """TCP-dial the OTLP endpoint. Proves reachability, not OTLP validity."""
    target = endpoint or _state.get('endpoint') or 'http://localhost:4317'
    try:
        scheme, host, port = _split_host_port(target)
    except Exception as e:
        return {'checked': _redact_endpoint(target), 'ok': False, 'error': f'bad endpoint: {e}'}
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        dt = (time.monotonic() - t0) * 1000
        return {'checked': f'{scheme}://{host}:{port}', 'ok': True,
                'elapsed_ms': round(dt, 1)}
    except Exception as e:
        dt = (time.monotonic() - t0) * 1000
        return {'checked': f'{scheme}://{host}:{port}', 'ok': False,
                'elapsed_ms': round(dt, 1), 'error': f'{type(e).__name__}: {e}'}


class _CollectingExporter:
    """SpanExporter that just retains ended spans for synchronous probing."""
    def __init__(self):
        self.spans: list = []

    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=None):
        return True


def _probe_export(spans: list, timeout: float) -> tuple[bool, str]:
    """Synchronously export spans with a throwaway exporter.

    Unlike BatchSpanProcessor.force_flush (which only reports queue drain),
    this returns whether Tempo actually accepted the batch.
    """
    if not spans:
        return False, 'no spans captured'
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace.export import SpanExportResult
    except ImportError:
        return False, 'otlp exporter not installed'
    cfg = _state.get('exporter_config') or {}
    probe = None
    try:
        probe = OTLPSpanExporter(
            endpoint=cfg.get('endpoint'),
            insecure=cfg.get('insecure', True),
            headers=cfg.get('headers'),
            timeout=max(2.0, min(timeout, 30.0)),
        )
        result = probe.export(spans)
        ok = result is SpanExportResult.SUCCESS
        return ok, result.name if result is not None else 'unknown'
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    finally:
        try:
            if probe is not None:
                probe.shutdown()
        except Exception:
            pass


def send_test_span(timeout: float = 15.0) -> dict:
    """Emit one `tracing.debug-test` span and verify Tempo received it.

    Returns {'ok': True, 'trace_id': ...} only if the exporter accepted the
    batch. Look this trace_id up in Tempo/Grafana to confirm end-to-end.
    """
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    except ImportError:
        return {'ok': False, 'reason': 'opentelemetry packages not installed'}
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return {'ok': False,
                'reason': f'tracing not initialised (provider={type(provider).__name__})'}
    collector = _CollectingExporter()
    provider.add_span_processor(SimpleSpanProcessor(collector))
    tracer = trace.get_tracer('tracing.debug')
    with tracer.start_as_current_span('tracing.debug-test') as span:
        ctx = span.get_span_context()
        trace_id = f'{ctx.trace_id:032x}'
        span.set_attribute('debug', True)
        if not ctx.trace_flags.sampled:
            return {'ok': False, 'trace_id': trace_id,
                    'reason': 'span not sampled (sample_ratio too low?)'}
    t0 = time.monotonic()
    batch_ok = flush_tracing(timeout_millis=int(timeout * 1000))
    flush_ms = round((time.monotonic() - t0) * 1000, 1)
    delivered, detail = _probe_export(collector.spans, timeout)
    result: dict = {'ok': bool(delivered), 'trace_id': trace_id,
                    'batch_flush_ok': bool(batch_ok), 'flush_ms': flush_ms,
                    'delivery': detail}
    if not delivered:
        result['reason'] = (f'Tempo did not accept the batch ({detail}); '
                            'see `opentelemetry.exporter.otlp` logs for the gRPC status')
    return result


def run_tracing_diagnostics(timeout: float = 15.0) -> dict:
    """Combined report: config + sanity warnings + TCP check + test span."""
    status = get_tracing_status()
    warnings: list[str] = []
    raw = status.get('endpoint_raw') or ''
    effective = status.get('endpoint') or ''

    if status.get('auth') == 'basic':
        warnings.append(f'endpoint embeds userinfo; sent as Basic auth header to {effective}')
    try:
        scheme, host, port = _split_host_port(effective or raw)
        if ('://' in (effective or raw) and ':' not in (effective or raw).split('://', 1)[1].split('/')[0].split('@')[-1]):
            warnings.append(f'no explicit port in endpoint; using default {port} for scheme {scheme}')
        if scheme == 'https':
            warnings.append('scheme is https (TLS); if Tempo serves h2c cleartext, use http:// instead')
    except Exception:
        warnings.append(f'endpoint is not a parseable URL: {raw}')

    tcp = check_endpoint_connectivity(effective or None, timeout=min(timeout, 5.0))
    test = (send_test_span(timeout=timeout)
            if status.get('configured') else {'ok': False, 'reason': 'tracing not configured'})
    return {
        'status': status,
        'warnings': warnings,
        'tcp': tcp,
        'test_span': test,
        'ok': bool(tcp.get('ok') and test.get('ok')),
    }


def get_tracer(name: str = 'e621-bot'):
    """Return a tracer, or a no-op tracer if tracing is not configured."""
    try:
        from opentelemetry import trace
        return trace.get_tracer(name)
    except Exception:
        return _NoopTracer()


class _NoopSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def set_attribute(self, *args, **kwargs):
        pass

    def set_status(self, *args, **kwargs):
        pass

    def record_exception(self, *args, **kwargs):
        pass

    def add_event(self, *args, **kwargs):
        pass


class _NoopTracer:
    def start_as_current_span(self, *args, **kwargs):
        return _NoopSpan()

    def start_span(self, *args, **kwargs):
        return _NoopSpan()


def set_span_attributes(attributes: dict) -> None:
    """Set attributes on the current span. Safe no-op when untraced."""
    if not attributes:
        return
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span is None or not span.is_recording():
            return
        for k, v in attributes.items():
            if v is None:
                continue
            span.set_attribute(k, v)
    except Exception:
        pass


def record_span_error(span, exc: BaseException) -> None:
    """Record an exception on a span and mark it ERROR. Never raises."""
    try:
        from opentelemetry.trace import Status, StatusCode
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, str(exc)))
    except Exception:
        pass


def traced(span_name: str | None = None, attributes: dict | None = None):
    """Decorator adding an OTel span around sync/async functions.

    Every span automatically gets `code.function` / `code.namespace`.
    Records exceptions and sets ERROR status automatically. When tracing
    is disabled this is a transparent pass-through.
    """
    def decorator(fn: Callable[P, T]) -> Callable[P, T]:
        name = span_name or f'{fn.__module__}.{fn.__qualname__}'
        code_attrs = {'code.function': fn.__qualname__, 'code.namespace': fn.__module__}

        if _is_coro(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs):
                tracer = get_tracer(fn.__module__)
                with tracer.start_as_current_span(name) as span:
                    try:
                        for k, v in code_attrs.items():
                            span.set_attribute(k, v)
                        if attributes:
                            for k, v in attributes.items():
                                span.set_attribute(k, v)
                        return await fn(*args, **kwargs)
                    except Exception as e:
                        try:
                            from opentelemetry.trace import Status, StatusCode
                            span.record_exception(e)
                            span.set_status(Status(StatusCode.ERROR, str(e)))
                        except Exception:
                            pass
                        raise
            return async_wrapper  # type: ignore[return-value]
        else:
            @functools.wraps(fn)
            def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
                tracer = get_tracer(fn.__module__)
                with tracer.start_as_current_span(name) as span:
                    try:
                        for k, v in code_attrs.items():
                            span.set_attribute(k, v)
                        if attributes:
                            for k, v in attributes.items():
                                span.set_attribute(k, v)
                        return fn(*args, **kwargs)
                    except Exception as e:
                        try:
                            from opentelemetry.trace import Status, StatusCode
                            span.record_exception(e)
                            span.set_status(Status(StatusCode.ERROR, str(e)))
                        except Exception:
                            pass
                        raise
            return sync_wrapper  # type: ignore[return-value]
    return decorator


_SENSITIVE_QUERY_KEYS = frozenset({'api_key', 'user_id', 'password', 'token', 'secret'})


def sanitize_url(url: str) -> str:
    """Redact credential-bearing query params (api_key, user_id, ...)."""
    try:
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
        parts = urlsplit(url)
        if not parts.query:
            return url
        query = [(k, '***' if k.lower() in _SENSITIVE_QUERY_KEYS else v)
                 for k, v in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path,
                           urlencode(query), parts.fragment))
    except Exception:
        return url


def _httpx_sanitize_hook(span, request) -> None:
    """request_hook: overwrite URL attributes with the sanitized URL."""
    try:
        clean = sanitize_url(str(request.url))
        span.set_attribute('url.full', clean)
        span.set_attribute('http.url', clean)
    except Exception:
        pass


async def _httpx_async_sanitize_hook(span, request) -> None:
    _httpx_sanitize_hook(span, request)


def _is_coro(fn: Callable) -> bool:
    import inspect
    return inspect.iscoroutinefunction(fn) or inspect.isasyncgenfunction(fn)


def instrument_sanic(app, service_name: str = 'e621-bot-api'):
    """Add manual SERVER spans for Sanic (no official instrumentation).

    Extracts W3C traceparent from incoming headers so calls from other
    services continue the same trace, and records http.* attributes plus
    error status on 5xx / exceptions.
    """
    try:
        from opentelemetry import propagate, trace
        from opentelemetry.trace import SpanKind, Status, StatusCode
    except ImportError:
        return

    tracer = trace.get_tracer(service_name)

    @app.middleware('request')
    async def _otel_start(request):
        ctx = propagate.extract(dict(request.headers))
        route = getattr(request, 'route', None)
        route_tpl = getattr(route, 'path', None) or request.path
        span = tracer.start_span(
            f'{request.method} {route_tpl}',
            context=ctx,
            kind=SpanKind.SERVER,
        )
        span.set_attribute('http.method', request.method)
        span.set_attribute('http.target', request.path)
        span.set_attribute('http.route', str(route_tpl))
        span.set_attribute('http.scheme', request.scheme)
        span.set_attribute('http.host', request.host)
        span.set_attribute('http.user_agent', request.headers.get('user-agent', ''))
        request.ctx.otel_span = span
        request.ctx.otel_scope = trace.use_span(span, end_on_exit=False)
        request.ctx.otel_scope.__enter__()

    @app.middleware('response')
    async def _otel_end(request, response):
        span = getattr(request.ctx, 'otel_span', None)
        scope = getattr(request.ctx, 'otel_scope', None)
        try:
            if span is not None:
                status = getattr(response, 'status', 200)
                span.set_attribute('http.status_code', status)
                if status >= 500:
                    span.set_status(Status(StatusCode.ERROR, f'HTTP {status}'))
        finally:
            try:
                if scope is not None:
                    scope.__exit__(None, None, None)
            finally:
                if span is not None:
                    span.end()

    @app.exception(Exception)
    async def _otel_error(request, exception):
        span = getattr(request.ctx, 'otel_span', None)
        if span is not None:
            try:
                span.record_exception(exception)
                span.set_status(Status(StatusCode.ERROR, str(exception)))
            except Exception:
                pass
        raise exception
