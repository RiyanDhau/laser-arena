"""Pure game logic (no pygame) so it can be unit-tested and reused."""
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

Vec = Tuple[float, float]


@dataclass
class Player:
    pid: int
    name: str
    color: Tuple[int, int, int]
    pos: Optional[Vec] = None
    angle: Optional[float] = None     # radians; 0 = right, pi/2 = down (screen coords)
    alive: bool = True
    powerup: Optional[str] = None     # "torch" / "reflector" for the current round, else None
    shield: bool = False              # absorbs the next hit that would eliminate this player


def direction(angle: float) -> Vec:
    return math.cos(angle), math.sin(angle)


def distance_to_wall(origin: Vec, d: Vec, arena: Tuple[float, float, float, float]) -> float:
    """Distance from origin (inside arena) along unit vector d to the arena edge."""
    x, y, w, h = arena
    ts = []
    if d[0] > 1e-9:
        ts.append((x + w - origin[0]) / d[0])
    elif d[0] < -1e-9:
        ts.append((x - origin[0]) / d[0])
    if d[1] > 1e-9:
        ts.append((y + h - origin[1]) / d[1])
    elif d[1] < -1e-9:
        ts.append((y - origin[1]) / d[1])
    return min(ts)


def ray_hits_circle(origin: Vec, d: Vec, center: Vec, radius: float) -> Optional[float]:
    """Return distance along the ray where it reaches the circle's closest approach, or None."""
    vx, vy = center[0] - origin[0], center[1] - origin[1]
    t = vx * d[0] + vy * d[1]
    if t < 0:
        return None
    perp = abs(vx * d[1] - vy * d[0])
    if perp <= radius:
        return t
    return None


def cast_laser(shooter: Player, targets: List[Player], arena, radius: float,
               pierces: bool = False) -> Tuple[Vec, List[Player]]:
    """Return (laser end point, players hit) for one shooter."""
    d = direction(shooter.angle)
    max_t = distance_to_wall(shooter.pos, d, arena)
    hits = []
    for t_player in targets:
        if t_player.pid == shooter.pid:
            continue
        t = ray_hits_circle(shooter.pos, d, t_player.pos, radius)
        if t is not None and t <= max_t:
            hits.append((t, t_player))
    hits.sort(key=lambda h: h[0])
    if not pierces and hits:
        hits = hits[:1]
        max_t = hits[0][0]
    end = (shooter.pos[0] + d[0] * max_t, shooter.pos[1] + d[1] * max_t)
    return end, [p for _, p in hits]


def resolve_round(players: List[Player], arena, radius: float, pierces: bool = False):
    """All living players fire simultaneously from their placed positions.

    Returns (lasers, eliminated) where lasers maps pid -> (start, end)
    and eliminated is a set of pids. Does NOT mutate players.
    """
    living = [p for p in players if p.alive]
    lasers: Dict[int, Tuple[Vec, Vec]] = {}
    eliminated: Set[int] = set()
    for shooter in living:
        end, hit = cast_laser(shooter, living, arena, radius, pierces)
        lasers[shooter.pid] = (shooter.pos, end)
        eliminated.update(p.pid for p in hit)
    return lasers, eliminated


def can_place(pos: Vec, placed: List[Player], arena, radius: float) -> bool:
    """Inside arena (with margin) and not overlapping already-placed players."""
    x, y, w, h = arena
    if not (x + radius <= pos[0] <= x + w - radius and y + radius <= pos[1] <= y + h - radius):
        return False
    for p in placed:
        if p.pos is not None and math.dist(pos, p.pos) < radius * 2:
            return False
    return True


def make_players(n: int, colors) -> List[Player]:
    return [Player(pid=i, name=f"Player {i + 1}", color=colors[i % len(colors)]) for i in range(n)]

def arena_for_round(base, round_no: int, shrink, min_size):
    """Arena rectangle (x, y, w, h) for a given round: shrinks evenly toward the center.
 
    Round 1 is the full base arena; each later round removes `shrink` pixels from each side,
    but never goes below `min_size`.
    """
    x, y, w, h = base
    n = max(0, round_no - 1)
    new_w = max(min_size[0], w - 2 * shrink[0] * n)
    new_h = max(min_size[1], h - 2 * shrink[1] * n)
    return (x + (w - new_w) / 2, y + (h - new_h) / 2, new_w, new_h)


# ---------------------------------------------------------------------------
# Power-ups: torch (cone) and reflector (bouncing beam)
# ---------------------------------------------------------------------------
def reflect_path(origin: Vec, d: Vec, arena, bounces: int) -> List[Vec]:
    """Polyline of a beam fired from `origin` along unit vector `d` that bounces off the
    arena walls `bounces` times (0 = a plain straight laser). Returns bounces + 2 points."""
    x, y, w, h = arena
    px, py = origin
    dx, dy = d
    pts = [(px, py)]
    for _ in range(bounces + 1):
        tx = (x + w - px) / dx if dx > 1e-12 else ((x - px) / dx if dx < -1e-12 else math.inf)
        ty = (y + h - py) / dy if dy > 1e-12 else ((y - py) / dy if dy < -1e-12 else math.inf)
        t = min(tx, ty)
        px, py = px + dx * t, py + dy * t
        pts.append((px, py))
        if tx <= ty + 1e-9:
            dx = -dx
        if ty <= tx + 1e-9:
            dy = -dy
    return pts


def segment_hit(a: Vec, b: Vec, center: Vec, radius: float) -> Optional[float]:
    """Distance along segment a->b to the point closest to `center`, if within `radius`."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return None
    t = max(0.0, min(1.0, ((center[0] - a[0]) * dx + (center[1] - a[1]) * dy) / l2))
    qx, qy = a[0] + dx * t, a[1] + dy * t
    if math.hypot(center[0] - qx, center[1] - qy) <= radius:
        return t * math.sqrt(l2)
    return None


def truncate_path(pts: List[Vec], length: float) -> List[Vec]:
    """Cut a polyline so it is only `length` long."""
    out = [pts[0]]
    left = length
    for a, b in zip(pts, pts[1:]):
        seg = math.dist(a, b)
        if seg >= left:
            f = left / seg if seg else 0.0
            out.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
            return out
        out.append(b)
        left -= seg
    return out


def in_cone(origin: Vec, d: Vec, half_angle: float, center: Vec, radius: float) -> bool:
    """Is a circle touched by the triangle/cone fired from origin along d (half_angle in radians)?"""
    vx, vy = center[0] - origin[0], center[1] - origin[1]
    dist = math.hypot(vx, vy)
    if dist <= radius:
        return True
    phi = abs(math.atan2(d[0] * vy - d[1] * vx, d[0] * vx + d[1] * vy))   # angle between d and the target
    return phi <= half_angle + math.asin(min(1.0, radius / dist))


def fire_shots(players: List[Player], arena, radius: float, pierces: bool = False,
               torch_half_deg: float = 15, bounces: int = 3, hits_self: bool = True,
               markers=None, marker_radius: float = 30.0,
               deflectors=None, deflector_radius: float = 24.0):
    """All placed players fire at once, using their `powerup` if they have one.

    Returns (shots, eliminated). Each shot is a dict:
      laser / reflector -> {"pid", "kind", "points": [[x, y], ...]}   (a polyline)
      torch             -> {"pid", "kind": "torch", "x0", "y0", "angle", "half"}   (a cone; pierces everyone)
    Every shot also has "touched": ids of the `markers` [(id, (x, y)), ...] its beam/cone reached, and
    "deflected": ids of the `deflectors` [(id, (x, y), turn), ...] that bent its beam (see trace_beam).
    Does NOT mutate players.
    """
    shots, eliminated = [], set()
    markers = markers or []
    deflectors = deflectors or []
    half = math.radians(torch_half_deg)
    for s in players:
        d = direction(s.angle)
        if s.powerup == "torch":
            for t in players:
                if t.pid != s.pid and in_cone(s.pos, d, half, t.pos, radius):
                    eliminated.add(t.pid)
            shots.append({"pid": s.pid, "kind": "torch", "x0": s.pos[0], "y0": s.pos[1],
                          "angle": s.angle, "half": half,
                          "touched": [mid for mid, mp in markers if in_cone(s.pos, d, half, mp, marker_radius)],
                          "deflected": []})            # the wide cone is not bent by deflectors
            continue
        reflect = s.powerup == "reflector"
        pts, events = trace_beam(s.pos, d, arena, bounces if reflect else 0, deflectors, deflector_radius)
        hits, travelled = [], 0.0
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            for t in players:
                if t.pid == s.pid and (i == 0 or not hits_self):
                    continue                      # your own beam never hits you on the way out
                hd = segment_hit(a, b, t.pos, radius)
                if hd is not None:
                    hits.append((travelled + hd, t.pid))
            travelled += math.dist(a, b)
        hits.sort()
        cut = None
        if hits and not pierces:
            hits = hits[:1]
            cut = hits[0][0]
            pts = truncate_path(pts, cut)
        eliminated.update(pid for _, pid in hits)
        touched = [mid for mid, mp in markers
                   if any(segment_hit(a, b, mp, marker_radius) is not None for a, b in zip(pts, pts[1:]))]
        shots.append({"pid": s.pid, "kind": "reflector" if reflect else "laser",
                      "points": [[round(px, 2), round(py, 2)] for px, py in pts], "touched": touched,
                      "deflected": [did for dist, did in events if cut is None or dist <= cut + 1e-9]})
    return shots, eliminated


def powerup_count(round_no: int, start: int = 4, first_end: int = 10, step: int = 10, cap: Optional[int] = None) -> int:
    """How many power-ups are hidden in a round: none before `start`, 1 up to round `first_end`,
    then one more every `step` rounds (11-20 -> 2, 21-30 -> 3, 31-40 -> 4 ...)."""
    if round_no < start:
        return 0
    n = 1 if round_no <= first_end else 1 + math.ceil((round_no - first_end) / step)
    return min(n, cap) if cap else n


def trace_beam(origin: Vec, d: Vec, arena, bounces: int, deflectors=(), deflector_radius: float = 24.0):
    """Like reflect_path, but the beam also turns 90 degrees where it first touches each deflector.

    `deflectors` is a list of (id, (x, y), turn); turn = +1 bends the beam clockwise on screen
    (heading right -> heading down), -1 counter-clockwise. A deflector bends a given beam at most once,
    and is ignored if the beam starts inside it. Returns (points, events) with
    events = [(distance travelled along the beam when it was bent, deflector id), ...].
    """
    x, y, w, h = arena
    px, py = origin
    dx, dy = d
    pts, events, travelled, walls = [(px, py)], [], 0.0, 0
    used = {did for did, c, _ in deflectors if math.dist(origin, c) < deflector_radius}
    for _ in range(500):                              # hard stop: every deflector can only bend a beam once
        if walls > bounces:
            break
        tx = (x + w - px) / dx if dx > 1e-12 else ((x - px) / dx if dx < -1e-12 else math.inf)
        ty = (y + h - py) / dy if dy > 1e-12 else ((y - py) / dy if dy < -1e-12 else math.inf)
        t_wall = min(tx, ty)
        best = None                                   # nearest deflector this beam runs into before the wall
        for did, c, turn in deflectors:
            if did in used:
                continue
            fx, fy = px - c[0], py - c[1]
            b = fx * dx + fy * dy
            disc = b * b - (fx * fx + fy * fy - deflector_radius ** 2)
            if disc < 0:
                continue
            t = -b - math.sqrt(disc)                  # where the beam enters the icon's circle
            if 1e-6 < t < t_wall and (best is None or t < best[0]):
                best = (t, did, turn)
        if best:
            t, did, turn = best
            px, py = px + dx * t, py + dy * t
            travelled += t
            pts.append((px, py))
            events.append((travelled, did))
            used.add(did)
            dx, dy = (-dy, dx) if turn > 0 else (dy, -dx)
            continue
        px, py = px + dx * t_wall, py + dy * t_wall
        travelled += t_wall
        pts.append((px, py))
        if tx <= ty + 1e-9:
            dx = -dx
        if ty <= tx + 1e-9:
            dy = -dy
        walls += 1
    return pts, events


def bomb_victims(center: Vec, radius: float, players: List[Player], player_radius: float, owner_pid: int) -> List[int]:
    """Players (never the bomb's owner) whose character touches the blast circle."""
    return [p.pid for p in players if p.pid != owner_pid and math.dist(p.pos, center) <= radius + player_radius]


def bomber_spots(arena, inset: float = 50.0) -> List[Vec]:
    """The five fixed places the Bomber can hide: the four corners (a little inside the walls) and the centre."""
    x, y, w, h = arena
    return [(x + inset, y + inset), (x + w - inset, y + inset),
            (x + inset, y + h - inset), (x + w - inset, y + h - inset),
            (x + w / 2, y + h / 2)]