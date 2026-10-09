"""e621 worker workflow: fetch -> match -> dedupe -> send -> cursor advance."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from context.query import Query
from tests.helpers import (
    _no_sleep,
    await_args,
    make_ctx,
    make_e621_storage,
    make_pv,
)
from websites.e621 import E621, E621MatchedPV


def _workflow_ctx(storage_stub):
    return make_ctx(storage=SimpleNamespace(e621=storage_stub))


@pytest.mark.asyncio
async def test_e621_new_matching_post_is_sent(monkeypatch):
    storage_stub, lock, sent_set, cursor_log = make_e621_storage()
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = E621()
    pv = make_pv(101, 5001)
    monkeypatch.setattr(client, 'get_post_versions', AsyncMock(return_value=[pv]))
    sent_plans = []

    async def _send(self, ctx):
        sent_plans.append(self)

    monkeypatch.setattr(E621MatchedPV, 'send_post', _send)

    await client.process_new_posts(ctx)

    assert lock.acquired == 1
    assert len(sent_plans) == 1
    assert isinstance(sent_plans[0], E621MatchedPV)
    assert [str(q) for q in sent_plans[0].matched_queries] == ['wolf']
    assert sent_set == {5001}
    assert cursor_log[-1] == 101


@pytest.mark.asyncio
async def test_e621_no_new_versions_advances_nothing(monkeypatch):
    storage_stub, _, sent_set, cursor_log = make_e621_storage(last_version=200)
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = E621()
    monkeypatch.setattr(client, 'get_post_versions', AsyncMock(return_value=[]))
    send = AsyncMock()
    monkeypatch.setattr(E621MatchedPV, 'send_post', send)

    await client.process_new_posts(ctx)

    send.assert_not_awaited()
    assert sent_set == set()
    assert cursor_log == [200]


@pytest.mark.asyncio
async def test_e621_non_matching_version_skips_send(monkeypatch):
    storage_stub, _, sent_set, cursor_log = make_e621_storage()
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = E621()
    pv = make_pv(101, 5001, tags='deer')
    monkeypatch.setattr(client, 'get_post_versions', AsyncMock(return_value=[pv]))
    send = AsyncMock()
    monkeypatch.setattr(E621MatchedPV, 'send_post', send)

    await client.process_new_posts(ctx)

    send.assert_not_awaited()
    assert sent_set == set()
    assert cursor_log == [101]


@pytest.mark.asyncio
async def test_e621_already_sent_post_filtered(monkeypatch):
    storage_stub, _, sent_set, cursor_log = make_e621_storage(sent={5001})
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = E621()
    pv = make_pv(101, 5001)
    monkeypatch.setattr(client, 'get_post_versions', AsyncMock(return_value=[pv]))
    send = AsyncMock()
    monkeypatch.setattr(E621MatchedPV, 'send_post', send)

    await client.process_new_posts(ctx)

    send.assert_not_awaited()
    assert sent_set == {5001}
    assert cursor_log == [101]


@pytest.mark.asyncio
async def test_e621_full_page_paginates(monkeypatch):
    storage_stub, _, sent_set, cursor_log = make_e621_storage(last_version=100)
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = E621()
    page1 = [make_pv(101 + i, 5000 + i) for i in range(320)]
    page2 = [make_pv(421, 6000)]
    fetch = AsyncMock(side_effect=[page1, page2])
    monkeypatch.setattr(client, 'get_post_versions', fetch)
    sent_count = []

    async def _send(self, ctx):
        sent_count.append(self)

    monkeypatch.setattr(E621MatchedPV, 'send_post', _send)

    await client.process_new_posts(ctx)

    assert fetch.await_count == 2
    assert len(sent_count) == 321
    assert len(sent_set) == 321
    assert cursor_log[-1] == 421


@pytest.mark.asyncio
async def test_e621_send_error_does_not_stop_batch(monkeypatch):
    storage_stub, _, sent_set, cursor_log = make_e621_storage()
    ctx = _workflow_ctx(storage_stub)
    _no_sleep(monkeypatch)
    client = E621()
    pvs = [make_pv(101, 5001), make_pv(102, 5002)]
    monkeypatch.setattr(client, 'get_post_versions', AsyncMock(return_value=pvs))
    attempts = []

    async def _send(self, ctx):
        attempts.append(self)
        if len(attempts) == 1:
            raise RuntimeError('boom')

    monkeypatch.setattr(E621MatchedPV, 'send_post', _send)

    await client.process_new_posts(ctx)  # must not raise

    assert len(attempts) == 2
    assert sent_set == {5002}  # failed post is retried next tick
    assert cursor_log == [102]


@pytest.mark.asyncio
async def test_e621_matched_pv_delegates_to_post(monkeypatch):
    post = AsyncMock()
    get_post = AsyncMock(return_value=post)
    ctx = make_ctx(e621=SimpleNamespace(get_post=get_post))
    plan = E621MatchedPV([Query('wolf')], make_pv(101, 5001))

    await plan.send_post(ctx)

    get_post.assert_awaited_once_with(5001)
    assert post.send_post.await_count == 1
    sent_ctx, sent_queries = await_args(post.send_post)
    assert sent_ctx is ctx
    assert [str(q) for q in sent_queries] == ['wolf']
