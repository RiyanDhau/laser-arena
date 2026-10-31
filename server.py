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
from game_logic import Player, bomb_victims, bomber_spots, can_place, fire_shots, powerup_count, resolve_round

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
        self.powerups: list = []        # power-ups lying on the field this round
        self.bombers: set = set()       # players who hold a bomb THIS round
        self.next_bombers: set = set()  # players who found the bomber this round -> bomb next round
        self.bomb_pos: dict = {}        # pid -> (x, y) where they secretly dropped their bomb

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
            m.player.shield = False
        self.round = 0
        self.bombers, self.next_bombers, self.bomb_pos = set(), set(), {}
        await self.start_round()

    def _spawn_powerups(self, n_living: int = 0):
        """Hide power-ups in the arena. How many depends on the round (see game_logic.powerup_count);
        clients are NOT told where they are until the round is fired."""
        self.powerups = []
        if not C.POWERUPS_ENABLED:
            return
        count = powerup_count(self.round, C.POWERUP_START_ROUND, C.POWERUP_TIER_END,
                              C.POWERUP_TIER_SIZE, C.POWERUP_MAX_PER_ROUND)
        types = list(C.POWERUP_TYPES)
        if self.round >= C.DEFLECTOR_START_ROUND:
            types.append("deflector")                       # joins the random mix after round 10
        x, y, w, h = C.ARENA_RECT
        m = C.POWERUP_MARGIN

        def place(kind=None):
            for _try in range(80):
                px, py = random.uniform(x + m, x + w - m), random.uniform(y + m, y + h - m)
                if all(math.dist((px, py), (q["x"], q["y"])) >= C.POWERUP_MIN_GAP for q in self.powerups):
                    pu = {"id": len(self.powerups), "type": kind or random.choice(types), "x": round(px, 1), "y": round(py, 1)}
                    if pu["type"] == "deflector":
                        pu["turn"] = random.choice([1, -1])  # +1 bends beams clockwise (on screen), -1 counter-clockwise
                    self.powerups.append(pu)
                    return

        if C.BOMBER_EVERY and self.round > 0 and self.round % C.BOMBER_EVERY == 0:
            bx, by = random.choice(bomber_spots(C.ARENA_RECT, C.BOMBER_SPOT_INSET))   # a corner or the centre
            self.powerups.append({"id": 0, "type": "bomber", "x": bx, "y": by})        # extra icon on rounds 10, 20, 30 ...
        for _ in range(count):
            place()

    def _assign_powerups(self, placed):
        """Whoever stands on a power-up gets it; if several do, the closest one wins (ties: lowest id).
        Resolved only at fire time, so nobody learns where rivals stood."""
        picks = []
        for pu in self.powerups:
            if pu["type"] == "deflector":
                continue                                    # deflectors cannot be picked up
            near = sorted((math.dist(p.pos, (pu["x"], pu["y"])), p.pid, p) for p in placed
                          if p.powerup is None and math.dist(p.pos, (pu["x"], pu["y"])) <= C.POWERUP_PICKUP_RADIUS)
            if near:
                winner = near[0][2]
                winner.powerup = pu["type"]
                picks.append({"pid": winner.pid, "type": pu["type"], "id": pu["id"]})
        return picks

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
            m.player.powerup = None
        self._spawn_powerups(len(living))
        # whoever found the bomber last round may drop a bomb now (if still alive)
        self.bombers = {pid for pid in self.next_bombers if pid in self.members and self.members[pid].player.alive}
        self.next_bombers = set()
        self.bomb_pos = {}
        await self.broadcast({"type": "round_start", "round": self.round,
                              "seconds": C.PLACING_SECONDS, "alive": [m.pid for m in living],
                              "hidden_powerups": len(self.powerups),       # only how many, never where
                              "shields": [m.pid for m in living if m.player.shield],
                              "bombers": sorted(self.bombers), "bomb_radius": C.BOMB_RADIUS})
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
        bomb = msg.get("bomb")                    # only bomb holders may send one; it stays secret until the reveal
        if member.pid in self.bombers and isinstance(bomb, (list, tuple)) and len(bomb) == 2:
            try:
                bx, by = float(bomb[0]), float(bomb[1])
                if math.isfinite(bx) and math.isfinite(by) and can_place((bx, by), [], C.ARENA_RECT, 0):
                    self.bomb_pos[member.pid] = (bx, by)
            except (TypeError, ValueError):
                pass
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
        pickups = self._assign_powerups(placed)
        self.next_bombers |= {k["pid"] for k in pickups if k["type"] == "bomber"}   # they bomb NEXT round
        markers = [(pu["id"], (pu["x"], pu["y"])) for pu in self.powerups if pu["type"] != "deflector"]
        deflectors = [(pu["id"], (pu["x"], pu["y"]), pu["turn"]) for pu in self.powerups if pu["type"] == "deflector"]
        shots, hit = fire_shots(placed, C.ARENA_RECT, C.PLAYER_RADIUS, C.LASER_PIERCES,
                                C.TORCH_HALF_ANGLE, C.REFLECTOR_BOUNCES, C.REFLECTOR_HITS_SELF,
                                markers, C.POWERUP_BEAM_RADIUS, deflectors, C.DEFLECTOR_RADIUS)
        # Bombs explode together with the lasers; they are hits like any other (so a shield can absorb them).
        bombs, bombed = [], set()
        for pid, (bx, by) in self.bomb_pos.items():
            if pid in self.bombers and any(p.pid == pid for p in placed):
                caught = bomb_victims((bx, by), C.BOMB_RADIUS, placed, C.PLAYER_RADIUS, pid)
                bombs.append({"pid": pid, "x": bx, "y": by, "radius": C.BOMB_RADIUS, "hit": caught})
                bombed.update(caught)
        hit = set(hit) | bombed
        self.bombers = set()
        # Shield: a beam that touches a power-up icon (other than the one its shooter stands on) earns one,
        # which protects its owner during the NEXT round only.
        own = {k["pid"]: k["id"] for k in pickups}
        gained = sorted({sh["pid"] for sh in shots if any(mid != own.get(sh["pid"]) for mid in sh["touched"])})
        # A shield that is active NOW (earned last round) absorbs everything that hits its owner this round.
        active = sorted(p.pid for p in placed if p.shield)
        blocked = sorted(pid for pid in hit if self.members[pid].player.shield)
        killed = set(hit) - set(blocked)
        for p in placed:                                  # every shield lasts exactly one round, used or not
            p.shield = False
        for pid in gained:                                # shields earned this round start protecting next round
            self.members[pid].player.shield = True
        eliminated = killed | timed_out | {m.pid for m in living if not m.connected}
        for pid in eliminated:
            self.members[pid].player.alive = False
        over = len(self.living()) <= 1
        shields = [m.pid for m in self.members.values() if m.player.alive and m.player.shield]
        await self.broadcast({
            "type": "reveal", "round": self.round, "next_in": C.REVEAL_SECONDS,
            "players": [{"pid": p.pid, "x": p.pos[0], "y": p.pos[1], "angle": p.angle} for p in placed],
            "shots": shots, "powerups": self.powerups, "pickups": pickups,      # hidden info is revealed now
            "pickup_radius": C.POWERUP_PICKUP_RADIUS, "deflector_radius": C.DEFLECTOR_RADIUS,
            "deflected": sorted(sh["pid"] for sh in shots if sh["deflected"]),
            "shield_gained": gained, "active_shields": active, "blocked": blocked, "shields": shields,
            "bombs": bombs, "bombed": sorted(bombed),
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
            m.player.shield = False
        self.phase = "lobby"
        self.bombers, self.next_bombers, self.bomb_pos = set(), set(), {}
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


def rules_payload() -> dict:
    """Numbers shown in the in-game rulebook, so it always matches config.py."""
    return {
        "min_players": C.MIN_PLAYERS, "max_players": C.MAX_PLAYERS, "placing_seconds": C.PLACING_SECONDS,
        "laser_pierces": C.LASER_PIERCES, "powerups_enabled": C.POWERUPS_ENABLED,
        "powerup_start": C.POWERUP_START_ROUND, "tier_end": C.POWERUP_TIER_END, "tier_size": C.POWERUP_TIER_SIZE,
        "pickup_radius": C.POWERUP_PICKUP_RADIUS, "torch_half_deg": C.TORCH_HALF_ANGLE,
        "bounces": C.REFLECTOR_BOUNCES, "deflector_start": C.DEFLECTOR_START_ROUND,
        "bomber_every": C.BOMBER_EVERY, "bomb_radius": C.BOMB_RADIUS,
    }


def new_code() -> str:
    while True:
        code = "".join(random.choice(CODE_CHARS) for _ in range(4))
        if code not in rooms:
            return code


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})   # always fetch the latest page


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
                                    "radius": C.PLAYER_RADIUS, "rules": rules_payload()})
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