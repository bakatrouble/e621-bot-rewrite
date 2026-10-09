from websites.gelbooru import GelbooruPost, GelbooruTag, GelbooruTagType


def _make_tag(**overrides):
    base = {"id": 1, "name": "some_artist", "count": 10, "type": 1, "ambiguous": 0}
    base.update(overrides)
    return GelbooruTag.model_validate(base)


def _make_post(**overrides):
    base = {
        "id": 1,
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
        "tags": "wolf fox forest",
        "title": "t",
        "has_notes": "0",
        "has_comments": "0",
        "file_url": "https://example.com/x.jpg",
        "preview_url": "https://example.com/p.jpg",
        "sample_url": "https://example.com/s.jpg",
        "sample_height": 1,
        "sample_width": 1,
        "status": "active",
        "post_locked": 0,
        "has_children": "0",
    }
    base.update(overrides)
    return GelbooruPost.model_validate(base)


def test_tag_list_splits_on_spaces():
    assert _make_post(tags="wolf fox forest").tag_list == ["wolf", "fox", "forest"]


def test_enum_type_known():
    assert _make_tag(type=1).enum_type is GelbooruTagType.ARTIST
    assert _make_tag(type=4).enum_type is GelbooruTagType.CHARACTER
    assert _make_tag(type=3).enum_type is GelbooruTagType.COPYRIGHT
    assert _make_tag(type=0).enum_type is GelbooruTagType.GENERAL


def test_enum_type_unknown():
    assert _make_tag(type=999).enum_type is GelbooruTagType.UNKNOWN


def test_hashtag_sanitizes():
    assert _make_tag(name="my-tag.x").hashtag == "#my_tag_x"
