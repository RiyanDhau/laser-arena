"""Game settings. The server reads these; the browser receives arena size / radius on join."""

ARENA_RECT = (0, 0, 900, 600)     # x, y, width, height (browser canvas uses the same coordinates)
PLAYER_RADIUS = 16
MIN_PLAYERS = 2
MAX_PLAYERS = 6
LASER_PIERCES = False             # True: lasers pass through every player in their path
PLACING_SECONDS = 40              # time each round to place + aim (players who miss it are eliminated)
REVEAL_SECONDS = 7                # how long results stay on screen before the next round

PLAYER_COLORS = [
    (80, 170, 255), (255, 90, 90), (90, 220, 120),
    (255, 210, 70), (200, 120, 255), (255, 150, 60),
]
