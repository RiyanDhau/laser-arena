"""Laser Arena Online - FastAPI + WebSocket server.

The server is authoritative: it keeps every player's placement secret until all living
players have locked in, then resolves the round with game_logic.resolve_round and
broadcasts the result.

Run:  uvicorn server:app --host 0.0.0.0 --port 8000
"""
import asyncio
import math
import re
import random
import string
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config as C
from game_logic import Player, can_place, resolve_round

BASE = Path(__file__).parent
app = FastAPI(title="Laser Arena Online")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

rooms: Dict[str, "Room"] = {}
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def hex_color(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


class Member:
    def __init__(self, pid: int, name: str, color, ws: WebSocket):
        self.pid = pid
        self.ws = ws
        self.connected = True
        self.player = Player(pid=pid, name=name, color=color)
        self.chat_times = deque()       # timestamps of recent chat messages (rate limiting)


class Room:
    def __init__(self, code: str):
        self.code = code
        self.members: Dict[int, Member] = {}
        self.next_pid = 0
        self.host: Optional[int] = None
        self.phase = "lobby"            # lobby | placing | reveal | gameover
        self.round = 0
        self.submitted = set()
        self.task: Optional[asyncio.Task] = None
        self.chat_log: list = []        # last few chat messages, replayed to new joiners

    # ----- helpers -----
    def living(self):
        return [m for m in self.members.values() if m.player.alive]

    def add(self, name: str, ws: WebSocket) -> Member:
        pid = self.next_pid
        self.next_pid += 1
        used = {m.player.color for m in self.members.values()}
        color = next((c for c in C.PLAYER_COLORS if c not in used), C.PLAYER_COLORS[pid % len(C.PLAYER_COLORS)])
        m = Member(pid, (name or "").strip()[:16] or f"Player {pid + 1}", color, ws)
        self.members[pid] = m
        if self.host is None:
            self.host = pid
        return m

    async def send(self, member: Member, data: dict):
        if not member.connected:
            return
        try:
            await member.ws.send_json(data)
        except Exception:
            pass

    async def broadcast(self, data: dict):
        await asyncio.gather(*(self.send(m, data) for m in list(self.members.values())))

    def lobby_payload(self, show: bool):
        return {
            "type": "lobby", "show": show, "code": self.code, "host": self.host,
            "players": [{"pid": m.pid, "name": m.player.name, "color": hex_color(m.player.color),
                         "alive": m.player.alive} for m in self.members.values()],
        }

    def _schedule(self, coro):
        old = self.task
        if old and old is not asyncio.current_task():
            old.cancel()
        self.task = asyncio.create_task(coro)

    def _cancel(self):
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
        self.task = None

    # ----- chat -----
    async def _post(self, msg: dict):
        self.chat_log.append(msg)
        del self.chat_log[:-C.CHAT_HISTORY]
        await self.broadcast(msg)

    async def system_message(self, text: str):
        await self._post({"type": "chat", "system": True, "text": text, "ts": time.time()})

    async def chat(self, member: Member, text):
        """Anyone in the room (including eliminated players) can chat in any phase."""
        if not isinstance(text, str):
            return
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)            # strip control characters
        text = " ".join(text.split())[:C.CHAT_MAX_LENGTH]
        if not text:
            return
        limit, window = C.CHAT_RATE_LIMIT
        now = time.monotonic()
        times = member.chat_times
        while times and now - times[0] > window:
            times.popleft()
        if len(times) >= limit:
            await self.send(member, {"type": "error", "message": "You're sending messages too fast."})
            return
        times.append(now)
        await self._post({"type": "chat", "pid": member.pid, "name": member.player.name,
                          "color": hex_color(member.player.color), "text": text, "ts": time.time()})

    # ----- game flow -----
    async def start(self, member: Member):
        if member.pid != self.host or self.phase != "lobby":
            return
        if len(self.members) < C.MIN_PLAYERS:
            await self.send(member, {"type": "error", "message": f"Need at least {C.MIN_PLAYERS} players."})
            return
        for m in self.members.values():
            m.player.alive = True
        self.round = 0
        await self.start_round()

    async def start_round(self):
        for m in self.members.values():
            if not m.connected:
                m.player.alive = False
        living = self.living()
        if len(living) <= 1:
            await self.end_game()
            return
        self.round += 1
        self.phase = "placing"
        self.submitted = set()
        for m in living:
            m.player.pos = None
            m.player.angle = None
        await self.broadcast({"type": "round_start", "round": self.round,
                              "seconds": C.PLACING_SECONDS, "alive": [m.pid for m in living]})
        self._schedule(self._placing_timeout())

    async def _placing_timeout(self):
        await asyncio.sleep(C.PLACING_SECONDS + 1)
        if self.phase == "placing":
            await self.resolve()

    async def submit(self, member: Member, msg: dict):
        if self.phase != "placing" or not member.player.alive or member.pid in self.submitted:
            return
        try:
            x, y, a = float(msg["x"]), float(msg["y"]), float(msg["angle"])
        except (KeyError, TypeError, ValueError):
            return
        if not all(math.isfinite(v) for v in (x, y, a)):
            return
        if not can_place((x, y), [], C.ARENA_RECT, C.PLAYER_RADIUS):
            await self.send(member, {"type": "error", "message": "Invalid position."})
            return
        member.player.pos = (x, y)
        member.player.angle = a
        self.submitted.add(member.pid)
        await self.broadcast({"type": "submitted", "pids": sorted(self.submitted)})
        await self._check_all_submitted()

    async def _check_all_submitted(self):
        if self.phase == "placing" and all(m.pid in self.submitted for m in self.living()):
            await self.resolve()

    async def resolve(self):
        if self.phase != "placing":
            return
        self.phase = "reveal"
        living = self.living()
        placed = [m.player for m in living if m.player.pos is not None]
        timed_out = {m.pid for m in living if m.player.pos is None}
        lasers, hit = resolve_round(placed, C.ARENA_RECT, C.PLAYER_RADIUS, C.LASER_PIERCES)
        eliminated = set(hit) | timed_out | {m.pid for m in living if not m.connected}
        for pid in eliminated:
            self.members[pid].player.alive = False
        over = len(self.living()) <= 1
        await self.broadcast({
            "type": "reveal", "round": self.round, "next_in": C.REVEAL_SECONDS,
            "players": [{"pid": p.pid, "x": p.pos[0], "y": p.pos[1], "angle": p.angle} for p in placed],
            "lasers": [{"pid": pid, "x0": a[0], "y0": a[1], "x1": b[0], "y1": b[1]}
                       for pid, (a, b) in lasers.items()],
            "eliminated": sorted(eliminated), "timed_out": sorted(timed_out), "game_over": over,
        })
        self._schedule(self._after_reveal(over))

    async def _after_reveal(self, over: bool):
        await asyncio.sleep(C.REVEAL_SECONDS)
        if over:
            await self.end_game()
        else:
            await self.start_round()

    async def end_game(self):
        self.phase = "gameover"
        living = self.living()
        winner = living[0] if len(living) == 1 else None
        await self.broadcast({"type": "gameover",
                              "winner": winner.pid if winner else None,
                              "name": winner.player.name if winner else None})

    async def restart(self, member: Member):
        if member.pid != self.host or self.phase != "gameover":
            return
        for pid in [p for p, m in self.members.items() if not m.connected]:
            del self.members[pid]
        for m in self.members.values():
            m.player.alive = True
        self.phase = "lobby"
        await self.broadcast(self.lobby_payload(show=True))

    async def on_disconnect(self, member: Member):
        member.connected = False
        await self.system_message(f"{member.player.name} left")
        if self.phase in ("lobby", "gameover"):
            self.members.pop(member.pid, None)
            if not self.members:
                self._cancel()
                rooms.pop(self.code, None)
                return
            if self.host == member.pid:
                self.host = next(iter(self.members))
            await self.broadcast(self.lobby_payload(show=False))
        elif self.phase == "placing" and member.player.alive and member.pid not in self.submitted:
            member.player.alive = False
            await self.broadcast({"type": "submitted", "pids": sorted(self.submitted)})
            await self._check_all_submitted()
        # if already submitted, their laser still fires this round; they are removed afterwards.


def new_code() -> str:
    while True:
        code = "".join(random.choice(CODE_CHARS) for _ in range(4))
        if code not in rooms:
            return code


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    room: Optional[Room] = None
    member: Optional[Member] = None
    try:
        while True:
            try:
                msg = await ws.receive_json()
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            t = msg.get("type")
            if room is None:
                if t == "create":
                    room = Room(new_code())
                    rooms[room.code] = room
                elif t == "join":
                    r = rooms.get(str(msg.get("code", "")).strip().upper())
                    if r is None:
                        await ws.send_json({"type": "error", "message": "Room not found."})
                        continue
                    if r.phase != "lobby":
                        await ws.send_json({"type": "error", "message": "That game already started."})
                        continue
                    if len(r.members) >= C.MAX_PLAYERS:
                        await ws.send_json({"type": "error", "message": "Room is full."})
                        continue
                    room = r
                else:
                    continue
                member = room.add(str(msg.get("name", "")), ws)
                await ws.send_json({"type": "joined", "pid": member.pid, "code": room.code,
                                    "arena": [C.ARENA_RECT[2], C.ARENA_RECT[3]],
                                    "radius": C.PLAYER_RADIUS})
                await ws.send_json({"type": "chat_history", "messages": room.chat_log})
                await room.broadcast(room.lobby_payload(show=True))
                await room.system_message(f"{member.player.name} joined")
            elif t == "chat":
                await room.chat(member, msg.get("text"))
            elif t == "start":
                await room.start(member)
            elif t == "submit":
                await room.submit(member, msg)
            elif t == "restart":
                await room.restart(member)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        if room and member:
            await room.on_disconnect(member)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000)