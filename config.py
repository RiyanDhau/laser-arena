"""Game settings. The server reads these; the browser receives arena size / radius on join."""

ARENA_RECT = (0, 0, 900, 600)     # x, y, width, height (browser canvas uses the same coordinates)
PLAYER_RADIUS = 16
MIN_PLAYERS = 2
MAX_PLAYERS = 10
LASER_PIERCES = False             # True: lasers pass through every player in their path
PLACING_SECONDS = 40              # time each round to place + aim (players who miss it are eliminated)
REVEAL_SECONDS = 7                # how long results stay on screen before the next round

PLAYER_COLORS = [
    (80, 170, 255), (255, 90, 90), (90, 220, 120),
    (255, 210, 70), (200, 120, 255), (255, 150, 60),
    (60, 220, 220), (255, 120, 200), (170, 220, 60), (220, 220, 220),   # extra colors so 10 players all look different
]

# ---- chat ----
CHAT_MAX_LENGTH = 200             # characters per message
CHAT_RATE_LIMIT = (5, 5.0)        # at most 5 messages per 5 seconds per player
CHAT_HISTORY = 50                 # messages replayed to players who join the room later