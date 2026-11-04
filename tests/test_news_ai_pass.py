"""Every story shown on a tab must have been through the AI, even when it rejects many stories."""
import json
import re
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from pipeline import common, news


class FakeAI:
    available = True

    def __init__(self):
        self.sent = 0

    def json_call(self, key, system, user, schema, max_tokens=4000):
        if key.startswith("news-month"):
            return None
        rows = []
        for line in user.splitlines():
            self.sent += 1
            ev_id = re.match(r"id=(\S+)", line).group(1)
            number = int(re.search(r"storyno(\d+)", line).group(1))
            row = {"id": ev_id, "summary": f"AI summary {number}", "country": "International", "region": "world",
                   "significance": "minor", "is_update": False, "disputed": "", "story": f"story {number}"}
            for t in news.TOPICS:
                row[t] = t == "science" and number % 10 == 9   # the AI rejects most stories
                row[t + "_why"] = ""
            rows.append(row)
        return {"events": rows}


def articles(count):
    now = common.now_utc()
    return [{"title": f"storyno{i} alpha{i} bravo{i} charlie{i} delta{i}", "summary": f"research scientists study echo{i} foxtrot{i} golf{i}",
             "url": f"https://example.com/{i}", "source": "BBC News", "published": now - timedelta(hours=1 + i % 20),
             "feed_region": None, "topic_feed": "science", "image": None} for i in range(count)]


class AiPass(unittest.TestCase):
    def build(self, ai, archive=None):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "archive.json"
            path.write_text(json.dumps(archive or {}))
            with mock.patch.object(news, "_collect", lambda: articles(200)), mock.patch.object(news, "ARCHIVE", path), \
                    mock.patch.object(news.news_images, "fill", lambda regions: {}):
                return news.build(ai=ai)

    def test_no_story_is_shown_unjudged(self):
        result = self.build(FakeAI())
        science = result["regions"]["science"]
        for period in ("today", "week", "month"):
            self.assertEqual(len(science[period]), 12)
            self.assertEqual({c["summary_by"] for c in science[period]}, {"ai"}, period)

    def test_runs_without_ai(self):
        self.assertTrue(self.build(None)["regions"]["science"]["today"])

    def test_unjudged_archive_stories_stay_out(self):
        now = common.iso(common.now_utc())
        old = {"tabs": ["science"], "parts": {"outlets": 6, "sig": 1, "latest": now, "tier": 1, "days": 9},
               "card": {"headline": "oldstory", "summary_by": "source"}, "story": ""}
        result = self.build(FakeAI(), {"old": old})
        self.assertNotIn("oldstory", [c["headline"] for c in result["regions"]["science"]["month"]])


if __name__ == "__main__":
    unittest.main()
