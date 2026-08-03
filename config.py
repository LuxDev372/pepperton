"""Pepperton configuration — the single source of truth.

Tune here, not in the engine. Everything the sim does that you might
want to change lives in this file.
"""

# ---------------------------------------------------------------- town
TOWN_NAME = "Pepperton"   # your town, your name — threads through
                          # prompts, the group chat, radio, and the UI

# ---------------------------------------------------------------- pacing
# "accelerated": sim runs fast — one sim-day in ~10 real minutes at defaults.
# "realtime":    sim clock == wall clock. The town lives alongside you.
PACING_MODE = "accelerated"

PACING = {
    "accelerated": {
        "real_seconds_per_tick": 6.0,   # wall-clock pause between ticks
        "sim_minutes_per_tick": 15,     # sim time advanced per tick
    },
    "realtime": {
        "real_seconds_per_tick": 900.0,  # 15 min
        "sim_minutes_per_tick": 15,
    },
}

# ---------------------------------------------------------------- brains
# MOCK_MODE = True runs the whole town on scripted mock brains — no GPU,
# no Ollama, fully deterministic given WORLD_SEED. Flip to False on a
# machine with Ollama to give the villagers real minds.
MOCK_MODE = True

# Ollama hosts — the town can span multiple GPUs. "default" is the box
# running the sim; add LAN hosts to spread the cast across cards, e.g.
#   "annex": "http://192.168.1.50:11434",
OLLAMA_HOSTS = {
    "default": "http://127.0.0.1:11434",
}
OLLAMA_TIMEOUT = 120          # seconds; small models on busy GPUs take a while
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_OPTIONS = {"temperature": 0.8, "num_ctx": 4096}

# The casting pool: villager N gets MODEL_POOL[N % len(MODEL_POOL)].
# Heterogeneous minds are the whole point — different model families are
# different cognitive species. Each entry: (model_tag, host_key).
# Suggested casts by VRAM — FAMILY diversity matters more than size:
#   ~8GB:  three 3B-4B models          ~16GB: the default below
#   24GB+: swap in 7B-8B minds freely  multi-GPU: use host keys per entry
MODEL_POOL = [
    ("mistral:latest", "default"),      # the confident one (will invent facts, found things)
    ("qwen3:8b", "default"),            # the overthinker (thinking auto-disabled)
    ("qwen2.5:latest", "default"),      # the earnest elder
    ("phi4-mini:latest", "default"),    # the small one with big diary energy
    ("llama3.2:3b", "default"),         # the agreeable parrot (the laws keep it honest)
]

# ---------------------------------------------------------------- world
WORLD_SEED = None      # None = random town each run; set an int to pin the cast
CAST_SIZE = 5

# How the whole town talks. Injected into every villager's persona —
# retune the register here without touching code.
TOWN_VOICE = (
    "It is the present day (2026). Talk like a normal contemporary American — "
    "casual, current, everyday speech. People here have phones, wifi, streaming, "
    "online orders, and opinions about gas prices. Absolutely no old-timey or "
    "western dialect: never say 'reckon', 'mosey', 'didja', 'mighty fine', "
    "'young guns', or anything that sounds like a cowboy movie."
)

SIM_START = "07:00"    # sim clock at boot, day 1

# Needs: 0..100. Decay per tick while awake; urgent below threshold.
NEEDS = {
    "energy":   {"start": 80, "decay": 1.0, "urgent_below": 25},
    "fullness": {"start": 70, "decay": 0.8, "urgent_below": 30},
}
REST_RECOVERY = 4.0        # energy per tick while resting at home
NAP_RECOVERY = 2.0         # energy per tick napping in the park
MEAL_FULLNESS = 45         # fullness gained per meal
MEAL_COST = 5              # dollars
WAGE_PER_SHIFT_TICK = 4    # dollars per tick while working
START_MONEY = (12, 30)     # uniform range at cast generation

# Locations. Homes are added automatically, one per villager.
LOCATIONS = {
    "the plaza":        {"desc": "the open square at the center of town"},
    "Rosie's Diner":    {"desc": "the diner — coffee, hot meals, and the radio on the counter", "sells_food": True, "radio": True},
    "Pepper & Sons":    {"desc": "the general store — groceries and odds and ends", "sells_food": True},
    "the park":         {"desc": "a small park with benches and a duck pond"},
    "the library":      {"desc": "the town library — quiet, smells of old paper"},
    "the workshop":     {"desc": "a shared workshop on the edge of town"},
    "the Rusty Tap":    {"desc": "the town bar — dim, warm, a pool table nobody's level at", "bar": True},
}

DRINK_COST = 4          # a beer at the Rusty Tap
ROOM_COST = 8           # a night in the room above the Rusty Tap (for the homeless)
TIPSY_TICKS = 8         # how long one drink keeps a villager loosened up
DRUNK_AT = 3            # drinks within the window that tip candid into sloppy

# Overheard (untargeted) speech no longer interrupts everyone in the room —
# it's still perceived, but only being directly addressed demands a response.
# This is the main brake on runaway talk-chains.
AMBIENT_SPEECH_INTERRUPTS = False

# ------------------------------------------------------- the notice board
# Communal projects: real things the town can BUILD. Progress is public,
# and so is the contributors list — the difference between talkers and
# doers, posted on the board for everyone to see.
# "Good intentions never built a castle."
PROJECTS = [
    {"name": "the gazebo", "site": "the park", "work": 40,
     "desc": "rebuild the collapsed gazebo by the duck pond",
     "adds": "a handsome rebuilt gazebo stands by the pond", "icon": "⛩️"},
    {"name": "the fall fair", "site": "the plaza", "work": 60,
     "desc": "plan, build, and decorate the Pepperton fall fair",
     "adds": "fair stalls and string lights fill the square — the fall fair the town built",
     "icon": "🎪"},
    {"name": "the book box", "site": "the library", "work": 20,
     "desc": "build a take-one-leave-one book box for the plaza",
     "adds": "a hand-built take-one-leave-one book box stands out front",
     "icon": "📚"},
]
TALK_STREAK_NUDGE = 3   # consecutive talk-only turns before the world objects


# job -> where that job reports for a shift
WORKPLACES = {
    "cook":       "Rosie's Diner",
    "shopkeeper": "Pepper & Sons",
    "librarian":  "the library",
    "handyman":   "the workshop",
    "gardener":   "the park",
    "artist":     "the plaza",
    "bartender":  "the Rusty Tap",
    "retired":    None,
}

# ---------------------------------------------------------------- cast
# Random archetype generation: every new world deals a different hand.
FIRST_NAMES = [
    "Mabel", "Otis", "June", "Walt", "Pearl", "Gus", "Ida", "Frank",
    "Nora", "Cal", "Hazel", "Roy", "Vera", "Ned", "Opal", "Sam",
]
LAST_NAMES = [
    "Fletcher", "Grady", "Holt", "Merriweather", "Pike", "Sorrel",
    "Tibbs", "Vance", "Whitlock", "Crane", "Dobbs", "Early",
]

ARCHETYPES = [
    {"job": "cook",       "traits": ["warm", "nosy", "feeds people whether they ask or not"]},
    {"job": "shopkeeper", "traits": ["shrewd", "keeps a mental ledger of every favor", "secretly sentimental"]},
    {"job": "librarian",  "traits": ["precise", "quietly judgmental", "knows everything about everyone from what they borrow"]},
    {"job": "handyman",   "traits": ["unhurried", "distrusts anything invented after 1990", "surprisingly philosophical"]},
    {"job": "gardener",   "traits": ["cheerful", "talks to plants more than people", "conflict-avoidant"]},
    {"job": "artist",     "traits": ["dramatic", "chronically broke", "finds meaning in everything"]},
    {"job": "retired",    "traits": ["opinionated", "was somebody once and mentions it", "generous with unsolicited advice"]},
    {"job": "bartender",  "traits": ["hears everything and repeats exactly the wrong parts", "pours with opinions", "closes whenever they feel like it"]},
]

QUIRKS = [
    "convinced the weather is personal",
    "collects rumors like coupons",
    "never finishes a cup of coffee",
    "quotes their late spouse constantly",
    "keeps a list of grievances, updated daily",
    "believes the ducks in the park are watching",
    "cannot keep a secret longer than an hour",
    "suspicious of the radio",
]

GOALS = [
    "wants to organize a town event nobody asked for",
    "is trying to get out of a debt owed to another villager",
    "wants to be seen as the most informed person in town",
    "is quietly looking for a business partner",
    "wants someone — anyone — to appreciate their work",
    "is determined to uncover what the neighbors are hiding",
    "wants to win an argument they lost years ago",
]

# ------------------------------------------------------------- the Director
# Random chaos events, rolled quietly through each day. Weights are relative.
CHAOS = {
    "enabled": True,
    "events_per_day": 1.3,          # average; actual rolls are random per tick
    "weights": {
        "anonymous_text": 3,        # a villager gets a text from an unknown number
        "rumor_seed": 3,            # someone could SWEAR they saw something last night
        "windfall": 2,              # found money
        "duck_omen": 2,             # something is wrong at the pond
        "group_leak": 2,            # a private text gets forwarded to Pepperton_Gossip
        "dead_air": 1,              # the radio is static all day
        "stranger": 1,              # someone steps off the bus
    },
    "max_strangers": 2,
}

STRANGER_NAMES = ["Marlow", "Quinn", "Sable", "Dorian", "Lennox", "Ash"]
STRANGER_MODELS = MODEL_POOL        # a stranger's mind is drawn from the same pool
STRANGER_GOALS = [
    "is looking for a specific person in this town, but won't say who",
    "is trying to buy property here quietly, before anyone asks questions",
    "is clearly running from something and needs this town to not notice",
    "claims to be 'just passing through' but keeps not leaving",
    "knew this town a long time ago, under circumstances nobody remembers",
]

ANON_TEXTS = [
    "I know what you did.",
    "Don't trust {name}.",
    "Check the park at midnight. Come alone.",
    "You didn't hear this from me, but {name} isn't who they say they are.",
    "Stop asking questions.",
    "The money isn't where you think it is.",
    "{name} has been talking about you behind your back.",
]

RUMOR_SEEDS = [
    "You could swear you saw {name} sneaking out of {place} very late last night.",
    "Word around town is {name} came into money recently and hasn't said a thing.",
    "You heard {name} was seen arguing with someone on the edge of town.",
    "Somebody said {name} has been getting strange phone calls.",
]

# ---------------------------------------------------------------- radio
RADIO_ENABLED = True
# How news reaches the town:
#   "radio" — bulletins play at the diner; only those present hear (info has geography)
#   "phone" — news alerts push to every villager's phone, each getting a
#             personal SAMPLE of the headlines (filter bubbles: neighbors
#             argue about the news having read different news)
#   "both"  — the diner radio plays AND phones buzz
NEWS_DELIVERY = "phone"
NEWS_ALERTS_PER_VILLAGER = 2   # headlines sampled per villager per bulletin
NEWS_POOL_PER_BULLETIN = 6     # size of the headline pool each bulletin draws from
RSS_FEEDS = [
    "http://feeds.bbci.co.uk/news/rss.xml",
    "https://feeds.npr.org/1001/rss.xml",
]
RADIO_HEADLINES_PER_BULLETIN = 3
RADIO_BULLETIN_TIMES = ["08:00", "18:00"]   # sim times, at the diner
NEWS_CACHE = "data/news_cache.json"
NEWS_MAX_AGE_HOURS = 6      # refetch feeds when cache is older than this

# ---------------------------------------------------------------- server
HOST = "0.0.0.0"
PORT = 8811
WS_PUSH_INTERVAL = 0.5      # seconds between websocket state pushes

# ---------------------------------------------------------------- data
DB_PATH = "data/pepperton.db"
TRANSCRIPT_JSONL = "data/transcript.jsonl"
TRANSCRIPT_LOG = "data/transcript.log"

# ------------------------------------------------------- decision cadence
# The brain (LLM) is consulted only when something warrants it — activity
# finished, someone spoke to them, radio bulletin, a need went urgent —
# and at most every MAX_TICKS_BETWEEN_DECISIONS ticks regardless. This is
# what makes a 5-model town feasible on one GPU.
MAX_TICKS_BETWEEN_DECISIONS = 8

# Reflection: at this sim time each night, every agent summarizes its day
# into a high-importance memory (Stanford-style).
REFLECTION_TIME = "23:30"

# Memory retrieval weights
MEMORY_TOP_K = 8
MEMORY_WEIGHTS = {"recency": 1.0, "importance": 1.0, "relevance": 1.2}
MEMORY_RECENCY_HALFLIFE_TICKS = 96   # one sim day
