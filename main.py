#!/usr/bin/env python3

import asyncio

import asyncclick as click

import api
from bot import telegram_bot
from context import storage, config
from context.query import Query
from utils.cache import cache_cleaner
from websites import e621, gelbooru


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
        if config.gelbooru:
            tg.create_task(gelbooru.worker())
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
