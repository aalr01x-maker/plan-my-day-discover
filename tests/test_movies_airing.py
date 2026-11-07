from datetime import date

from pipeline import movies


class FakeIMDb:
    def rating(self, iid):
        return (8.1, 900) if iid == "tt1" else None


class FakeTMDB:
    """Answers like TMDB: discover lists, details with release dates, ids, and keywords."""

    def __init__(self, listed, details, keyworded=()):
        self.listed, self.details, self.keyworded, self.calls = listed, details, keyworded, []

    def keyword(self, word):
        return {"biography": "7", "superhero": "9"}.get(word)

    def get(self, path, params=None, ttl_h=0):
        self.calls.append((path, params))
        if path == "/discover/movie":
            if params["page"] > 1:
                return {"results": [], "total_pages": 1}
            rows = self.keyworded if "with_keywords" in params else self.listed
            return {"results": [dict(t) for t in rows], "total_pages": 1}
        if path.endswith("/external_ids"):
            return {"imdb_id": f"tt{path.split('/')[2]}"}
        return self.details[int(path.split("/")[2])]


def _movie(tid, genres, popularity=10, **extra):
    return {"id": tid, "title": f"M{tid}", "genre_ids": genres, "popularity": popularity, "poster_path": "/p.jpg", "overview": "O", **extra}


def _release(region="US", day="2026-11-10", kind=3):
    return {"title": "Detail title", "release_dates": {"results": [{"iso_3166_1": region, "release_dates": [{"release_date": f"{day}T00:00:00.000Z", "type": kind}]}]}}


def test_window_uses_kuwait_before_us_and_ignores_other_dates():
    block = {"results": [{"iso_3166_1": "US", "release_dates": [{"release_date": "2026-11-08T00:00:00Z", "type": 3}]},
                         {"iso_3166_1": "KW", "release_dates": [{"release_date": "2026-11-12T00:00:00Z", "type": 3},
                                                                {"release_date": "2026-11-20T00:00:00Z", "type": 4}]}]}
    assert movies._release_in_window(block, "2026-11-07", "2026-11-13") == ("2026-11-12", "KW", "in theaters")
    assert movies._release_in_window(block, "2026-12-01", "2026-12-07") is None
    premiere = {"results": [{"iso_3166_1": "US", "release_dates": [{"release_date": "2026-11-08T00:00:00Z", "type": 1}]}]}
    assert movies._release_in_window(premiere, "2026-11-07", "2026-11-13") is None


def test_each_tab_follows_its_own_rule():
    tabs = movies.TABS
    action, cartoon = _movie(1, [28]), _movie(2, [16, 28])
    anime = _movie(3, [16], original_language="ja")
    doc = _movie(4, [99, 18])
    assert movies._airing_belongs(tabs["all"][1], doc, None)
    assert movies._airing_belongs(tabs["action"][1], action, None)
    assert not movies._airing_belongs(tabs["action"][1], cartoon, None)
    assert movies._airing_belongs(tabs["anime"][1], anime, None)
    assert not movies._airing_belongs(tabs["anime"][1], cartoon, None)
    assert not movies._airing_belongs(tabs["crime-drama"][1], doc, None)
    assert movies._airing_belongs(tabs["documentary"][1], doc, None)
    assert not movies._airing_belongs(tabs["classic"][1], action, None)
    assert movies._airing_belongs(tabs["superhero"][1], action, {1})
    assert not movies._airing_belongs(tabs["superhero"][1], doc, {1})


def test_lists_are_checked_ranked_by_popularity_and_every_tab_exists():
    listed = [_movie(1, [28], 5), _movie(2, [28], 50), _movie(3, [28], 90), {**_movie(4, [28], 99), "poster_path": None}]
    details = {1: _release("KW"), 2: _release(), 3: _release(day="2027-01-01"), 5: _release()}
    keyworded = [_movie(5, [18], 70)]
    tmdb = FakeTMDB(listed, details, keyworded)
    out = movies.airing_lists(tmdb, FakeIMDb(), date(2026, 11, 7))
    assert set(out) == set(movies.TABS)
    assert [c["title"] for c in out["action"]] == ["Detail title", "Detail title"]  # 3's date is outside the week, 4 has no poster
    assert [c["score"] for c in out["action"]] == [50, 5] and [c["rank"] for c in out["action"]] == [1, 2]
    assert out["action"][1]["why"] == "Out 2026-11-10 in theaters in Kuwait; ranked by TMDB popularity."
    assert out["action"][0]["rating"] == {"value": None, "votes": 0, "source": "IMDb"} and out["action"][0]["airing"]
    assert [c["id"] for c in out["biography"]] == ["movie-tt5"] and out["classic"] == []
    assert any(p and p.get("with_keywords") == "7" for _, p in tmdb.calls)
    assert all(p["release_date.gte"] == "2026-11-07" and p["release_date.lte"] == "2026-11-13" for path, p in tmdb.calls if path == "/discover/movie")


def test_slim_keeps_only_kuwait_and_us_release_dates():
    data = {"id": 1, "release_dates": {"results": [{"iso_3166_1": "US"}, {"iso_3166_1": "FR"}, {"iso_3166_1": "KW"}]}}
    assert [r["iso_3166_1"] for r in movies._slim("/movie/1", data)["release_dates"]["results"]] == ["US", "KW"]
