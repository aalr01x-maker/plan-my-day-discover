import unittest

from pipeline import music, music_catalog as mc


def row(vid, artist, title, views, published="2015-01-01T00:00:00Z"):
    return {"artist": artist, "title": title, "mbid": "yt:" + vid, "released": published[:10],
            "yt": {"id": vid, "title": f"{artist} - {title}", "channel": artist, "published": published, "views": views}}


class CatalogTest(unittest.TestCase):
    def test_new_song_lands_at_its_rank_and_pushes_others_down(self):
        cat = {}
        for i, v in enumerate([900, 800, 700]):
            mc.upsert(cat, row(f"v{i}", f"A{i}", f"S{i}", v), "rap", "test", "2026-11-14")
        mc.upsert(cat, row("new", "B", "Big", 850), "rap", "test", "2026-11-15")
        shown, _, _ = mc.rank(list(cat.items()), lambda e: e["views"], 3, 2)
        self.assertEqual([v for v, _ in shown], ["v0", "new", "v1"])

    def test_song_missing_from_todays_fetch_stays(self):
        cat = {}
        mc.upsert(cat, row("desp", "Luis Fonsi", "Despacito", 9_000_000_000), "all", "wiki", "2026-11-13")
        # next day: nothing fetched for it, it is still in the catalogue and ranks first
        shown, _, _ = mc.rank(list(cat.items()), lambda e: e["views"], 30, 2)
        self.assertEqual(shown[0][1]["title"], "Despacito")

    def test_duplicate_and_artist_limit_are_documented(self):
        cat = {}
        mc.upsert(cat, row("a", "X", "Song", 100), "rap", "t", "d")
        mc.upsert(cat, row("b", "X", "Song (Official Video)", 50), "rap", "t", "d")
        mc.upsert(cat, row("c", "X", "Two", 90), "rap", "t", "d")
        mc.upsert(cat, row("d", "X", "Three", 80), "rap", "t", "d")
        shown, skipped, order = mc.rank(list(cat.items()), lambda e: e["views"], 5, 2)
        self.assertEqual([v for v, _ in shown], ["a", "c"])
        self.assertIn("duplicate", skipped["b"])
        self.assertIn("artist limit", skipped["d"])
        self.assertEqual(mc.verify(shown, skipped, order, lambda e: e["views"]), [])

    def test_verify_flags_an_undocumented_omission(self):
        cat = {}
        mc.upsert(cat, row("a", "X", "One", 100), "rap", "t", "d")
        mc.upsert(cat, row("b", "Y", "Two", 50), "rap", "t", "d")
        order = mc.rank(list(cat.items()), lambda e: e["views"], 5, 2)[2]
        self.assertEqual(len(mc.verify([("b", cat["b"])], {}, order, lambda e: e["views"])), 1)

    def test_weekly_gain_needs_valid_snapshots(self):
        self.assertIsNone(mc.weekly_gain({"2026-11-14": 10}, "2026-11-14", "2026-11-07"))
        self.assertIsNone(mc.weekly_gain({"2026-11-10": 20, "2026-11-14": 10}, "2026-11-14", "2026-11-07"))
        self.assertEqual(mc.weekly_gain({"2026-11-10": 10, "2026-11-14": 50}, "2026-11-14", "2026-11-07"), (70.0, 4))

    def test_diff_reports_reasons_and_ranks(self):
        prev = [{"vid": "a", "title": "A", "artist": "x", "rank": 1}, {"vid": "b", "title": "B", "artist": "y", "rank": 2}]
        new = [{"vid": "a", "title": "A", "artist": "x", "rank": 1}, {"vid": "c", "title": "C", "artist": "z", "rank": 2}]
        ch = mc.diff(prev, new, {"a": 1, "c": 2, "b": 3}, {}, {"c": "newly found today"}, 2)
        self.assertEqual(ch[0], {"change": "added", "title": "C", "artist": "z", "before": None, "after": 2, "reason": "newly found today"})
        self.assertEqual(ch[1]["reason"], "fell below rank 2 (now #3)")


class DatesAndTitlesTest(unittest.TestCase):
    def test_musicbrainz_decides_original_date(self):
        # Axel F: iTunes reissue says 2000-01-01, video 2009; MusicBrainz 2005
        self.assertEqual(music.original_date("2005-05-23", None, "2000-01-01", "2009-06-16"), "2005-05-23")
        # Numb: video 2007, MusicBrainz 2003
        self.assertEqual(music.original_date("2003-03-25", None, None, "2007-03-05"), "2003-03-25")
        # a video before the MusicBrainz date (premiere) wins
        self.assertEqual(music.original_date("2026-03-02", None, None, "2026-03-01"), "2026-03-01")
        self.assertEqual(music.original_date("2003", "2003-03-25", None, "2007-03-05"), "2003-03-25")
        self.assertEqual(music.original_date(None, "2010-01-02", "2011-01-01", "2009-05-05"), "2009-05-05")

    def test_title_then_artist_with_en_dash(self):
        self.assertEqual(music._split_artist_title("Numb – Linkin Park", "Linkin Park"), ("Linkin Park", "Numb"))
        self.assertEqual(music._split_artist_title("Luis Fonsi - Despacito ft. Daddy Yankee", "LuisFonsiVEVO"),
                         ("Luis Fonsi", "Despacito ft. Daddy Yankee"))
        self.assertEqual(music._split_artist_title("Shape of You", "Ed Sheeran"), ("Ed Sheeran", "Shape of You"))

    def test_anniversary_reupload_is_an_incorrect_upload(self):
        e = {"title": "Faded", "video": {"title": "Faded - 10th Anniversary (Animated Music Video)", "channel": "Alan Walker"}}
        self.assertIn("anniversary", music._incorrect_upload(e))
        ok = {"title": "Live Forever", "video": {"title": "Oasis - Live Forever (Official Video)", "channel": "Oasis"}}
        self.assertIsNone(music._incorrect_upload(ok))

    def test_late_upload_of_youtube_era_song_is_rechecked(self):
        song = {"released": "2015-12-03"}
        self.assertTrue(music._todo({"id": "x", "published": "2025-12-24T13:00:07Z"}, None, song))
        self.assertFalse(music._todo({"id": "x", "published": "2025-12-24T13:00:07Z", "rechecked": True}, None, song))
        self.assertFalse(music._todo({"id": "x", "published": "2008-01-01T00:00:00Z"}, None, {"released": "1975-10-31"}))


if __name__ == "__main__":
    unittest.main()
