#!/usr/bin/env python3

import asyncio

import asyncclick as click

import api
from bot import telegram_bot
from context import storage, config
from context.query import Query
from utils.cache import cache_cleaner
from utils.tracing import setup_tracing, traced
from websites import e621, gelbooru


@click.group()
async def cli():
    # setup_otel()
    await storage.migrate()


@cli.command('telegram-bot')
async def start_telegram_bot():
    setup_tracing('e621-bot-telegram')
    await telegram_bot.start()


@cli.command('worker')
async def start_worker():
    setup_tracing('e621-bot-worker')
    async with asyncio.TaskGroup() as tg:
        tg.create_task(e621.worker())
        if config.gelbooru:
            tg.create_task(gelbooru.worker())
        tg.create_task(cache_cleaner())


@cli.command('api-server')
async def start_api_server():
    setup_tracing('e621-bot-api')
    await api.start()


@cli.command('debug-tracing')
@click.option('--timeout', default=15.0, type=float, show_default=True,
              help='Per-check timeout in seconds.')
@click.option('--endpoint', default=None,
              help='Override the OTLP endpoint for this check '
                   '(default: config file / OTEL_EXPORTER_OTLP_ENDPOINT).')
@click.option('--verbose', is_flag=True, default=False,
              help='Enable OpenTelemetry SDK debug logging.')
async def debug_tracing(timeout: float, endpoint: str | None, verbose: bool):
    """Check tracing config, TCP reachability and test-span delivery."""
    import logging

    from utils.tracing import run_tracing_diagnostics

    if verbose:
        logging.getLogger('opentelemetry').setLevel(logging.DEBUG)
    setup_tracing('e621-bot-cli', endpoint=endpoint)
    report = run_tracing_diagnostics(timeout=timeout)

    status = report['status']
    click.echo(f"service:     {status['service_name']}")
    click.echo(f"endpoint:    {status['endpoint_raw']}")
    click.echo(f"  effective: {status['endpoint']} (auth={status['auth']})")
    click.echo(f"environment: {status['environment']}  sample_ratio={status['sample_ratio']}  "
               f"provider={status['provider']}")
    click.echo('instrumentation: ' + ', '.join(
        f'{k}={v}' for k, v in status['instrumentation'].items()) or 'none')
    for w in report['warnings']:
        click.echo(f'warning:     {w}')

    tcp = report['tcp']
    if tcp['ok']:
        click.echo(f"tcp:         OK {tcp['checked']} ({tcp['elapsed_ms']} ms)")
    else:
        click.echo(f"tcp:         FAIL {tcp['checked']}: {tcp.get('error')}")

    test = report['test_span']
    if test.get('ok'):
        click.echo(f"test span:   OK trace_id={test['trace_id']} "
                   f"(Tempo accepted batch, flush {test['flush_ms']} ms) - "
                   f'look it up in Tempo/Grafana')
    else:
        click.echo(f"test span:   FAIL {test.get('reason')}")
        click.echo(f"             batch_flush_ok={test.get('batch_flush_ok')} "
                   f"delivery={test.get('delivery')}")
        if test.get('trace_id'):
            click.echo(f"             trace_id={test['trace_id']}")

    if report['ok']:
        click.echo('verdict:     traces are being delivered')
    else:
        click.echo('verdict:     traces are NOT reaching Tempo - '
                   'check the tcp/test-span lines above and the exporter logs '
                   '(re-run with --verbose)')
        if (report['tcp'].get('ok') and not report['test_span'].get('ok')
                and (status['endpoint'] or '').startswith('https://')):
            click.echo('hint:        TCP connects but gRPC export fails over https - '
                       'if Tempo serves h2c cleartext, use an http:// endpoint')
        raise SystemExit(1)


@cli.group('e621')
def e621_group():
    # NB: setup lives here and not in the top-level `cli` group on purpose.
    # The tracer provider (and thus service.name) is process-global and first
    # call wins, so initialising it in `cli()` would mislabel worker/api/bot
    # spans. Management commands are short-lived processes of their own, and
    # this callback runs before any `e621` subcommand, which is also what arms
    # AsyncClickInstrumentor in time to wrap the subcommand invocation.
    setup_tracing('e621-bot-cli')


@e621_group.command()
@click.argument('subs', nargs=-1, type=str)
@traced('cli.e621.add')
async def add(subs: list[str]):
    existing_subs = set(await storage.get_subs())
    for s in subs:
        s = s.lower()
        if s in existing_subs:
            click.echo(f'`{s}` already exists')
            continue
        await storage.add_sub(s)
        click.echo(f'`{s}` added')


@e621_group.command('del')
@click.argument('subs', nargs=-1, type=str)
@traced('cli.e621.del')
async def delete(subs: list[str]):
    existing_subs = set(await storage.get_subs())
    for s in subs:
        s = s.lower()
        if s not in existing_subs:
            click.echo(f'`{s}` not found')
            continue
        await storage.remove_sub(s)
        click.echo(f'`{s}` deleted')


@e621_group.command('test')
@click.argument('post_id', type=str)
@traced('cli.e621.test')
async def e621_test(post_id: str):
    post = await e621.get_post(post_id)
    await post.send_post()


@e621_group.command('test-pv')
@click.argument('post_id', type=str)
@traced('cli.e621.test-pv')
async def e621_test_pv(post_id: str):
    pv = await e621.get_post_versions(after_id=int(post_id) - 1, limit=1)
    pv = pv[0]
    if matched_queries := pv.check_queries(Query.get_queries(await storage.e621.get_subs())):
        post = await e621.get_post(pv.post_id)
        await post.send_post(matched_queries)
    else:
        click.echo('No matched queries')


if __name__ == '__main__':
    try:
        cli()
    except KeyboardInterrupt:
        pass
