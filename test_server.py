"""Integration test: two fake players play a round through the real WebSocket endpoint."""
import math
import unittest

from fastapi.testclient import TestClient

import server
import config

config.PLACING_SECONDS = 2   # keep tests fast
config.REVEAL_SECONDS = 1


def recv_until(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


class ServerTest(unittest.TestCase):
    def test_full_round(self):
        c = TestClient(server.app)
        with c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            a.send_json({"type": "create", "name": "A"})
            joined = recv_until(a, "joined")
            b.send_json({"type": "join", "code": joined["code"], "name": "B"})
            recv_until(b, "joined")
            a.send_json({"type": "start"})
            recv_until(a, "round_start"); recv_until(b, "round_start")
            # A at left shooting right, B at right shooting left -> both hit
            a.send_json({"type": "submit", "x": 100, "y": 300, "angle": 0})
            b.send_json({"type": "submit", "x": 800, "y": 300, "angle": math.pi})
            res = recv_until(a, "reveal")
            self.assertEqual(sorted(res["eliminated"]), [0, 1])
            self.assertTrue(res["game_over"])

    def test_bad_room(self):
        c = TestClient(server.app)
        with c.websocket_connect("/ws") as a:
            a.send_json({"type": "join", "code": "ZZZZ", "name": "A"})
            self.assertEqual(a.receive_json()["type"], "error")


if __name__ == "__main__":
    unittest.main()
