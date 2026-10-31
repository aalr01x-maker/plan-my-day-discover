import unittest
from types import SimpleNamespace
from unittest import mock

from pipeline import ai, common, geo, games, music, sports


class Ratings(unittest.TestCase):
    def test_few_votes_pull_toward_prior(self):
        few = common.bayes_rating(9.8, 12, 7.0, 1000)
        many = common.bayes_rating(9.0, 50000, 7.0, 1000)
        self.assertLess(few, many)

    def test_user_score_needs_reviews(self):
        self.assertEqual(games._user_score(10, 2)[0], None)
        hi_few, _ = games._user_score(40, 0)
        hi_many, _ = games._user_score(40000, 2000)
        self.assertLess(hi_few, hi_many)


class Regions(unittest.TestCase):
    def test_headline_place(self):
        self.assertEqual(geo.guess_regions("Floods in Kenya displace thousands")[:1], ["africa"])
        self.assertEqual(geo.guess_regions("New York mayor race")[:1], ["north-america"])

    def test_leaders_are_not_places(self):
        self.assertEqual(geo.guess_regions("Trump meets Zelensky in Kyiv")[:1], ["europe"])

    def test_whole_phrases_only(self):
        self.assertNotIn("north-america", geo.guess_regions("A new plan for Spain"))


class Budget(unittest.TestCase):
    def test_hard_cap_blocks_worst_case(self):
        b = ai.Budget(cap=15.0)
        b.data = {b.month: {"usd": 14.999}}
        self.assertFalse(b.allows(prompt_chars=30000, max_tokens=4000))
        b.data = {b.month: {"usd": 1.0}}
        self.assertTrue(b.allows(prompt_chars=30000, max_tokens=4000))

    @mock.patch.object(common, "log")
    def test_no_call_over_cap(self, _log):
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "x"}):
            a = ai.AI()
        a.cache = {}
        a.budget.data = {a.budget.month: {"usd": 15.0}}
        a._client = SimpleNamespace(messages=SimpleNamespace(create=mock.Mock(side_effect=AssertionError("called"))))
        self.assertIsNone(a.json_call("k", "s", "u", {"type": "object"}))
        self.assertFalse(a.available)

    def test_cached_result_costs_nothing(self):
        a = ai.AI()
        a.cache = {"k": {"ok": 1}}
        self.assertEqual(a.json_call("k", "s", "u", {}), {"ok": 1})


class Tagging(unittest.TestCase):
    def test_league_phrases(self):
        s = sports.SPORTS["soccer"]
        self.assertEqual(sports._league("Arsenal beat Chelsea at the Emirates", s), "Premier League")
        self.assertIsNone(sports._league("Weather delays kick-off", s))

    def test_music_fit_requires_tag(self):
        meta = {"tags": music._tag_strengths({"recording": [{"tag": "synthwave", "count": 3}],
                                              "artist": [{"tag": "rock", "count": 9}]})}
        self.assertGreater(music._fit(meta, ["synthwave", "retrowave"]), 0.7)
        self.assertEqual(music._fit(meta, ["nu disco"]), 0)


if __name__ == "__main__":
    unittest.main()
