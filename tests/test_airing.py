from datetime import date

from pipeline import airing, common, movies

TABLE = """== October–December ==
{| class="wikitable sortable"
|-
! rowspan="30" data-sort-value="11"|<span aria-label="November">N<br>O<br>V</span>
| rowspan="2" style="background:#f7dfc7" | '''6'''
| ''[[The Cat in the Hat (2026 film)|The Cat in the Hat]]''
| Warner Bros.
| Director
| <ref>x</ref>
|-
| ''[[Wild Horse Nine]]''
| Studio
|-
| rowspan="2" | '''13'''
| ''God with Us''
| Studio
|-
| {{sort|Paper|''[[Paper Tiger (2026 film)|Paper Tiger]]''}}
| Studio
|-
! rowspan="1"|<span aria-label="December">D<br>E<br>C</span>
| '''1'''
| ''[[Later]]''
|}
"""


def test_parse_carries_month_and_day_across_rows_and_reads_every_title_form():
    rows = airing.parse_list(TABLE, 2026)
    assert [(r["date"], r["title"], r["page"]) for r in rows] == [
        ("2026-11-06", "The Cat in the Hat", "The Cat in the Hat (2026 film)"), ("2026-11-06", "Wild Horse Nine", "Wild Horse Nine"),
        ("2026-11-13", "God with Us", None), ("2026-11-13", "Paper Tiger", "Paper Tiger (2026 film)"), ("2026-12-01", "Later", "Later")]


def test_window_is_the_past_seven_days_and_the_next_seven_by_date():
    rows = airing.parse_list(TABLE, 2026)
    assert [r["title"] for r in airing.in_window(rows, date(2026, 11, 7))] == ["The Cat in the Hat", "Wild Horse Nine", "God with Us", "Paper Tiger"]
    assert [r["title"] for r in airing.in_window(rows, date(2026, 11, 14))] == ["God with Us", "Paper Tiger"]
    assert [r["title"] for r in airing.in_window(rows, date(2026, 11, 21))] == []
    assert [r["title"] for r in airing.in_window(rows, date(2026, 11, 26))] == ["Later"]


def test_ids_come_from_wikidata_and_a_film_without_one_stays_listed(monkeypatch):
    def fake(url, params=None, **kw):
        if url == airing.WIKIPEDIA:
            return {"parse": {"wikitext": TABLE}}
        return {"entities": {"Q1": {"sitelinks": {"enwiki": {"title": "Paper Tiger (2026 film)"}},
                                    "claims": {"P345": [{"mainsnak": {"datavalue": {"value": "tt9"}}}]}}}}
    monkeypatch.setattr(common, "get", fake)
    assert [(r["title"], r["imdb"]) for r in airing.this_week(date(2026, 11, 14))] == [("God with Us", None), ("Paper Tiger", "tt9")]


def test_when_wikipedia_fails_the_last_list_is_kept_without_passed_films(monkeypatch):
    def down(*a, **k):
        raise common.FetchError("down")
    monkeypatch.setattr(airing.common, "get", down)
    last = [{"date": "2026-10-30", "title": "Old"}, {"date": "2026-11-06", "title": "Out"}, {"date": "2026-11-08", "title": "Soon"}]
    state = {"airing_last": last}
    assert [r["title"] for r in movies._airing_rows(state, date(2026, 11, 7))] == ["Out", "Soon"] and state["airing_last"] == last


def test_a_page_with_no_films_counts_as_a_failure(monkeypatch):
    monkeypatch.setattr(airing.common, "get", lambda *a, **k: {"parse": {"wikitext": "changed layout"}})
    assert movies._airing_rows({}, date(2026, 11, 7)) == []


class FakeTMDB:
    def get(self, path, params=None, ttl_h=0):
        if path.startswith("/find/"):
            return {"movie_results": [{"id": 5}]}
        return {"overview": "O", "poster_path": "/p.jpg", "genres": [{"name": "Drama"}]}


def test_card_uses_imdb_rating_and_shows_unrated_or_unmatched_films():
    row = {"date": "2026-11-13", "title": "Paper Tiger", "page": "Paper Tiger (2026 film)", "imdb": "tt9"}
    card = movies._airing_card(FakeTMDB(), {"tt9": (7.1, 400)}, {}, row)
    assert card["rating"]["value"] == 7.1 and card["poster"].endswith("/p.jpg") and card["genres"] == ["Drama"] and card["airing"]
    assert card["links"][0]["url"].endswith("/tt9/") and card["links"][1]["url"].endswith("Paper_Tiger_(2026_film)")
    bare = movies._airing_card(FakeTMDB(), {}, {}, {"date": "2026-11-13", "title": "God with Us", "page": None, "imdb": None})
    assert bare["rating"]["value"] is None and bare["poster"] is None and bare["links"][0]["url"].endswith("List_of_American_films_of_2026")
