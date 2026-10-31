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

# ---- power-ups ----
# Power-ups are HIDDEN: players are never told where they are while placing. If you happen to
# place your character on one (within POWERUP_PICKUP_RADIUS) it is activated for that round's shot.
# If your beam touches a power-up icon (within POWERUP_BEAM_RADIUS) you earn a SHIELD: it protects you during the
# NEXT round only (it absorbs every hit on you that round, then expires, used or not).
# Everything is revealed once the round is fired.
POWERUPS_ENABLED = True
POWERUP_START_ROUND = 4           # first round they appear ("after the third round")
# How many are hidden each round: rounds 4-10 -> 1, 11-20 -> 2, 21-30 -> 3, 31-40 -> 4 ... (+1 every POWERUP_TIER_SIZE rounds)
POWERUP_TIER_END = 10             # last round of the first tier
POWERUP_TIER_SIZE = 10            # length of every later tier
POWERUP_MAX_PER_ROUND = 10        # safety cap so the arena never gets crowded
POWERUP_TYPES = ["torch", "reflector"]
POWERUP_PICKUP_RADIUS = 50        # you get it if you place within this distance (hidden, so keep it generous)
POWERUP_BEAM_RADIUS = 30          # your beam 'touches' an icon if it passes within this distance
POWERUP_MARGIN = 60               # keep spawns this far from the walls
POWERUP_MIN_GAP = 140             # keep spawns this far from each other
TORCH_HALF_ANGLE = 15             # degrees; the torch beam is a triangle 2x this wide
REFLECTOR_BOUNCES = 3             # how many times the reflector beam bounces off the walls (2 or 3 is fun)
REFLECTOR_HITS_SELF = True        # a bounced beam can come back and eliminate its own shooter

# ---- deflector (joins the random power-up mix after round 10) ----
# A hidden icon that bends any laser or reflector beam that touches it by 90 degrees (each icon has a fixed turn
# direction, revealed after the round). It can't be picked up, and the Torch's wide cone is not bent by it.
DEFLECTOR_START_ROUND = 11
DEFLECTOR_RADIUS = 24             # how close a beam must pass to the icon to be bent

# ---- bomber (a special hidden power-up that appears once every BOMBER_EVERY rounds: 10, 20, 30 ...) ----
# It is ALWAYS hidden in one of 5 fixed spots - the four corners or the centre of the arena - and players know
# that, but not which one it is this time.
# It is an EXTRA icon on top of the normal power-ups. Whoever places their character on it gets a bomb for the
# NEXT round: they secretly drop it anywhere in the arena, and when the lasers fire it explodes - every OTHER
# player whose character touches the blast is eliminated (a shield can still absorb it).
BOMBER_EVERY = 10                 # 0 turns the bomber off
BOMB_RADIUS = 90                  # blast radius in pixels
BOMBER_SPOT_INSET = 50            # how far in from the walls the four corner spots are (so a character can stand on them)