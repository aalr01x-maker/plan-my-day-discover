import unittest

from pipeline import music


class FakeYT:
    def __init__(self, channels):
        self.channels, self.units, self.asked = channels, 0, []

    def get(self, path, params):
        self.asked.append(params["forHandle"])
        hit = self.channels.get(params["forHandle"])
        return {"items": [hit]} if hit else {}


def channel(cid, title, subs):
    return {"id": cid, "snippet": {"title": title}, "statistics": {"subscriberCount": str(subs)}}


class GuessChannelTest(unittest.TestCase):
    def test_official_handle_with_matching_name(self):
        yt = FakeYT({"@TheWeeknd": channel("UC1", "The Weeknd", 37_000_000)})
        self.assertEqual(music._guess_channel(yt, "The Weeknd", {}), "UC1")
        self.assertEqual(yt.asked, ["@TheWeeknd"])

    def test_vevo_handle(self):
        yt = FakeYT({"@AviciiVEVO": channel("UC2", "AviciiOfficialVEVO", 9_000_000)})
        self.assertEqual(music._guess_channel(yt, "Avicii", {}), "UC2")

    def test_small_or_differently_named_channels_are_refused(self):
        yt = FakeYT({"@Kalax": channel("UC3", "Kalax", 900),
                     "@KalaxOfficial": channel("UC4", "Kalax Fan Uploads", 50_000)})
        cache = {}
        self.assertIsNone(music._guess_channel(yt, "Kalax", cache))
        self.assertIsNone(music._guess_channel(yt, "Kalax", cache))    # cached: no second lookup
        self.assertEqual(len(yt.asked), 3)


if __name__ == "__main__":
    unittest.main()
