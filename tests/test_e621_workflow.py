"""e621 worker workflow: fetch -> match -> dedupe -> send -> cursor advance."""

from unittest.mock import AsyncMock

import pytest

from context.query import Query
from tests.helpers import _no_sleep, make_pv, stub_e621_storage
from websites.e621 import E621, E621MatchedPV


@pytest.mark.asyncio
async def test_e621_new_matching_post_is_sent(monkeypatch):
    from websites import e621 as e621_module

    _no_sleep(monkeypatch)
    lock, sent_set, cursor_log = stub_e621_storage(monkeypatch)
    pv = make_pv(101, 5001)
    monkeypatch.setattr(E621, "get_post_versions", AsyncMock(return_value=[pv]))
    sent_plans = []

    async def _send(self):
        sent_plans.append(self)

    monkeypatch.setattr(E621MatchedPV, "send_post", _send)

    await e621_module.process_new_posts()

    assert lock.acquired == 1
    assert len(sent_plans) == 1
    assert isinstance(sent_plans[0], E621MatchedPV)
    assert [str(q) for q in sent_plans[0].matched_queries] == ["wolf"]
    assert sent_set == {5001}
    assert cursor_log[-1] == 101


@pytest.mark.asyncio
async def test_e621_no_new_versions_advances_nothing(monkeypatch):
    from websites import e621 as e621_module

    _no_sleep(monkeypatch)
    _, sent_set, cursor_log = stub_e621_storage(monkeypatch, last_version=200)
    monkeypatch.setattr(E621, "get_post_versions", AsyncMock(return_value=[]))
    send = AsyncMock()
    monkeypatch.setattr(E621MatchedPV, "send_post", send)

    await e621_module.process_new_posts()

    send.assert_not_awaited()
    assert sent_set == set()
    assert cursor_log == [200]


@pytest.mark.asyncio
async def test_e621_non_matching_version_skips_send(monkeypatch):
    from websites import e621 as e621_module

    _no_sleep(monkeypatch)
    _, sent_set, cursor_log = stub_e621_storage(monkeypatch)
    pv = make_pv(101, 5001, tags="deer")
    monkeypatch.setattr(E621, "get_post_versions", AsyncMock(return_value=[pv]))
    send = AsyncMock()
    monkeypatch.setattr(E621MatchedPV, "send_post", send)

    await e621_module.process_new_posts()

    send.assert_not_awaited()
    assert sent_set == set()
    assert cursor_log == [101]


@pytest.mark.asyncio
async def test_e621_already_sent_post_filtered(monkeypatch):
    from websites import e621 as e621_module

    _no_sleep(monkeypatch)
    _, sent_set, cursor_log = stub_e621_storage(monkeypatch, sent={5001})
    pv = make_pv(101, 5001)
    monkeypatch.setattr(E621, "get_post_versions", AsyncMock(return_value=[pv]))
    send = AsyncMock()
    monkeypatch.setattr(E621MatchedPV, "send_post", send)

    await e621_module.process_new_posts()

    send.assert_not_awaited()
    assert sent_set == {5001}
    assert cursor_log == [101]


@pytest.mark.asyncio
async def test_e621_full_page_paginates(monkeypatch):
    from websites import e621 as e621_module

    _no_sleep(monkeypatch)
    _, sent_set, cursor_log = stub_e621_storage(monkeypatch, last_version=100)
    page1 = [make_pv(101 + i, 5000 + i) for i in range(320)]
    page2 = [make_pv(421, 6000)]
    fetch = AsyncMock(side_effect=[page1, page2])
    monkeypatch.setattr(E621, "get_post_versions", fetch)
    sent_count = []

    async def _send(self):
        sent_count.append(self)

    monkeypatch.setattr(E621MatchedPV, "send_post", _send)

    await e621_module.process_new_posts()

    assert fetch.await_count == 2
    assert len(sent_count) == 321
    assert len(sent_set) == 321
    assert cursor_log[-1] == 421


@pytest.mark.asyncio
async def test_e621_send_error_does_not_stop_batch(monkeypatch):
    from websites import e621 as e621_module

    _no_sleep(monkeypatch)
    _, sent_set, cursor_log = stub_e621_storage(monkeypatch)
    pvs = [make_pv(101, 5001), make_pv(102, 5002)]
    monkeypatch.setattr(E621, "get_post_versions", AsyncMock(return_value=pvs))
    attempts = []

    async def _send(self):
        attempts.append(self)
        if len(attempts) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(E621MatchedPV, "send_post", _send)

    await e621_module.process_new_posts()  # must not raise

    assert len(attempts) == 2
    assert sent_set == {5002}  # failed post is retried next tick
    assert cursor_log == [102]


@pytest.mark.asyncio
async def test_e621_matched_pv_delegates_to_post(monkeypatch):
    from websites import e621 as e621_module

    post = AsyncMock()
    monkeypatch.setattr(E621, "get_post", AsyncMock(return_value=post))
    plan = E621MatchedPV([Query("wolf")], make_pv(101, 5001))

    await plan.send_post()

    e621_module.get_post.assert_awaited_once_with(5001)
    assert post.send_post.await_count == 1
    assert [str(q) for q in post.send_post.await_args.args[0]] == ["wolf"]
