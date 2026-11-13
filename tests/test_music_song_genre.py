import unittest

from pipeline import music


def run(gid, song, track, artist):
    """song_in_genre with pre-filled caches (no network: key None)."""
    now = music.common.iso(music.common.now_utc())
    tt = {music.common.norm(f"{song['artist']} {music._song_title_for_tags(song['title'])}").strip(): {"at": now, "t": track}} \
        if track is not None else {}
    at = {song["artist"]: {"at": now, "t": artist}} if artist is not None else {}
    return music.song_in_genre(gid, song, None, tt, at, [0], [0])


class SongGenreTest(unittest.TestCase):
    def test_song_tags_decide_over_artist(self):
        # Mark Ronson has hip hop in his top 5, Uptown Funk itself is funk/pop
        self.assertFalse(run("rap", {"artist": "Mark Ronson", "title": "Uptown Funk ft. Bruno Mars"},
                             "funk | pop | dance | soul | 2014", "funk | british | pop | hip hop | dj"))
        self.assertTrue(run("rap", {"artist": "Wiz Khalifa", "title": "See You Again ft. Charlie Puth Furious 7 Soundtrack"},
                            "hip hop | rap | pop | soundtrack", "hip hop | rap"))

    def test_rap_song_by_pop_artist_counts(self):
        self.assertTrue(run("rap", {"artist": "Some Pop Star", "title": "Big Verse"},
                            "hip hop | rap | 2025", "pop | dance | female vocalists"))

    def test_no_genre_on_song_falls_back_to_artist(self):
        self.assertTrue(run("rap", {"artist": "Drake", "title": "New One"}, "2025 | seen live", "hip hop | rap"))
        self.assertTrue(run("rap", {"artist": "Drake", "title": "New One"}, "", "hip hop | rap"))
        self.assertFalse(run("rap", {"artist": "Rihanna", "title": "Take a Bow"}, "", "pop | rnb | female vocalists | dance | hip hop"))

    def test_youtube_find_unknown_everywhere_stays_out(self):
        self.assertFalse(run("rap", {"artist": "Nobody", "title": "X", "mbid": "yt:abc"}, None, None))

    def test_arabic_and_all_exempt(self):
        self.assertTrue(run("arabic", {"artist": "Am miraz", "title": "Most Viral Arabic DJ Song"}, None, None))

    def test_same_song_key(self):
        a = music.same_song_key("TUL8TE", "Nano")
        b = music.same_song_key("TUL8TE", "Nano I تووليت، ساينت ليفانت - نانو")
        self.assertEqual(a, b)
        self.assertNotEqual(music.same_song_key("Billie Eilish", "Everything I Wanted"), music.same_song_key("Billie Eilish", "Everything"))
        self.assertEqual(music.same_song_key("Bob Marley", "Is This Love"), music.same_song_key("Bob Marley & The Wailers", "Is This Love (Remastered)"))


if __name__ == "__main__":
    unittest.main()
