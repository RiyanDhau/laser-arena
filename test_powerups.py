"""Tests for the torch / reflector power-ups (pure logic + one real WebSocket round)."""
import math
import unittest

from fastapi.testclient import TestClient

import config
import server
from game_logic import Player, bomb_victims, bomber_spots, fire_shots, powerup_count, reflect_path, trace_beam

ARENA = (0, 0, 900, 600)
R = 16


def P(pid, x, y, deg, powerup=None):
    return Player(pid, f"P{pid}", (255, 255, 255), (x, y), math.radians(deg), True, powerup)


def fire(players, **kw):
    return fire_shots(players, ARENA, R, **kw)


class PathTests(unittest.TestCase):
    def test_straight_laser_has_no_bounce(self):
        pts = reflect_path((100, 300), (1, 0), ARENA, 0)
        self.assertEqual(len(pts), 2)
        self.assertAlmostEqual(pts[1][0], 900)

    def test_bounces_stay_inside_arena_and_count(self):
        d = (math.cos(math.radians(37)), math.sin(math.radians(37)))
        pts = reflect_path((300, 200), d, ARENA, 3)
        self.assertEqual(len(pts), 5)                      # start + 4 wall hits (3 bounces)
        for x, y in pts:
            self.assertTrue(-1e-6 <= x <= 900 + 1e-6 and -1e-6 <= y <= 600 + 1e-6)


class TorchTests(unittest.TestCase):
    def test_cone_hits_inside_and_misses_outside(self):
        a = P(0, 100, 300, 0, "torch")
        inside = P(1, 700, 300 + 600 * math.tan(math.radians(10)), -90)   # targets shoot away from each other
        outside = P(2, 700, 300 + 600 * math.tan(math.radians(25)), 90)
        behind = P(3, 50, 300, 90)
        _, dead = fire([a, inside, outside, behind])
        self.assertEqual(dead, {1})

    def test_torch_pierces_everyone_in_the_cone(self):
        a = P(0, 100, 300, 0, "torch")
        _, dead = fire([a, P(1, 300, 300, 90), P(2, 600, 310, 90)])
        self.assertEqual(dead, {1, 2})
        plain = P(0, 100, 300, 0)
        _, dead = fire([plain, P(1, 300, 300, 90), P(2, 600, 310, 90)])
        self.assertEqual(dead, {1})                        # normal laser stops at the first hit


class ReflectorTests(unittest.TestCase):
    def test_reflected_beam_reaches_target_a_plain_laser_misses(self):
        target = P(1, 700, 200, 0)
        _, dead = fire([P(0, 200, 300, -45), target])
        self.assertEqual(dead, set())
        shots, dead = fire([P(0, 200, 300, -45, "reflector"), target], bounces=3)
        self.assertEqual(dead, {1})
        self.assertEqual(shots[0]["kind"], "reflector")

    def test_beam_can_come_back_and_hit_its_shooter(self):
        a = P(0, 450, 300, 0, "reflector")
        behind = P(1, 100, 300, 90)                        # further along the returning beam
        _, dead = fire([a, behind], bounces=3, hits_self=True)
        self.assertEqual(dead, {0})                        # shooter is hit first, beam stops there
        _, dead = fire([a, behind], bounces=3, hits_self=False)
        self.assertEqual(dead, {1})


class PickupTests(unittest.TestCase):
    def test_closest_player_wins_a_contested_powerup(self):
        room = server.Room("TEST")
        room.powerups = [{"id": 0, "type": "torch", "x": 400, "y": 300}]
        a, b, c = P(0, 410, 300, 0), P(1, 395, 300, 0), P(2, 500, 300, 0)
        picks = room._assign_powerups([a, b, c])
        self.assertEqual(picks, [{"pid": 1, "type": "torch", "id": 0}])
        self.assertEqual((a.powerup, b.powerup, c.powerup), (None, "torch", None))

    def test_no_powerups_before_start_round(self):
        room = server.Room("TEST")
        room.round = config.POWERUP_START_ROUND - 1
        room._spawn_powerups(4)
        self.assertEqual(room.powerups, [])
        room.round = config.POWERUP_START_ROUND
        room._spawn_powerups(4)
        self.assertEqual(len(room.powerups), 1)
        for pu in room.powerups:
            self.assertTrue(config.POWERUP_MARGIN <= pu["x"] <= 900 - config.POWERUP_MARGIN)


class CountTests(unittest.TestCase):
    def test_tiers(self):
        got = {r: powerup_count(r) for r in (1, 3, 4, 10, 11, 20, 21, 30, 31, 45)}
        self.assertEqual(got, {1: 0, 3: 0, 4: 1, 10: 1, 11: 2, 20: 2, 21: 3, 30: 3, 31: 4, 45: 5})

    def test_cap(self):
        self.assertEqual(powerup_count(200, cap=10), 10)

    def test_room_spawns_the_tier_amount(self):
        room = server.Room("TEST")
        for rnd, expected in ((4, 1), (10, 1), (11, 2), (25, 3)):
            room.round = rnd
            room._spawn_powerups()
            normal = [p for p in room.powerups if p["type"] != "bomber"]
            self.assertEqual(len(normal), expected, rnd)          # the bomber is an EXTRA icon on top of these


class BomberTests(unittest.TestCase):
    def test_bomber_is_always_in_a_corner_or_the_centre(self):
        spots = bomber_spots((0, 0, 900, 600), 50)
        self.assertEqual(sorted(spots), sorted([(50, 50), (850, 50), (50, 550), (850, 550), (450, 300)]))
        room = server.Room("TEST")
        seen = set()
        for i in range(300):
            room.round = 10 * (1 + i % 6)
            room._spawn_powerups()
            for pu in room.powerups:
                if pu["type"] == "bomber":
                    self.assertIn((pu["x"], pu["y"]), spots)
                    seen.add((pu["x"], pu["y"]))
            others = [p for p in room.powerups if p["type"] != "bomber"]
            for o in others:                                       # random icons keep their distance from the bomber
                self.assertGreaterEqual(math.dist((o["x"], o["y"]), next((p["x"], p["y"]) for p in room.powerups if p["type"] == "bomber")),
                                        config.POWERUP_MIN_GAP - 1e-6)
        self.assertEqual(seen, set(spots))                         # all five places get used

    def test_bomber_spawns_once_every_10_rounds_only(self):
        room = server.Room("TEST")
        for rnd in range(1, 62):
            room.round = rnd
            room._spawn_powerups()
            bombers = [p for p in room.powerups if p["type"] == "bomber"]
            self.assertEqual(len(bombers), 1 if rnd % 10 == 0 else 0, rnd)

    def test_bomb_blast_touches_everyone_but_the_owner(self):
        a, b, c, d = P(0, 300, 300, 0), P(1, 380, 300, 0), P(2, 400, 300, 0), P(3, 700, 300, 0)
        # radius 90 + player radius 16 = 106 reach around the bomb at (300, 300)
        self.assertEqual(sorted(bomb_victims((300, 300), 90, [a, b, c, d], R, 0)), [1, 2])
        self.assertEqual(bomb_victims((300, 300), 90, [a, b, c, d], R, 1), [0, 2])   # owner never hit
        edge = P(4, 300 + 90 + R, 300, 0)
        self.assertEqual(bomb_victims((300, 300), 90, [edge], R, 9), [4])             # touching the blast counts


class DeflectorTests(unittest.TestCase):
    def test_beam_turns_90_degrees_where_it_touches(self):
        for turn, wall_y in ((1, 600), (-1, 0)):               # +1 = clockwise on screen (right -> down)
            pts, events = trace_beam((100, 300), (1, 0), ARENA, 0, [(7, (400, 300), turn)], 24)
            self.assertEqual(len(pts), 3)
            self.assertAlmostEqual(pts[1][0], 376)               # bent at the icon's edge
            self.assertAlmostEqual(pts[2][1], wall_y)
            self.assertEqual(events[0][1], 7)

    def test_a_beam_is_bent_only_once_and_not_if_it_starts_inside(self):
        pts, events = trace_beam((100, 300), (1, 0), ARENA, 3, [(1, (400, 300), 1)], 24)
        self.assertEqual(len(events), 1)                         # the reflector beam passes it again, unbent
        pts, events = trace_beam((400, 300), (1, 0), ARENA, 0, [(1, (400, 300), 1)], 24)
        self.assertEqual(events, [])

    def test_bent_beam_hits_a_player_a_straight_one_would_miss(self):
        target = P(1, 376, 500, 90)
        dfl = [(5, (400, 300), 1)]
        _, dead = fire([P(0, 100, 300, 0), target])
        self.assertEqual(dead, set())
        shots, dead = fire([P(0, 100, 300, 0), target], deflectors=dfl, deflector_radius=24)
        self.assertEqual(dead, {1})
        self.assertEqual(shots[0]["deflected"], [5])

    def test_no_deflection_if_the_beam_stops_before_reaching_it(self):
        shots, dead = fire([P(0, 100, 300, 0), P(1, 250, 300, 90)], deflectors=[(5, (400, 300), 1)], deflector_radius=24)
        self.assertEqual((dead, shots[0]["deflected"]), ({1}, []))

    def test_torch_cone_ignores_deflectors(self):
        shots, dead = fire([P(0, 100, 300, 0, "torch"), P(1, 600, 300, 90)], deflectors=[(5, (400, 300), 1)], deflector_radius=24)
        self.assertEqual((dead, shots[0]["deflected"]), ({1}, []))

    def test_deflector_only_spawns_after_round_10(self):
        room = server.Room("TEST")
        room.round = 10
        for _ in range(60):
            room._spawn_powerups()
            self.assertTrue(all(p["type"] != "deflector" for p in room.powerups))
        room.round = 11
        seen = []
        for _ in range(200):
            room._spawn_powerups()
            seen += [p for p in room.powerups if p["type"] == "deflector"]
        self.assertTrue(seen)
        self.assertTrue(all(p["turn"] in (1, -1) for p in seen))
        picks = server.Room("T2")
        picks.powerups = [{"id": 0, "type": "deflector", "x": 400, "y": 300, "turn": 1}]
        self.assertEqual(picks._assign_powerups([P(0, 400, 300, 0)]), [])   # can't be picked up


class MarkerTests(unittest.TestCase):
    def test_beam_and_cone_report_touched_markers(self):
        markers = [(0, (300, 200)), (1, (300, 400))]
        shots, _ = fire([P(0, 100, 200, 0)], markers=markers, marker_radius=30)
        self.assertEqual(shots[0]["touched"], [0])                # straight laser passes icon 0 only
        shots, _ = fire([P(0, 100, 200, 0, "reflector")], markers=markers, marker_radius=30, bounces=3)
        self.assertIn(0, shots[0]["touched"])
        near = [(0, (300, 250)), (1, (300, 350)), (2, (300, 560))]
        shots, _ = fire([P(0, 100, 300, 0, "torch")], markers=near, marker_radius=30)
        self.assertEqual(sorted(shots[0]["touched"]), [0, 1])     # the wide cone reaches both, not the far one
        shots, _ = fire([P(0, 100, 200, 0), P(1, 250, 200, 90)], markers=markers, marker_radius=30)
        self.assertEqual(shots[0]["touched"], [])                 # beam stops at the player before the icon


def recv_until(ws, kind):
    while True:
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg


FIXED = [{"id": 0, "type": "torch", "x": 300, "y": 200}]


class RoundTest(unittest.TestCase):
    """Real WebSocket rounds with ONE known hidden power-up (a torch at 300,200)."""

    def setUp(self):
        self.saved = (config.PLACING_SECONDS, config.REVEAL_SECONDS, server.Room._spawn_powerups)
        config.PLACING_SECONDS, config.REVEAL_SECONDS = 3, 1
        self.fixed = FIXED
        server.Room._spawn_powerups = lambda room, n=0: setattr(room, "powerups", [dict(p) for p in self.fixed])

    def tearDown(self):
        config.PLACING_SECONDS, config.REVEAL_SECONDS, server.Room._spawn_powerups = self.saved

    def play_round(self, a_move, b_move):
        with TestClient(server.app) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            a.send_json({"type": "create", "name": "A"})
            code = recv_until(a, "joined")["code"]
            b.send_json({"type": "join", "code": code, "name": "B"})
            recv_until(b, "joined")
            a.send_json({"type": "start"})
            rs = recv_until(a, "round_start")
            recv_until(b, "round_start")
            a.send_json({"type": "submit", **a_move})
            b.send_json({"type": "submit", **b_move})
            return rs, recv_until(a, "reveal")

    def play_rounds(self, moves):
        """moves = [(a_move, b_move), ...] -> list of (round_start, reveal) for each round."""
        out = []
        with TestClient(server.app) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            a.send_json({"type": "create", "name": "A"})
            code = recv_until(a, "joined")["code"]
            b.send_json({"type": "join", "code": code, "name": "B"})
            recv_until(b, "joined")
            a.send_json({"type": "start"})
            for a_move, b_move in moves:
                rs = recv_until(a, "round_start")
                recv_until(b, "round_start")
                a.send_json({"type": "submit", **a_move})
                b.send_json({"type": "submit", **b_move})
                out.append((rs, recv_until(a, "reveal")))
        return out

    def test_a_shield_does_not_protect_the_round_it_is_earned(self):
        # A's beam runs through the icon (earning a shield) while B shoots A: A is NOT protected yet.
        angle_b = math.atan2(200 - 350, 100 - 700)
        (rs, res), = self.play_rounds([({"x": 100, "y": 200, "angle": 0}, {"x": 700, "y": 350, "angle": angle_b})])
        self.assertEqual(res["shield_gained"], [0])
        self.assertEqual((res["active_shields"], res["blocked"]), ([], []))
        self.assertEqual(res["eliminated"], [0])

    def test_a_shield_protects_the_next_round_only(self):
        harmless_b = {"x": 800, "y": 560, "angle": -math.pi / 2}
        angle_b = math.atan2(560 - 350, 100 - 700)                    # B's beam runs at A's spot but misses the icon
        rounds = self.play_rounds([
            ({"x": 100, "y": 200, "angle": 0}, harmless_b),                                              # round 1: A earns a shield
            ({"x": 100, "y": 560, "angle": -math.pi / 2}, {"x": 700, "y": 350, "angle": angle_b}),       # round 2: B shoots A
            ({"x": 100, "y": 560, "angle": -math.pi / 2}, {"x": 700, "y": 350, "angle": angle_b}),       # round 3: shield is gone
        ])
        (rs1, r1), (rs2, r2), (rs3, r3) = rounds
        self.assertEqual((r1["shield_gained"], r1["shields"], r1["eliminated"]), ([0], [0], []))
        self.assertEqual(rs2["shields"], [0])                         # active during round 2
        self.assertEqual((r2["active_shields"], r2["blocked"], r2["eliminated"]), ([0], [0], []))
        self.assertEqual(rs3["shields"], [])                          # expired after one round (even though it was used)
        self.assertEqual(r3["eliminated"], [0])                       # so the same hit now eliminates A

    def test_an_unused_shield_also_expires_after_the_next_round(self):
        quiet_a = {"x": 100, "y": 560, "angle": -math.pi / 2}
        quiet_b = {"x": 800, "y": 560, "angle": -math.pi / 2}
        rounds = self.play_rounds([({"x": 100, "y": 200, "angle": 0}, quiet_b), (quiet_a, quiet_b), (quiet_a, quiet_b)])
        self.assertEqual([rs["shields"] for rs, _ in rounds], [[], [0], []])

    def test_a_shield_absorbs_a_bomb_the_round_after_it_is_earned(self):
        self.fixed = [{"id": 0, "type": "bomber", "x": 400, "y": 300}, {"id": 1, "type": "torch", "x": 700, "y": 200}]
        a_found_bomber = {"x": 410, "y": 300, "angle": math.pi / 2}
        b_earns_shield = {"x": 550, "y": 200, "angle": 0}              # beam runs through the torch icon at (700,200)
        a_bombs_b = {"x": 100, "y": 560, "angle": -math.pi / 2, "bomb": [550, 200]}
        (rs1, r1), (rs2, r2) = self.play_rounds([(a_found_bomber, b_earns_shield), (a_bombs_b, b_earns_shield)])
        self.assertEqual((r1["pickups"][0]["type"], r1["shield_gained"], r1["eliminated"]), ("bomber", [1], []))
        self.assertEqual((rs2["bombers"], rs2["shields"]), ([0], [1]))
        self.assertEqual((r2["bombed"], r2["blocked"], r2["eliminated"]), ([1], [1], []))

    def test_joined_message_carries_the_rulebook_numbers(self):
        with TestClient(server.app) as c, c.websocket_connect("/ws") as a:
            a.send_json({"type": "create", "name": "A"})
            rules = recv_until(a, "joined")["rules"]
            self.assertEqual(rules["bomb_radius"], config.BOMB_RADIUS)
            self.assertEqual(rules["bomber_every"], config.BOMBER_EVERY)
            self.assertEqual(rules["torch_half_deg"], config.TORCH_HALF_ANGLE)

    def test_positions_are_never_sent_before_the_reveal(self):
        rs, res = self.play_round({"x": 100, "y": 500, "angle": 0}, {"x": 800, "y": 560, "angle": 3})
        self.assertNotIn("powerups", rs)
        self.assertEqual(rs["hidden_powerups"], 1)                # players only learn how many
        self.assertEqual(res["powerups"], FIXED)                  # ...and see where after the shot

    def test_standing_on_it_activates_it(self):
        _, res = self.play_round({"x": 320, "y": 210, "angle": 0}, {"x": 800, "y": 560, "angle": -math.pi / 2})
        self.assertEqual(res["pickups"], [{"pid": 0, "type": "torch", "id": 0}])
        self.assertEqual(next(s for s in res["shots"] if s["pid"] == 0)["kind"], "torch")
        self.assertEqual(res["shield_gained"], [])                # your own icon does not also give a shield



    def test_bomber_gives_a_secret_bomb_for_the_next_round(self):
        self.fixed = [{"id": 0, "type": "bomber", "x": 400, "y": 300}]
        with TestClient(server.app) as c, c.websocket_connect("/ws") as a, c.websocket_connect("/ws") as b:
            a.send_json({"type": "create", "name": "A"})
            code = recv_until(a, "joined")["code"]
            b.send_json({"type": "join", "code": code, "name": "B"})
            recv_until(b, "joined")
            a.send_json({"type": "start"})
            r1 = recv_until(a, "round_start"); recv_until(b, "round_start")
            self.assertEqual(r1["bombers"], [])
            # round 1: A stands on the (hidden) bomber icon, nobody is hit
            a.send_json({"type": "submit", "x": 410, "y": 300, "angle": 0, "bomb": [700, 520]})   # no bomb yet: ignored
            b.send_json({"type": "submit", "x": 700, "y": 520, "angle": -math.pi / 2})
            res = recv_until(a, "reveal")
            self.assertEqual(res["pickups"], [{"pid": 0, "type": "bomber", "id": 0}])
            self.assertEqual((res["bombs"], res["eliminated"]), ([], []))
            # round 2: A (and only A) holds a bomb
            r2 = recv_until(a, "round_start"); recv_until(b, "round_start")
            self.assertEqual((r2["bombers"], r2["bomb_radius"]), ([0], config.BOMB_RADIUS))
            a.send_json({"type": "submit", "x": 100, "y": 500, "angle": -math.pi / 2, "bomb": [700, 500]})
            b.send_json({"type": "submit", "x": 700, "y": 500, "angle": -math.pi / 2, "bomb": [100, 500]})  # B has no bomb: ignored
            res = recv_until(a, "reveal")
            self.assertEqual(res["eliminated"], [1])                  # B was inside A's hidden blast; A was not hit
            self.assertEqual(res["bombed"], [1])
            self.assertEqual(res["bombs"], [{"pid": 0, "x": 700.0, "y": 500.0, "radius": config.BOMB_RADIUS, "hit": [1]}])
            self.assertTrue(res["game_over"])


    def test_deflector_bends_the_beam_and_is_revealed(self):
        self.fixed = [{"id": 0, "type": "deflector", "x": 400, "y": 300, "turn": 1}]
        _, res = self.play_round({"x": 100, "y": 300, "angle": 0}, {"x": 376, "y": 500, "angle": math.pi / 2})
        self.assertEqual(res["eliminated"], [1])                  # A's beam turned down and hit B
        self.assertEqual(res["deflected"], [0])
        self.assertEqual(res["shield_gained"], [])                # a deflector is not a shield source
        shot = next(s for s in res["shots"] if s["pid"] == 0)
        self.assertEqual(shot["deflected"], [0])
        self.assertEqual(res["powerups"][0]["turn"], 1)


if __name__ == "__main__":
    unittest.main()