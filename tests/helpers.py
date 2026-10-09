"""Shared builders and stubs for e621 / Gelbooru tests. Test code only."""

import asyncio
import sys
from datetime import timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from context import AppContext
from websites.e621 import E621Post, E621PostVersion
from websites.gelbooru import GelbooruPost, GelbooruTag

# NB: `websites.e621` / `websites.gelbooru` attributes on the package are the
# classes, so module-level names (resize_image, send_as_photo, ...) must be
# patched on the real module objects below.
e621_module = sys.modules['websites.e621']
gelbooru_module = sys.modules['websites.gelbooru']


class _Lock:
    """Async context manager standing in for the redis distributed lock."""

    def __init__(self):
        self.acquired = 0

    async def __aenter__(self):
        self.acquired += 1
        return self

    async def __aexit__(self, *args):
        return False


def _no_sleep(monkeypatch):
    monkeypatch.setattr(asyncio, 'sleep', AsyncMock())


def make_config(*, gelbooru=True):
    gel = (
        SimpleNamespace(
            user_id='test-user',
            api_key='test-key',
            interval=timedelta(seconds=600),
        )
        if gelbooru
        else None
    )
    return SimpleNamespace(interval=timedelta(seconds=300), gelbooru=gel)


def make_ctx(*, config=None, storage=None, bot=None, e621=None, gelbooru=None):
    """Build a stub AppContext; only the parts a test needs must be given."""
    # A single documented cast at the seam: the stub is structurally complete
    # for the code under test, but it is not a real AppContext.
    return cast(
        AppContext,
        SimpleNamespace(
            config=config if config is not None else make_config(),
            storage=storage if storage is not None else SimpleNamespace(),
            bot=bot if bot is not None else object(),
            e621=e621 if e621 is not None else SimpleNamespace(),
            gelbooru=gelbooru if gelbooru is not None else SimpleNamespace(),
        ),
    )


def make_e621_post(
    post_id=123,
    ext='jpg',
    url: str | None = 'https://example.com/x.jpg',
    general=None,
    species=None,
    character=None,
    copyright_=None,
    artist=None,
):
    return E621Post.model_validate(
        {
            'id': post_id,
            'created_at': '2024-01-01',
            'updated_at': '2024-01-02',
            'file': {
                'width': 10,
                'height': 10,
                'ext': ext,
                'size': 1,
                'md5': 'd' * 32,
                'url': url,
            },
            'tags': {
                'general': general or [],
                'species': species or [],
                'character': character or [],
                'copyright': copyright_ or [],
                'artist': artist or [],
                'invalid': [],
                'lore': [],
                'meta': [],
            },
        }
    )


def make_pv(vid, pid, tags='wolf', added=None, removed=None):
    return E621PostVersion(
        id=vid,
        post_id=pid,
        tags=tags,
        added_tags=[tags] if added is None else added,
        removed_tags=[] if removed is None else removed,
    )


def make_gelbooru_post(
    post_id=7, tags='wolf fox', file_url='https://example.com/x.jpg', subs=None
):
    return GelbooruPost.model_validate(
        {
            'id': post_id,
            'created_at': '2024-01-01',
            'score': 0,
            'width': 10,
            'height': 10,
            'md5': 'd' * 32,
            'directory': 'dir',
            'image': 'img.jpg',
            'rating': 's',
            'change': 0,
            'owner': 'o',
            'creator_id': 1,
            'parent_id': 0,
            'sample': 0,
            'preview_height': 1,
            'preview_width': 1,
            'tags': tags,
            'title': 't',
            'has_notes': '0',
            'has_comments': '0',
            'file_url': file_url,
            'preview_url': 'https://example.com/p.jpg',
            'sample_url': 'https://example.com/s.jpg',
            'sample_height': 1,
            'sample_width': 1,
            'status': 'active',
            'post_locked': 0,
            'has_children': '0',
            'subs': subs,
        }
    )


def make_tag(name, type_=0):
    return GelbooruTag.model_validate(
        {'id': 1, 'name': name, 'count': 5, 'type': type_, 'ambiguous': 0}
    )


def stub_e621_client(*, media=b'raw-bytes'):
    return SimpleNamespace(download_media=AsyncMock(return_value=media))


def stub_gelbooru_client(*, tags=(), media=b'raw-bytes'):
    return SimpleNamespace(
        get_tags=AsyncMock(return_value=list(tags)),
        download_media=AsyncMock(return_value=media),
    )


def stub_e621_sends(monkeypatch):
    """Patch module-level media/telegram senders; return the send mocks."""
    monkeypatch.setattr(e621_module, 'resize_image', AsyncMock(return_value=b'resized'))
    monkeypatch.setattr(
        e621_module, 'convert_to_mp4', AsyncMock(return_value=b'converted')
    )
    photo = AsyncMock()
    video = AsyncMock()
    doc = AsyncMock()
    monkeypatch.setattr(e621_module, 'send_as_photo', photo)
    monkeypatch.setattr(e621_module, 'send_as_video', video)
    monkeypatch.setattr(e621_module, 'send_as_document', doc)
    return photo, video, doc


def stub_gelbooru_sends(monkeypatch):
    monkeypatch.setattr(
        gelbooru_module, 'resize_image', AsyncMock(return_value=b'resized')
    )
    monkeypatch.setattr(
        gelbooru_module, 'convert_to_mp4', AsyncMock(return_value=b'converted')
    )
    photo = AsyncMock()
    video = AsyncMock()
    doc = AsyncMock()
    monkeypatch.setattr(gelbooru_module, 'send_as_photo', photo)
    monkeypatch.setattr(gelbooru_module, 'send_as_video', video)
    monkeypatch.setattr(gelbooru_module, 'send_as_document', doc)
    return photo, video, doc


def await_args(send_mock):
    """Positional args of the single awaited call (asserts there was one)."""
    assert send_mock.await_count == 1
    (args, _kwargs) = send_mock.await_args_list[0]
    return args


def caption_of(send_mock):
    _bot, _media, caption, _post_id = await_args(send_mock)
    return caption


def make_e621_storage(*, subs=('wolf',), last_version=100, sent=()):
    """Build a stub StorageImpl; return (stub, lock, sent_set, cursor_log)."""
    lock = _Lock()
    sent_set = set(sent)
    cursor_log = []

    async def _get_post_sent(ids):
        return {i: i in sent_set for i in ids}

    async def _set_post_sent(pid):
        sent_set.add(pid)

    async def _set_last(version):
        cursor_log.append(version)

    async def _get_subs():
        return list(subs)

    async def _get_last():
        return last_version

    stub = SimpleNamespace(
        lock=lock,
        get_subs=_get_subs,
        get_last_post_version=_get_last,
        get_post_sent=_get_post_sent,
        set_post_sent=_set_post_sent,
        set_last_post_version=_set_last,
    )
    return stub, lock, sent_set, cursor_log


def make_gelbooru_storage(*, subs=('wolf',), scanned=True, sent=()):
    lock = _Lock()
    sent_set = set(sent)
    scanned_set = set(subs) if scanned else set()

    async def _get_post_sent(ids):
        return {i: i in sent_set for i in ids}

    async def _set_post_sent(pid):
        sent_set.add(pid)

    async def _get_subs():
        return list(subs)

    async def _get_scanned(s):
        return s in scanned_set

    async def _set_scanned(s):
        scanned_set.add(s)

    stub = SimpleNamespace(
        lock=lock,
        get_subs=_get_subs,
        get_scanned=_get_scanned,
        set_scanned=_set_scanned,
        get_post_sent=_get_post_sent,
        set_post_sent=_set_post_sent,
    )
    return stub, lock, sent_set, scanned_set
