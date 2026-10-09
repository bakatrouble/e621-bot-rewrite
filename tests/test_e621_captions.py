"""e621 caption tests: `build_caption` directly plus `send_post` routing."""

import pytest

from context.query import Query
from tests.helpers import caption_of, make_e621_post, stub_e621_io


def test_e621_caption_with_matched_queries():
    post = make_e621_post(
        post_id=123,
        species=["wolf"],
        general=["forest"],
        artist=["artist1"],
        character=["char1"],
        copyright_=["copy1"],
    )
    lines = post.build_caption([Query("wolf")]).split("\n")
    assert lines[0] == "Monitored tags: <b>#wolf</b>"
    assert lines[1] == "Matched queries:"
    assert lines[2] == " - <code>wolf</code>"
    assert "Artist: <b>#artist1</b>" in lines
    assert "Character: <b>#char1</b>" in lines
    assert "Copyright: <b>#copy1</b>" in lines
    assert lines[-1] == "https://e621.net/posts/123"


def test_e621_caption_without_queries_omits_monitored_block():
    caption = make_e621_post(post_id=5, artist=["artist1"]).build_caption()
    assert "Monitored tags" not in caption
    assert "Matched queries" not in caption
    assert "Artist: <b>#artist1</b>" in caption
    assert caption.endswith("https://e621.net/posts/5")


def test_e621_monitored_tags_only_includes_post_tags():
    # query mentions fox, but the post has no fox -> only wolf is listed
    post = make_e621_post(post_id=9, species=["wolf"])
    caption = post.build_caption([Query("wolf fox")])
    assert caption.split("\n")[0] == "Monitored tags: <b>#wolf</b>"
    assert "#fox" not in caption.split("\n")[0]
    assert " - <code>wolf fox</code>" in caption


def test_e621_character_tags_truncated_at_15():
    chars = [f"char{i:02d}" for i in range(20)]
    caption = make_e621_post(post_id=1, character=chars).build_caption()
    char_line = next(line for line in caption.split("\n") if line.startswith("Character:"))
    expected = " ".join(f"#char{i:02d}" for i in range(15)) + " ..."
    assert char_line == f"Character: <b>{expected}</b>"


def test_e621_query_text_interpolated_verbatim():
    # documents current behavior: matched-query text is not HTML-escaped
    caption = make_e621_post(post_id=1, general=["a"]).build_caption([Query("a")])
    assert " - <code>a</code>" in caption


def test_e621_caption_no_tags_yet_ends_with_url():
    caption = make_e621_post(post_id=42).build_caption()
    assert caption.endswith("https://e621.net/posts/42")


@pytest.mark.asyncio
async def test_e621_missing_file_url_sends_nothing(monkeypatch):
    post = make_e621_post(post_id=1, url=None)
    photo, video, doc = stub_e621_io(monkeypatch)

    await post.send_post([Query("wolf")])

    photo.assert_not_awaited()
    video.assert_not_awaited()
    doc.assert_not_awaited()


@pytest.mark.asyncio
async def test_e621_routing_by_extension(monkeypatch):
    photo, video, doc = stub_e621_io(monkeypatch)

    await make_e621_post(post_id=10, ext="jpg").send_post()
    assert photo.await_count == 1
    assert photo.await_args.args[3] == "e10"

    await make_e621_post(post_id=11, ext="gif").send_post()
    assert video.await_count == 1
    assert video.await_args.args[3] == "e11"

    await make_e621_post(post_id=12, ext="swf").send_post()
    assert doc.await_count == 1
    assert doc.await_args.args[3:] == ("e12", "swf")


@pytest.mark.asyncio
async def test_e621_send_post_uses_build_caption(monkeypatch):
    post = make_e621_post(post_id=123, species=["wolf"])
    photo, _, _ = stub_e621_io(monkeypatch)

    await post.send_post([Query("wolf")])

    assert caption_of(photo) == post.build_caption([Query("wolf")])


@pytest.mark.asyncio
async def test_e621_unsupported_extension_raises(monkeypatch):
    stub_e621_io(monkeypatch)
    with pytest.raises(RuntimeError):
        await make_e621_post(post_id=1, ext="zip").send_post()
