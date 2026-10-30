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

    def test_chat(self):
        def next_chat(ws):
            while True:
                m = recv_until(ws, "chat")
                if not m.get("system"):
                    return m

        with TestClient(server.app) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            a.send_json({"type": "create", "name": "Alice"})
            joined = recv_until(a, "joined")
            b.send_json({"type": "join", "code": joined["code"], "name": "Bob"})
            recv_until(b, "joined")
            self.assertEqual(recv_until(b, "chat_history")["type"], "chat_history")

            a.send_json({"type": "chat", "text": "  hello   <b>there</b>  "})
            m = next_chat(b)
            self.assertEqual((m["name"], m["text"]), ("Alice", "hello <b>there</b>"))

            a.send_json({"type": "chat", "text": "   "})            # empty -> ignored
            a.send_json({"type": "chat", "text": "x" * 500})        # too long -> truncated
            self.assertEqual(len(next_chat(b)["text"]), config.CHAT_MAX_LENGTH)

            for _ in range(8):                                      # spam -> rate limited
                a.send_json({"type": "chat", "text": "spam"})
            self.assertEqual(recv_until(a, "error")["message"], "You're sending messages too fast.")


if __name__ == "__main__":
    unittest.main()