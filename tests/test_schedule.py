import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import schedule  # noqa: E402

UTC = timezone.utc


class ScheduleTest(unittest.TestCase):
    def test_cron_is_local_noon_in_utc(self):
        now = datetime(2026, 10, 31, 8, tzinfo=UTC)
        self.assertEqual(schedule.cron("Asia/Kuwait", now=now), "0 9 * * *")
        self.assertEqual(schedule.cron("America/New_York", now=now), "0 16 * * *")   # EDT
        self.assertEqual(schedule.cron("Asia/Tokyo", now=now), "0 3 * * *")
        self.assertEqual(schedule.cron("Asia/Kolkata", now=now), "0 7 * * *")        # 06:30 rounds up

    def test_tomorrow_follows_daylight_saving(self):
        # New York leaves daylight saving on 1 Nov 2026: noon moves from 16:00 to 17:00 UTC
        self.assertEqual(schedule.cron("America/New_York", True, datetime(2026, 10, 31, 20, tzinfo=UTC)), "0 17 * * *")

    def test_bad_names_are_rejected(self):
        for bad in ["", "Mars/Olympus", "Europe/London; rm -rf /", "$(id)"]:
            with self.assertRaises(ValueError):
                schedule.zone(bad)

    def test_due_only_after_noon_without_a_newer_update(self):
        meta = {"sections": {"news": {"updated_at": "2026-10-31T08:00:00+00:00"}}}
        self.assertFalse(schedule.due("Asia/Kuwait", meta, datetime(2026, 10, 31, 8, 30, tzinfo=UTC)))   # 11:30 local
        self.assertTrue(schedule.due("Asia/Kuwait", meta, datetime(2026, 10, 31, 10, tzinfo=UTC)))       # 13:00, last 11:00
        meta["sections"]["news"]["updated_at"] = "2026-10-31T09:20:00+00:00"
        self.assertFalse(schedule.due("Asia/Kuwait", meta, datetime(2026, 10, 31, 10, tzinfo=UTC)))


if __name__ == "__main__":
    unittest.main()
