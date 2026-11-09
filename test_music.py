"""The shuffled-music endpoint lists the audio files in static/music/."""
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import server

MUSIC = Path(server.BASE) / "static" / "music"


class MusicListTest(unittest.TestCase):
    def setUp(self):
        self.made = []

    def tearDown(self):
        for f in self.made:
            f.unlink(missing_ok=True)

    def make(self, name, data=b"ID3fake"):
        f = MUSIC / name
        f.write_bytes(data)
        self.made.append(f)
        return f

    def test_lists_only_audio_files_sorted_and_url_encoded(self):
        self.make("b_second.ogg"); self.make("a first song.mp3"); self.make("notes.txt")
        with TestClient(server.app) as c:
            r = c.get("/api/music")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.headers["cache-control"], "no-cache")
            tracks = r.json()["tracks"]
            mine = [t for t in tracks if "first" in t or "second" in t]
            self.assertEqual(mine, ["/static/music/a%20first%20song.mp3", "/static/music/b_second.ogg"])
            self.assertFalse(any(t.endswith(".txt") for t in tracks))
            self.assertEqual(c.get("/static/music/a%20first%20song.mp3").status_code, 200)   # and the file is really served

    def test_empty_folder_gives_an_empty_list(self):
        with TestClient(server.app) as c:
            tracks = c.get("/api/music").json()["tracks"]
            self.assertEqual([t for t in tracks if t.startswith("/static/music/")], [])


if __name__ == "__main__":
    unittest.main()