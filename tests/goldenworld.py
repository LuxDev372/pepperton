"""THE GOLDEN WORLD — a frozen config, so the fingerprint means something.

tests/goldenhash.py used to import the operator's live config.py. That
made the "golden hash" a fingerprint of CODE PLUS THIS MACHINE'S SETTINGS,
which is not what it was ever claimed to be. Brad ran v3.1.0 against his
own hundred-day Pepperton config on 2026-08-07 and got a completely
different hash from the one in the release notes, exactly as designed, and
briefly had every reason to think the release had broken his town. It had
not. The instrument was wrong.

MEAL_COST alone moves it. So does anything else the world is built from.

So the harness no longer reads config.py at all. It installs THIS file as
the config module before sim is imported, which means the hash answers the
only question it was ever supposed to answer:

    given the same world, did the CODE change behaviour?

RULES FOR THIS FILE

  * It is FROZEN. Do not tune it, do not sync it with config.py, do not
    "fix" a value here because you changed one there. Editing this file
    moves the hash for every contributor on every machine and invalidates
    every baseline in every commit message going back to v2.0.1.
  * A knob that is missing here falls through to the sim's own getattr
    default, which is code. That is correct and intentional.
  * If you must change it, say so loudly in the commit and record both
    the old and the new hash.

Frozen from config.example.py at v3.1.1.
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
# running the sim; add LAN hosts to spread the cast across cards
# (e.g. annex 16GB primary + annex's RTX 3050 6GB annex).
OLLAMA_HOSTS = {
    "default": "http://127.0.0.1:11434",
    "annex": "http://192.0.2.10:11434",
}
OLLAMA_TIMEOUT = 120          # seconds; small models on busy GPUs take a while
OLLAMA_KEEP_ALIVE = "30m"
OLLAMA_OPTIONS = {"temperature": 0.8, "num_ctx": 4096}

# ------------------------------------------------------------- providers
# A provider is HOW a mind is reached. The second element of every
# MODEL_POOL entry is a key into this table (every OLLAMA_HOSTS key is
# folded in automatically as an {"api": "ollama"} provider, so old configs
# and the "default" key keep working untouched).
#
# Adding a mind later = one entry here + one line in a cast. No code.
#
#   api          "ollama"  -> POST {url}/api/chat        (local AND Ollama-cloud)
#                "openai"  -> POST {url}/chat/completions (OpenAI-compatible:
#                             OpenRouter, Groq, Together, vLLM, LM Studio, ...)
#   url          base URL, no trailing slash
#   api_key_env  env var holding the key. Never put a key in this file.
#                Missing/empty key = that villager quietly falls back to its
#                mock understudy instead of crashing the town.
#   timeout      seconds (defaults to OLLAMA_TIMEOUT)
#   json_mode    openai only: ask the server for strict JSON. A few
#                gateways reject the field — set False if you see 400s.
#   headers      extra headers merged into the request
#   options      per-provider overrides merged over OLLAMA_OPTIONS
PROVIDERS = {
    # Ollama-cloud tags (anything ending ":cloud") are served THROUGH the
    # local daemon once `ollama signin` is done — same URL as "default",
    # but they cross a network and think hard, so they get a real timeout.
    "cloud": {
        "api": "ollama",
        "url": "http://127.0.0.1:11434",
        "timeout": 300,
    },

    # --- OpenAI-compatible gateways. Inert until the env var is set. ---
    "openrouter": {
        "api": "openai",
        "url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "timeout": 180,
        "headers": {"HTTP-Referer": "http://localhost:8811",
                    "X-Title": TOWN_NAME},
    },
    "groq": {
        "api": "openai",
        "url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "timeout": 120,
    },
    "openai": {
        "api": "openai",
        "url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "timeout": 180,
    },
}

# Hidden deliberation makes a villager geologically slow and adds nothing
# the town can see. Any model tag containing one of these substrings gets
# `"think": false` on Ollama requests. (Verified 2026-08-03: harmless on
# models with no thinking mode — Ollama ignores it rather than erroring.)
THINK_OFF = ("qwen3", "glm-5", "deepseek-v4", "nemotron", "gpt-oss")

# The casting pool: villager N gets MODEL_POOL[N % len(MODEL_POOL)].
# Heterogeneous minds are the whole point — different model families are
# different cognitive species. Each entry: (model_tag, provider_key).
# Pick the cast with CAST; every OLLAMA_HOSTS key doubles as a provider.
CASTS = {
    "local": [
        ("mistral:latest", "default"),      # annex 16GB — the confident one
        ("qwen3:8b", "default"),            # annex — the overthinker
        ("qwen2.5:latest", "default"),      # annex — the elder qwen
        ("phi4-mini:latest", "annex"),     # annex citizen, RTX 3050
        ("llama3.2:3b", "annex"),          # annex citizen, RTX 3050
    ],
}
CAST = "local"
MODEL_POOL = CASTS[CAST]

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
WAGE_PER_SHIFT_TICK = 2    # dollars per tick while working (a full shift ~$32
                           # against ~$15/day of living costs — v1.x paid $4,
                           # priced for a town where nobody worked; in the
                           # closed-loop economy that drained every till by
                           # day 2 and turned all payroll into IOUs)
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
    f"First Bank of {TOWN_NAME}": {"desc": "the town bank — marble counter, one teller window, and a ledger that forgets nothing", "bank": True},
}

DRINK_COST = 4          # a beer at the Rusty Tap
ROOM_COST = 8           # a night in the room above the Rusty Tap (for the homeless)
TIPSY_TICKS = 8         # how long one drink keeps a villager loosened up
DRUNK_AT = 3            # drinks within the window that tip candid into sloppy


# Creature Comforts (v2.5): the carrot half of the incentive state. Money
# can finally BECOME something — goods sold at real counters, paid into
# real tills, with effects a villager (and the town) can see. Durables
# are owned once; "consumable" items are used on the spot.
GOODS = {
    "a proper mattress": {"cost": 25, "sold_at": "Pepper & Sons",
        "pitch": "sleep like the righteous — rest at home recovers faster"},
    "carpenter's tools": {"cost": 20, "sold_at": "Pepper & Sons",
        "pitch": "arrive at any build site ready — your first swing counts double"},
    "a fine hat": {"cost": 15, "sold_at": "Pepper & Sons",
        "pitch": "people notice a hat like this"},
    "a pocket watch": {"cost": 30, "sold_at": "Pepper & Sons",
        "pitch": "the quiet signal of a person whose time matters"},
    "a paperback novel": {"cost": 6, "sold_at": "Pepper & Sons",
        "pitch": "something to be mid-way through; lends itself to conversation"},
    "a coffee": {"cost": 2, "sold_at": "Rosie's Diner", "consumable": True,
        "pitch": "hot, immediate, and cheaper than a meal"},
}

# ------------------------------------------------- the Invisible Hand (v2.0)
# Money is a CLOSED LOOP. Every dollar spent at a business lands in that
# business's till; wages are paid FROM the till. Public jobs (librarian,
# gardener, artist, handyman) are paid from the town fund, which rent
# replenishes. When a till can't make payroll, wages become DEBT the
# business settles when cash comes in. Nobody prints money. (The Director
# still can. The Director is not bound by economics.)
ECONOMY = True              # False = old open-faucet economy (v1.x behavior)
TILL_SEED = 60              # opening cash in each business till
TOWN_FUND_SEED = 150        # opening balance of the town fund (pays public wages)
TOWN_FUND = "the town fund"

WAGE_DEBT_CAP = 40          # when a till's back-wage IOUs hit this, it stops
                            # offering shifts until the business earns — real
                            # businesses cut hours, they don't print IOUs forever

RENT_COST = 6               # owed by every housed villager, each rent day
RENT_EVERY_DAYS = 3         # rent falls due every N days (day 4, 7, 10, ...)
RENT_GRACE_DAYS = 2         # unpaid rent becomes an overdue debt after this

# First Bank: loans against your public reputation. Credit is computed from
# the notice board (build shifts = collateral of character) and your debt
# history. The teller window is open to anyone; the ledger forgets nothing.
BANK_SEED = 200             # the bank's opening capital
LOAN_INTEREST = 0.25        # flat interest on the whole loan
LOAN_TERM_DAYS = 4          # repayment due N days after borrowing
LOAN_MAX_GOOD = 30          # credit limit, solid reputation
LOAN_MAX_SHAKY = 12         # credit limit, thin file
CREDIT_GOOD_SHIFTS = 8      # build shifts on the board for "solid" credit

# Promises: speech that creates obligation. When a villager SAYS or TEXTS a
# payment promise to a specific person, the world writes it down. Any
# payment to that person before the deadline keeps the promise; the
# deadline passing in silence breaks it — and the town's memory of a
# broken promise outlives the debt.
PROMISE_PATTERNS = (
    "i'll pay you back", "i will pay you back", "pay you back",
    "i'll pay you", "i will pay you", "i owe you", "i'll settle up",
    "i'll get you the money", "i'll get you your money",
)
PROMISE_GRACE_DAYS = 2

# Room at the Inn (v2.1): the town can BUILD shelter infrastructure. A
# proposed project whose name says motel/inn/hotel/hostel/lodge becomes,
# on completion, a real place where anyone homeless can sleep for
# INN_ROOM_COST a night — proceeds to the town fund (a municipal motel:
# the homeless quietly fund the librarian's wages).
INN_KEYWORDS = ("motel", "hotel", "boarding house", "boardinghouse",
                "hostel", "lodge", "motor lodge")   # plus the word "inn"
INN_ROOM_COST = 5           # a bed, cheaper than the Tap's $8 gouge

# Withholding (v2.1): wages are taxed at the till, straight to the town
# fund — the only reliable income the fund has besides rent. "To
# encourage," as the town's founding courier put it.
INCOME_TAX = 0.15

# The Poor Box (v2.2): a jar on the diner counter. Anyone can drop money
# in (pay action, to "the poor box" — a PUBLIC act; this town competes at
# legible virtue). When a broke villager tries to eat at the diner, the
# box pays for their meal — the diner still gets its money, the town sees
# who eats on charity, and nobody starves while the jar is full.
POOR_BOX = "the poor box"
POOR_BOX_ENABLED = True

# The Holt Act (v2.7): the bank buys the town fund's receivables at face
# value each morning, and paper the bank holds has TEETH. Sit on bought
# debt past the grace window and the bank levies your HOUSE: door locked,
# home listed for sale, you sleep where you can. Pay the bank off before
# the sale and you buy your door back; if it sells, the price goes
# against your debt and any surplus is honestly returned. Passed on Day
# 69 of Pepperton, when the arrears board hit 21 counts of unpaid rent
# against a fund four villagers ate from — named for Ash Holt, banker,
# four cycles in arrears at a bank he had not once opened, and thereby
# the first man in town history foreclosed on by his own employer.
FORECLOSURE = {
    "enabled": True,
    "bank_reserve": 60,   # loan capital the bank will NOT spend on paper
    "grace_days": 3,      # days between purchase notice and the levy
    "house_price": 40,    # list price of a seized house, deed at the teller
    "min_debt": 12,       # the bank doesn't take houses over pocket change
}

# The Crane Bonus (v2.9): the first law in this town's history named for a
# HERO instead of a criminal. Walt Crane worked twenty-five consecutive days
# at Rosie's Diner while every other door in Pepperton stayed shut, made the
# first poor-box donation the town ever saw, and never once mentioned either.
# Every statute before this one was a stick. This is the carrot: consecutive
# days worked earn a permanent step up in wage, a milestone bonus paid from
# the town fund (the fund's first positive expenditure), and your streak read
# aloud beside the absentees at the evening ledger. Miss a day and the raise
# resets — the stakes run both directions, same as the shame does.
#
# SHIPPED DARK (enabled False) on purpose: the ten-day bus experiment is
# running in Pepperton and nothing may change under it. Arm it at Day 90.
LOYALTY = {
    "enabled": False,
    "streak_days": 5,        # consecutive days worked per step
    "raise_pct": 0.20,       # each step adds this much to the wage
    "max_steps": 3,          # ceiling: +60%
    "milestone_bonus": 15,   # paid from the town fund when a step is earned
}

# The Townsfolk (v3.0): scripted residents who make a town a town. Zero
# GPU, seeded, deterministic. They are PRESENT so a room is not empty,
# they TRY DOORS and are seen finding them open or shut, they SPEAK TO
# villagers (a stranger wanting something from you is a different prompt
# than a friend describing his hunger), and they OFFER PAID WORK in
# person with the cash in hand.
#
# THE RULE, and it is not negotiable — Brad, Day 91: "no charity what so
# ever. work-reward." An NPC never gives a villager anything they did not
# earn. No gifts, no free meals, no jar donations, no rescues. Money
# crosses a counter or pays for labour, and takes no other path.
#
# Ships DARK. Arm it deliberately.
TOWNSFOLK = {
    "enabled": False,
    "count": 6,
    "spend": [3, 7],
    "move_chance": 0.35,
    "shop_chance": 0.18,
    "shut_quiet_ticks": 24,
    "speak_chance": 0.12,
    "oddjob_chance": 0.05,
    "oddjob_pay": [4, 8],
    "oddjob_expires": 8,
}

# The Bus Route (v2.8): the town's first customers from outside itself.
# Pepperton's money was a closed loop — seven broke people trying to be
# each other's economy — so the coach route reopens. Sightseers arrive on
# a rhythm with money from elsewhere (the only faucet in this world) and
# spend an afternoon. They buy ONLY from businesses that are open, and a
# business is open when a villager is standing in it working. Nothing
# explains this to anyone. The receipts are the argument.
BUS = {
    "enabled": True,
    "every_days": 2,        # a rhythm, not a windfall
    "arrive": "11:00",
    "depart": "16:00",
    "visitors": [4, 8],
    "spend": [4, 9],
    "buys_per_visitor": 2,
}

# Situations Vacant (v2.7, same statute): a law with teeth owes the
# toothless a way to earn. Any job in WORKPLACES that nobody holds is
# OPEN — an unemployed villager claims it by walking in and using the
# work action. Showing up is the interview. The bell and the evening
# ledger advertise the openings daily.
HIRING_ENABLED = True

# The Fall Fair Act (v2.4): proposing a project takes out a PERMIT with a
# completion window sized to the work. Blow the deadline: the proposer is
# fined into the town fund (or into the debt ledger, if broke). Two days
# after the fine, a still-unfinished project is CONDEMNED — torn down, the
# board slot freed, the contributors' shifts mourned. Named for the fall
# fair that squatted at 11/60 for three straight weeks while the town
# built porch lighting twice.
# The Working Day (v2.6): labor becomes as PUBLIC as building. A morning
# bell reminds every employed villager their shift starts — and that the
# town notices who shows. Each evening the attendance ledger posts
# town-wide: who worked (and earned what), whose doors never opened, and
# how many days running. The clout engine that built nine monuments,
# aimed at the teller window.
WORKDAY_BELL = "08:00"
ATTENDANCE_TIME = "18:00"
ATTENDANCE_ENABLED = True

PERMITS_ENABLED = True
PERMIT_MIN_DAYS = 4          # even a tiny project gets this long
PERMIT_SHIFTS_PER_DAY = 8    # window = max(MIN, work / this), in days
PERMIT_FINE = 10             # assessed on the proposer at the deadline
CONDEMN_GRACE_DAYS = 2       # days between the fine and the wrecking crew

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
    "banker":     f"First Bank of {TOWN_NAME}",
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
    {"job": "banker",     "traits": ["polite in a way that costs extra", "believes character is collateral", "remembers every dollar anyone has ever owed"]},
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
        # "windfall" is DELETED, not zeroed. Brad's ruling, Day 91:
        # "no charity whatsoever. work-reward." Money conjured into a
        # villager's pocket for nothing is the gods doing the exact thing
        # this town has spent seventy days proving does not work. The
        # handler stays in director.py so an old config that still lists
        # the event keeps booting; nothing rolls it any more.
        "duck_omen": 2,             # something is wrong at the pond
        "group_leak": 2,            # a private text gets forwarded to Pepperton_Gossip
        "dead_air": 1,              # the radio is static all day
        "stranger": 1,              # someone steps off the bus
        "heist": 0,                 # the lockbox is forced in the night —
                                    # weight 0: NEVER random. The Director
                                    # (you) fires it deliberately:
                                    #   /api/chaos {"event": "heist"}
                                    # No culprit exists. The town will
                                    # invent one. That's the show.
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
