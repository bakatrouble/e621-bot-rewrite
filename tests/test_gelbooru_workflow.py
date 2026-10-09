"""Gelbooru worker workflow: backfill -> chunked fetch -> dedupe -> send."""

from unittest.mock import AsyncMock

import pytest

from tests.helpers import (
    _no_sleep,
    make_gelbooru_post,
    stub_gelbooru_storage,
)
from websites.gelbooru import Gelbooru, GelbooruPost


@pytest.mark.asyncio
async def test_gelbooru_new_sub_backfills_and_marks_sent(monkeypatch):
    from websites import gelbooru as gelbooru_module

    _no_sleep(monkeypatch)
    lock, sent_set, scanned_set = stub_gelbooru_storage(monkeypatch, scanned=False)
    old = [make_gelbooru_post(101), make_gelbooru_post(102)]

    async def _get_posts(tags="", page=0, limit=100):
        if tags == "wolf":  # backfill query for the unscanned sub
            return old
        return []  # fetch phase: nothing new

    monkeypatch.setattr(Gelbooru, "get_posts", AsyncMock(side_effect=_get_posts))
    send = AsyncMock()
    monkeypatch.setattr(GelbooruPost, "send_post", send)

    await gelbooru_module.process_new_posts()

    assert lock.acquired == 1
    assert sent_set == {101, 102}
    assert scanned_set == {"wolf"}
    send.assert_not_awaited()  # backfilled posts are marked, never sent


@pytest.mark.asyncio
async def test_gelbooru_nothing_new_sends_nothing(monkeypatch):
    from websites import gelbooru as gelbooru_module

    _no_sleep(monkeypatch)
    stub_gelbooru_storage(monkeypatch, scanned=True)
    monkeypatch.setattr(Gelbooru, "get_posts", AsyncMock(return_value=[]))
    send = AsyncMock()
    monkeypatch.setattr(GelbooruPost, "send_post", send)

    await gelbooru_module.process_new_posts()

    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_gelbooru_new_post_is_sent(monkeypatch):
    from websites import gelbooru as gelbooru_module

    _no_sleep(monkeypatch)
    _, sent_set, _ = stub_gelbooru_storage(monkeypatch, scanned=True)
    fresh = make_gelbooru_post(201, tags="wolf")

    async def _get_posts(tags="", page=0, limit=100):
        return [fresh] if page == 0 else []

    monkeypatch.setattr(Gelbooru, "get_posts", AsyncMock(side_effect=_get_posts))
    send = AsyncMock()
    monkeypatch.setattr(GelbooruPost, "send_post", send)

    await gelbooru_module.process_new_posts()

    assert send.await_count == 1
    assert sent_set == {201}
    assert fresh.subs == ["wolf"]
