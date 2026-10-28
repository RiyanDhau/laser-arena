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