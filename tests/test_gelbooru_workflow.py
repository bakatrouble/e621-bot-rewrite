"""Gelbooru worker workflow: backfill -> chunked fetch -> dedupe -> send."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.helpers import (
    _no_sleep,
    make_config,
    make_ctx,
    make_gelbooru_post,
    make_gelbooru_storage,
)
from websites.gelbooru import Gelbooru, GelbooruPost


def _workflow_ctx(storage_stub, *, gelbooru=True):
    return make_ctx(
        config=make_config(gelbooru=gelbooru),
        storage=SimpleNamespace(gelbooru=storage_stub),
    )


@pytest.mark.asyncio
async def test_gelbooru_new_sub_backfills_and_marks_sent(monkeypatch):
    storage_stub, lock, sent_set, scanned_set = make_gelbooru_storage(scanned=False)
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = Gelbooru()
    old = [make_gelbooru_post(101), make_gelbooru_post(102)]

    async def _get_posts(tags='', page=0, limit=100, user_id='', api_key=''):
        if tags == 'wolf':  # backfill query for the unscanned sub
            return old
        return []  # fetch phase: nothing new

    monkeypatch.setattr(client, 'get_posts', AsyncMock(side_effect=_get_posts))
    send = AsyncMock()
    monkeypatch.setattr(GelbooruPost, 'send_post', send)

    await client.process_new_posts(ctx)

    assert lock.acquired == 1
    assert sent_set == {101, 102}
    assert scanned_set == {'wolf'}
    send.assert_not_awaited()  # backfilled posts are marked, never sent


@pytest.mark.asyncio
async def test_gelbooru_nothing_new_sends_nothing(monkeypatch):
    storage_stub, _, _, _ = make_gelbooru_storage(scanned=True)
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = Gelbooru()
    monkeypatch.setattr(client, 'get_posts', AsyncMock(return_value=[]))
    send = AsyncMock()
    monkeypatch.setattr(GelbooruPost, 'send_post', send)

    await client.process_new_posts(ctx)

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_gelbooru_new_post_is_sent(monkeypatch):
    storage_stub, _, sent_set, _ = make_gelbooru_storage(scanned=True)
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = Gelbooru()
    fresh = make_gelbooru_post(201, tags='wolf')

    async def _get_posts(tags='', page=0, limit=100, user_id='', api_key=''):
        return [fresh] if page == 0 else []

    monkeypatch.setattr(client, 'get_posts', AsyncMock(side_effect=_get_posts))
    send = AsyncMock()
    monkeypatch.setattr(GelbooruPost, 'send_post', send)

    await client.process_new_posts(ctx)

    assert send.await_count == 1
    assert sent_set == {201}
    assert fresh.subs == ['wolf']


@pytest.mark.asyncio
async def test_gelbooru_unconfigured_raises(monkeypatch):
    storage_stub, _, _, _ = make_gelbooru_storage(scanned=True)
    ctx = _workflow_ctx(storage_stub, gelbooru=False)
    _no_sleep(monkeypatch)
    client = Gelbooru()

    with pytest.raises(RuntimeError, match='not configured'):
        await client.process_new_posts(ctx)
