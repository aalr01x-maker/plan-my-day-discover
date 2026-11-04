import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pipeline import news_images as ni


def regions(*cards):
    return {"world": {"today": list(cards), "week": [], "month": []}}


def card(cid, sources, image=None):
    return {"id": cid, "image": image, "sources": [{"name": n, "url": u} for n, u in sources]}


class Fill(unittest.TestCase):
    def run_fill(self, data, answers):
        calls = []

        def fake(link):
            calls.append(link)
            answer = answers.get(link)
            if isinstance(answer, Exception):
                raise answer
            return answer
        with tempfile.TemporaryDirectory() as tmp:
            self.cache = Path(tmp) / "c.json"
            with mock.patch.object(ni, "CACHE", self.cache), mock.patch.object(ni, "image_from_link", fake):
                ni.fill(data)
                self.saved = self.cache.read_text()
        return calls

    def test_best_outlet_first_then_next_when_it_refuses(self):
        c = card("a", [("Reuters", "r"), ("BBC", "b"), ("CNBC", "c")])
        calls = self.run_fill(regions(c), {"r": None, "b": "bbc.jpg", "c": "cnbc.jpg"})
        self.assertEqual(c["image"], "bbc.jpg")
        self.assertEqual(calls, ["r", "b"])

    def test_cards_with_a_picture_are_untouched_and_never_looked_up(self):
        c = card("a", [("Reuters", "r")], image="keep.jpg")
        calls = self.run_fill(regions(c), {"r": "new.jpg"})
        self.assertEqual(c["image"], "keep.jpg")
        self.assertEqual(calls, [])

    def test_same_story_in_two_tabs_gets_one_picture_one_lookup(self):
        one, two = card("a", [("BBC", "b")]), card("a", [("BBC", "b")])
        calls = self.run_fill({"x": {"today": [one], "week": [two], "month": []}}, {"b": "p.jpg"})
        self.assertEqual((one["image"], two["image"], calls), ("p.jpg", "p.jpg", ["b"]))

    def test_temporary_failure_moves_on_and_is_not_cached(self):
        c = card("a", [("BBC", "b"), ("CNBC", "c")])
        self.run_fill(regions(c), {"b": ni.Temporary("429"), "c": "c.jpg"})
        self.assertEqual(c["image"], "c.jpg")
        self.assertNotIn('"b"', self.saved)

    def test_no_picture_anywhere_stays_empty(self):
        c = card("a", [("Reuters", "r")])
        self.run_fill(regions(c), {"r": None})
        self.assertIsNone(c["image"])


class PageImage(unittest.TestCase):
    def test_reads_either_attribute_order_and_unescapes(self):
        self.assertEqual(ni.page_image('<meta property="og:image" content="https://x/a.jpg?a=1&amp;b=2">'), "https://x/a.jpg?a=1&b=2")
        self.assertEqual(ni.page_image("<meta content='https://x/a.jpg' property='og:image'/>"), "https://x/a.jpg")

    def test_ignores_image_type_logos_and_junk(self):
        self.assertIsNone(ni.page_image('<meta property="og:image:type" content="image/jpeg">'))
        self.assertIsNone(ni.page_image('<meta property="og:image" content="https://x/site-logo.png">'))
        self.assertIsNone(ni.page_image('<meta property="og:image" content="image/jpeg">'))

    def test_prefers_https(self):
        self.assertEqual(ni.page_image('<meta property="og:image" content="http://x/a.jpg">'), "https://x/a.jpg")


if __name__ == "__main__":
    unittest.main()
