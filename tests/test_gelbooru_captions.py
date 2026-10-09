"""Gelbooru caption tests: `build_sub` / `build_caption` plus routing."""

import pytest

from tests.helpers import (
    make_gelbooru_post,
    make_tag,
    stub_gelbooru_io,
    stub_gelbooru_tags,
)


def test_build_sub_single_tag():
    post = make_gelbooru_post(tags="wolf fox")
    assert post.build_sub(["wolf"]) == ["wolf"]
    assert post.build_sub(["deer"]) == []


def test_build_sub_multi_tag_query():
    post = make_gelbooru_post(tags="wolf fox forest")
    assert post.build_sub(["wolf fox"]) == ["wolf fox"]
    assert post.build_sub(["wolf deer"]) == []


def test_build_sub_multiple_subs_filtered():
    post = make_gelbooru_post(tags="wolf fox")
    assert post.build_sub(["wolf", "deer", "fox"]) == ["wolf", "fox"]
    assert post.build_sub([]) == []


@pytest.mark.asyncio
async def test_gelbooru_caption_with_subs(monkeypatch):
    post = make_gelbooru_post(post_id=7, tags="wolf fox", subs=["wolf"])
    stub_gelbooru_tags(
        monkeypatch, [make_tag("wolf"), make_tag("fox"), make_tag("artist1", type_=1)]
    )

    lines = (await post.build_caption()).split("\n")
    assert lines[0] == "Monitored tags: <b>#wolf</b>"
    assert lines[1] == "Matched queries:"
    assert lines[2] == " - <code>wolf</code>"
    assert "Artist: <b>#artist1</b>" in lines
    assert lines[-1] == "https://gelbooru.com/index.php?page=post&s=view&id=7"


@pytest.mark.asyncio
async def test_gelbooru_caption_negative_sub_format(monkeypatch):
    post = make_gelbooru_post(post_id=7, tags="wolf", subs=["wolf -human"])
    stub_gelbooru_tags(monkeypatch, [make_tag("wolf")])

    caption = await post.build_caption()
    assert caption.split("\n")[0] == "Monitored tags: <b>#wolf -#human</b>"
    assert " - <code>wolf -human</code>" in caption


@pytest.mark.asyncio
async def test_gelbooru_caption_without_subs_omits_monitored_block(monkeypatch):
    post = make_gelbooru_post(post_id=8, tags="wolf")
    stub_gelbooru_tags(monkeypatch, [make_tag("wolf")])

    caption = await post.build_caption()
    assert "Monitored tags" not in caption
    assert "Matched queries" not in caption
    assert caption.endswith("https://gelbooru.com/index.php?page=post&s=view&id=8")


@pytest.mark.asyncio
async def test_gelbooru_character_tags_truncated_at_15(monkeypatch):
    names = [f"char{i:02d}" for i in range(20)]
    post = make_gelbooru_post(post_id=3, tags=" ".join(names))
    stub_gelbooru_tags(monkeypatch, [make_tag(n, type_=4) for n in names])

    caption = await post.build_caption()
    char_line = next(line for line in caption.split("\n") if line.startswith("Character:"))
    expected = " ".join(f"#char{i:02d}" for i in range(15)) + " ..."
    assert char_line == f"Character: <b>{expected}</b>"


@pytest.mark.asyncio
async def test_gelbooru_missing_file_url_sends_nothing(monkeypatch):
    post = make_gelbooru_post(post_id=1, file_url="")
    photo, video, doc = stub_gelbooru_io(monkeypatch, [])

    await post.send_post()

    photo.assert_not_awaited()
    video.assert_not_awaited()
    doc.assert_not_awaited()


@pytest.mark.asyncio
async def test_gelbooru_routing_by_extension(monkeypatch):
    photo, video, doc = stub_gelbooru_io(monkeypatch, [])

    await make_gelbooru_post(post_id=20, file_url="https://x/a.jpg").send_post()
    assert photo.await_count == 1
    assert photo.await_args.args[3] == "g20"

    await make_gelbooru_post(post_id=21, file_url="https://x/a.webm").send_post()
    assert video.await_count == 1
    assert video.await_args.args[3] == "g21"

    await make_gelbooru_post(post_id=22, file_url="https://x/a.swf").send_post()
    assert doc.await_count == 1
    assert doc.await_args.args[3:] == ("g22", "swf")


@pytest.mark.asyncio
async def test_gelbooru_unsupported_extension_raises(monkeypatch):
    stub_gelbooru_io(monkeypatch, [])
    with pytest.raises(RuntimeError):
        await make_gelbooru_post(post_id=1, file_url="https://x/a.zip").send_post()
