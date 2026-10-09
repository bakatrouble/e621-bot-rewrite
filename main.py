#!/usr/bin/env python3

import asyncio

import asyncclick as click

import api
from bot import TelegramBot
from context import AppContext
from context.query import Query
from utils.cache import cache_cleaner
from utils.tracing import set_span_attributes, setup_tracing, traced


@click.group()
async def cli():
    ctx = AppContext()
    await ctx.storage.migrate()


@cli.command('telegram-bot')
async def start_telegram_bot():
    ctx = AppContext()
    setup_tracing('e621-bot-telegram', ctx)
    await TelegramBot(ctx).start()


@cli.command('worker')
async def start_worker():
    ctx = AppContext()
    setup_tracing('e621-bot-worker', ctx)
    async with asyncio.TaskGroup() as tg:
        tg.create_task(ctx.e621.worker(ctx))
        if ctx.config.gelbooru:
            tg.create_task(ctx.gelbooru.worker(ctx))
        tg.create_task(cache_cleaner(ctx))


@cli.command('api-server')
async def start_api_server():
    ctx = AppContext()
    setup_tracing('e621-bot-api', ctx)
    await api.start(ctx)


@cli.command('debug-tracing')
@click.option(
    '--timeout',
    default=15.0,
    type=float,
    show_default=True,
    help='Per-check timeout in seconds.',
)
@click.option(
    '--endpoint',
    default=None,
    help='Override the OTLP endpoint for this check '
    '(default: config file / OTEL_EXPORTER_OTLP_ENDPOINT).',
)
@click.option(
    '--verbose',
    is_flag=True,
    default=False,
    help='Enable OpenTelemetry SDK debug logging.',
)
async def debug_tracing(timeout: float, endpoint: str | None, verbose: bool):
    """Check tracing config, TCP reachability and test-span delivery."""
    import logging

    from utils.tracing import run_tracing_diagnostics

    if verbose:
        logging.getLogger('opentelemetry').setLevel(logging.DEBUG)
    ctx = AppContext()
    setup_tracing('e621-bot-cli', ctx, endpoint=endpoint)
    report = run_tracing_diagnostics(timeout=timeout)

    status = report['status']
    click.echo(f'service:     {status["service_name"]}')
    click.echo(f'endpoint:    {status["endpoint_raw"]}')
    click.echo(f'  effective: {status["endpoint"]} (auth={status["auth"]})')
    click.echo(
        f'environment: {status["environment"]}  sample_ratio={status["sample_ratio"]}  '
        f'provider={status["provider"]}'
    )
    click.echo(
        'instrumentation: '
        + ', '.join(f'{k}={v}' for k, v in status['instrumentation'].items())
        or 'none'
    )
    for w in report['warnings']:
        click.echo(f'warning:     {w}')

    tcp = report['tcp']
    if tcp['ok']:
        click.echo(f'tcp:         OK {tcp["checked"]} ({tcp["elapsed_ms"]} ms)')
    else:
        click.echo(f'tcp:         FAIL {tcp["checked"]}: {tcp.get("error")}')

    test = report['test_span']
    if test.get('ok'):
        click.echo(
            f'test span:   OK trace_id={test["trace_id"]} '
            f'(Tempo accepted batch, flush {test["flush_ms"]} ms) - '
            f'look it up in Tempo/Grafana'
        )
    else:
        click.echo(f'test span:   FAIL {test.get("reason")}')
        click.echo(
            f'             batch_flush_ok={test.get("batch_flush_ok")} '
            f'delivery={test.get("delivery")}'
        )
        if test.get('trace_id'):
            click.echo(f'             trace_id={test["trace_id"]}')

    if report['ok']:
        click.echo('verdict:     traces are being delivered')
    else:
        click.echo(
            'verdict:     traces are NOT reaching Tempo - '
            'check the tcp/test-span lines above and the exporter logs '
            '(re-run with --verbose)'
        )
        if (
            report['tcp'].get('ok')
            and not report['test_span'].get('ok')
            and (status['endpoint'] or '').startswith('https://')
        ):
            click.echo(
                'hint:        TCP connects but gRPC export fails over https - '
                'if Tempo serves h2c cleartext, use an http:// endpoint'
            )
        raise SystemExit(1)


@cli.group('e621')
def e621_group():
    ctx = AppContext()
    # NB: setup lives here and not in the top-level `cli` group on purpose.
    # The tracer provider (and thus service.name) is process-global and first
    # call wins, so initialising it in `cli()` would mislabel worker/api/bot
    # spans. Management commands are short-lived processes of their own, and
    # this callback runs before any `e621` subcommand, which is also what arms
    # AsyncClickInstrumentor in time to wrap the subcommand invocation.
    setup_tracing('e621-bot-cli', ctx)


@e621_group.command()
@click.argument('subs', nargs=-1, type=str)
@traced('cli.e621.add')
async def add(subs: list[str]):
    ctx = AppContext()
    set_span_attributes({'cli.subs.requested': len(subs)})
    existing_subs = set(await ctx.storage.e621.get_subs())
    added = 0
    for s in subs:
        s = s.lower()
        if s in existing_subs:
            click.echo(f'`{s}` already exists')
            continue
        await ctx.storage.e621.add_sub(s)
        added += 1
        click.echo(f'`{s}` added')
    set_span_attributes({'cli.subs.added': added})


@e621_group.command('del')
@click.argument('subs', nargs=-1, type=str)
@traced('cli.e621.del')
async def delete(subs: list[str]):
    ctx = AppContext()
    set_span_attributes({'cli.subs.requested': len(subs)})
    existing_subs = set(await ctx.storage.e621.get_subs())
    deleted = 0
    for s in subs:
        s = s.lower()
        if s not in existing_subs:
            click.echo(f'`{s}` not found')
            continue
        await ctx.storage.e621.remove_sub(s)
        deleted += 1
        click.echo(f'`{s}` deleted')
    set_span_attributes({'cli.subs.deleted': deleted})


@e621_group.command('test')
@click.argument('post_id', type=str)
@traced('cli.e621.test')
async def e621_test(post_id: int):
    ctx = AppContext()
    set_span_attributes({'cli.post.id': post_id})
    post = await ctx.e621.get_post(post_id)
    await post.send_post(ctx)


@e621_group.command('test-pv')
@click.argument('post_id', type=str)
@traced('cli.e621.test-pv')
async def e621_test_pv(post_id: str):
    ctx = AppContext()
    set_span_attributes({'cli.post.id': post_id})
    pv = await ctx.e621.get_post_versions(after_id=int(post_id) - 1, limit=1)
    pv = pv[0]
    if matched_queries := pv.check_queries(
        Query.get_queries(await ctx.storage.e621.get_subs())
    ):
        set_span_attributes({'cli.matched_queries.count': len(matched_queries)})
        post = await ctx.e621.get_post(pv.post_id)
        await post.send_post(ctx, matched_queries)
    else:
        set_span_attributes({'cli.matched_queries.count': 0})
        click.echo('No matched queries')


if __name__ == '__main__':
    try:
        cli()
    except KeyboardInterrupt:
        pass
