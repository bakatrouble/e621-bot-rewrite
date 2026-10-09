"""Shared builders and stubs for e621 / Gelbooru tests. Test code only."""

import asyncio
import sys
from unittest.mock import AsyncMock

from context import storage
from websites import e621 as e621_singleton
from websites import gelbooru as gelbooru_singleton
from websites.e621 import E621Post, E621PostVersion
from websites.gelbooru import GelbooruPost, GelbooruTag

# NB: `websites.e621` / `websites.gelbooru` attributes are singleton
# instances shadowing the submodules, so module-level names (resize_image,
# send_as_photo, ...) must be patched on the real module objects below.
e621_module = sys.modules["websites.e621"]
gelbooru_module = sys.modules["websites.gelbooru"]


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
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())


def make_e621_post(
    post_id=123,
    ext="jpg",
    url="https://example.com/x.jpg",
    general=None,
    species=None,
    character=None,
    copyright_=None,
    artist=None,
):
    return E621Post.model_validate(
        {
            "id": post_id,
            "created_at": "2024-01-01",
            "updated_at": "2024-01-02",
            "file": {
                "width": 10,
                "height": 10,
                "ext": ext,
                "size": 1,
                "md5": "d" * 32,
                "url": url,
            },
            "tags": {
                "general": general or [],
                "species": species or [],
                "character": character or [],
                "copyright": copyright_ or [],
                "artist": artist or [],
                "invalid": [],
                "lore": [],
                "meta": [],
            },
        }
    )


def make_pv(vid, pid, tags="wolf", added=None, removed=None):
    return E621PostVersion(
        id=vid,
        post_id=pid,
        tags=tags,
        added_tags=[tags] if added is None else added,
        removed_tags=[] if removed is None else removed,
    )


def make_gelbooru_post(post_id=7, tags="wolf fox", file_url="https://example.com/x.jpg", subs=None):
    return GelbooruPost.model_validate(
        {
            "id": post_id,
            "created_at": "2024-01-01",
            "score": 0,
            "width": 10,
            "height": 10,
            "md5": "d" * 32,
            "directory": "dir",
            "image": "img.jpg",
            "rating": "s",
            "change": 0,
            "owner": "o",
            "creator_id": 1,
            "parent_id": 0,
            "sample": 0,
            "preview_height": 1,
            "preview_width": 1,
            "tags": tags,
            "title": "t",
            "has_notes": "0",
            "has_comments": "0",
            "file_url": file_url,
            "preview_url": "https://example.com/p.jpg",
            "sample_url": "https://example.com/s.jpg",
            "sample_height": 1,
            "sample_width": 1,
            "status": "active",
            "post_locked": 0,
            "has_children": "0",
            "subs": subs,
        }
    )


def make_tag(name, type_=0):
    return GelbooruTag.model_validate(
        {"id": 1, "name": name, "count": 5, "type": type_, "ambiguous": 0}
    )


def stub_e621_io(monkeypatch, media=b"raw-bytes"):
    """Stub network + media + telegram layers; return the send mocks."""
    monkeypatch.setattr(e621_singleton, "download_media", AsyncMock(return_value=media))
    monkeypatch.setattr(e621_module, "resize_image", AsyncMock(return_value=b"resized"))
    monkeypatch.setattr(e621_module, "convert_to_mp4", AsyncMock(return_value=b"converted"))
    photo = AsyncMock()
    video = AsyncMock()
    doc = AsyncMock()
    monkeypatch.setattr(e621_module, "send_as_photo", photo)
    monkeypatch.setattr(e621_module, "send_as_video", video)
    monkeypatch.setattr(e621_module, "send_as_document", doc)
    return photo, video, doc


def stub_gelbooru_io(monkeypatch, tags, media=b"raw-bytes"):
    monkeypatch.setattr(gelbooru_singleton, "get_tags", AsyncMock(return_value=tags))
    monkeypatch.setattr(gelbooru_singleton, "download_media", AsyncMock(return_value=media))
    monkeypatch.setattr(gelbooru_module, "resize_image", AsyncMock(return_value=b"resized"))
    monkeypatch.setattr(gelbooru_module, "convert_to_mp4", AsyncMock(return_value=b"converted"))
    photo = AsyncMock()
    video = AsyncMock()
    doc = AsyncMock()
    monkeypatch.setattr(gelbooru_module, "send_as_photo", photo)
    monkeypatch.setattr(gelbooru_module, "send_as_video", video)
    monkeypatch.setattr(gelbooru_module, "send_as_document", doc)
    return photo, video, doc


def stub_gelbooru_tags(monkeypatch, tags):
    monkeypatch.setattr(gelbooru_singleton, "get_tags", AsyncMock(return_value=tags))


def caption_of(send_mock):
    assert send_mock.await_count == 1
    _bot, _media, caption, _post_id = send_mock.await_args.args
    return caption


def stub_e621_storage(monkeypatch, subs=("wolf",), last_version=100, sent=()):
    """Replace storage.e621 collaborators; return (lock, sent_set, cursor_log)."""
    lock = _Lock()
    sent_set = set(sent)
    cursor_log = []

    async def _get_post_sent(ids):
        return {i: i in sent_set for i in ids}

    async def _set_post_sent(pid):
        sent_set.add(pid)

    async def _set_last(version):
        cursor_log.append(version)

    monkeypatch.setattr(storage.e621, "lock", lock)
    monkeypatch.setattr(storage.e621, "get_subs", AsyncMock(return_value=list(subs)))
    monkeypatch.setattr(storage.e621, "get_last_post_version", AsyncMock(return_value=last_version))
    monkeypatch.setattr(storage.e621, "get_post_sent", AsyncMock(side_effect=_get_post_sent))
    monkeypatch.setattr(storage.e621, "set_post_sent", AsyncMock(side_effect=_set_post_sent))
    monkeypatch.setattr(storage.e621, "set_last_post_version", AsyncMock(side_effect=_set_last))
    return lock, sent_set, cursor_log


def stub_gelbooru_storage(monkeypatch, subs=("wolf",), scanned=True, sent=()):
    lock = _Lock()
    sent_set = set(sent)
    scanned_set = set(subs) if scanned else set()

    async def _get_post_sent(ids):
        return {i: i in sent_set for i in ids}

    async def _set_post_sent(pid):
        sent_set.add(pid)

    monkeypatch.setattr(storage.gelbooru, "lock", lock)
    monkeypatch.setattr(storage.gelbooru, "get_subs", AsyncMock(return_value=list(subs)))
    monkeypatch.setattr(
        storage.gelbooru, "get_scanned", AsyncMock(side_effect=lambda s: s in scanned_set)
    )
    monkeypatch.setattr(storage.gelbooru, "set_scanned", AsyncMock(
        side_effect=lambda s: scanned_set.add(s)))
    monkeypatch.setattr(storage.gelbooru, "get_post_sent", AsyncMock(side_effect=_get_post_sent))
    monkeypatch.setattr(storage.gelbooru, "set_post_sent", AsyncMock(side_effect=_set_post_sent))
    return lock, sent_set, scanned_set
