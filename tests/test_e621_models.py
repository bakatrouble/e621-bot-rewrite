from websites.e621 import E621Post, E621PostVersion
from context.query import Query


def _make_version(tags="wolf fox", added=None, removed=None, vid=10, pid=5):
    return E621PostVersion(
        id=vid,
        post_id=pid,
        tags=tags,
        added_tags=added if added is not None else [],
        removed_tags=removed if removed is not None else [],
    )


def _make_post():
    return E621Post.model_validate(
        {
            "id": 1,
            "created_at": "2024-01-01",
            "updated_at": "2024-01-02",
            "file": {
                "width": 1,
                "height": 1,
                "ext": "jpg",
                "size": 1,
                "md5": "d" * 32,
                "url": "https://example.com/x.jpg",
            },
            "tags": {
                "general": ["forest"],
                "species": ["wolf"],
                "character": [],
                "copyright": [],
                "artist": ["artist1"],
                "invalid": [],
                "lore": [],
                "meta": [],
            },
        }
    )


def test_flat_tags_concatenates_all_categories():
    post = _make_post()
    assert set(post.flat_tags) == {"forest", "wolf", "artist1"}


def test_check_queries_newly_matched():
    # current={wolf, fox}, prev={wolf}: "wolf fox" is newly matched
    pv = _make_version(tags="wolf fox", added=["fox"], removed=[])
    matched = pv.check_queries([Query("wolf fox"), Query("deer")])
    assert [str(q) for q in matched] == ["wolf fox"]


def test_check_queries_already_matched_not_reported():
    # current={wolf}, prev={wolf}: already matched -> no report
    pv = _make_version(tags="wolf", added=[], removed=[])
    assert pv.check_queries([Query("wolf")]) == []


def test_check_queries_negative_blocks_current():
    pv = _make_version(tags="wolf human", added=["human"], removed=[])
    assert pv.check_queries([Query("wolf -human")]) == []


def test_check_queries_removed_tag_prev_reconstruction():
    # current={wolf}, removed=[fox] -> prev={wolf, fox}
    # "wolf fox" matched prev but not current -> not reported
    pv = _make_version(tags="wolf", added=[], removed=["fox"])
    assert pv.check_queries([Query("wolf fox")]) == []
