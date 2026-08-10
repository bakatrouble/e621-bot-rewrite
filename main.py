#!/usr/bin/env python3

import asyncio

import asyncclick as click
# from opentelemetry.instrumentation.asyncio import AsyncioInstrumentor
# from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
# from opentelemetry.instrumentation.logging import LoggingInstrumentor
# from opentelemetry.instrumentation.redis import RedisInstrumentor
# from opentelemetry.sdk.metrics import MeterProvider

import api
from bot import telegram_bot
from context import storage, config
from context.query import Query
from utils.cache import cache_cleaner
from websites import e621


# def setup_otel():
#     from opentelemetry import trace, _logs as logs, metrics
#     from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
#     from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
#     from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
#     from opentelemetry.sdk._logs import LoggerProvider
#     from opentelemetry.sdk._logs._internal.export import BatchLogRecordProcessor
#     from opentelemetry.sdk.resources import Resource
#     from opentelemetry.sdk.trace import TracerProvider
#     from opentelemetry.sdk.trace.export import BatchSpanProcessor
#     from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
#
#     # --traces_exporter otlp --metrics_exporter otlp --logs_exporter otlp --service_name subscriber
#     resource = Resource.create({
#         'service.name': 'subscriber',
#     })
#     trace.set_tracer_provider(TracerProvider(resource=resource))
#     logs.set_logger_provider(LoggerProvider(resource=resource))
#
#     reader = PeriodicExportingMetricReader(OTLPMetricExporter())
#     metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))
#
#     otlp_span_exporter = OTLPSpanExporter()
#     trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(otlp_span_exporter))
#
#     otlp_log_exporter = OTLPLogExporter()
#     logs.get_logger_provider().add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
#
#     HTTPXClientInstrumentor().instrument()
#     AsyncioInstrumentor().instrument()
#     LoggingInstrumentor().instrument()


@click.group()
async def cli():
    # setup_otel()
    await storage.migrate()


@cli.command('telegram-bot')
async def start_telegram_bot():
    await telegram_bot.start()


@cli.command('worker')
async def start_worker():
    async with asyncio.TaskGroup() as tg:
        tg.create_task(e621.worker())
        tg.create_task(cache_cleaner())


@cli.command('api-server')
async def start_api_server():
    await api.start()


@cli.group('e621')
def e621_group():
    pass


@e621_group.command()
@click.argument('subs', nargs=-1, type=str)
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
async def e621_test(post_id: str):
    post = await e621.get_post(post_id)
    await post.send_post()


@e621_group.command('test-pv')
@click.argument('post_id', type=str)
async def e621_test_pv(post_id: str):
    pv = await e621.get_post_versions(after_id=int(post_id) - 1, limit=1)
    pv = pv[0]
    if matched_queries := pv.check_queries(await Query.get_queries()):
        post = await e621.get_post(pv.post_id)
        await post.send_post(matched_queries)
    else:
        click.echo('No matched queries')


if __name__ == '__main__':
    try:
        cli()
    except KeyboardInterrupt:
        pass
