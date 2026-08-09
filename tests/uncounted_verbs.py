"""A villager who reaches for a verb we never built must leave a mark.

`World.execute()` dispatches on `_VERB_HANDLERS.get(act, _verb_idle)` — a
bare dict lookup with a default — so `_verb_idle` is reached two entirely
different ways and, until v3.9.4, said the same sentence for both:

    a villager who CHOSE to rest            -> "passed the time"
    a villager who named a verb we lack     -> "passed the time"

For 190 days nobody counted the second kind. This pins the distinction.

It also pins what the instrument must NOT do: no new verb becomes valid, no
wording a villager can read changes, and the tally never reaches world state
or the golden hash. (claude/THE-UNCOUNTED-VERBS.md)
"""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-uncounted-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
from sim.engine import Engine

config.MOCK_MODE = True
config.RADIO_ENABLED = False

FAILED = []


def check(label, condition):
    if condition:
        print(f"PASS {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}")


engine = Engine(seed=417)
world = engine.world
who = list(world.agents.values())[0]
other = list(world.agents.values())[1]

check("a fresh town has counted nothing", world.unknown_verbs == {})

# ------------------------------------------------- a deliberate rest
ok, summary = world.execute(who, {"action": "idle"})
check("choosing to idle still works", ok)
check("...and is NOT counted as an unrecognized verb", world.unknown_verbs == {})

ok, summary = world.execute(who, {})
check("an action with no verb at all is not counted either",
      ok and world.unknown_verbs == {})

# --------------------------------------- a verb this world does not have
ok, summary = world.execute(who, {"action": "open", "place": "the Rusty Tap"})
check("an unknown verb still idles — no new physics", ok)
check("...it is recorded as 'passed the time', unchanged for the villager",
      "passed the time" in summary)
check("...AND IT IS COUNTED", world.unknown_verbs.get("open") == 1)
check("...against the villager who reached for it",
      who.name in world.unknown_verb_actors.get("open", set()))

world.execute(other, {"action": "open"})
world.execute(other, {"action": "search", "for": "Marlow"})
check("counts accumulate across villagers", world.unknown_verbs.get("open") == 2)
check("and across verbs", world.unknown_verbs.get("search") == 1)
check("both actors are recorded for the same verb",
      {who.name, other.name} == set(world.unknown_verb_actors["open"]))

# ------------------------------------------------ known verbs stay known
before = dict(world.unknown_verbs)
world.execute(who, {"action": "say", "text": "hello"})
world.execute(who, {"action": "work"})
check("a real verb is never counted, even when it fails",
      world.unknown_verbs == before)

# --------------------------------------------------- it did not add a verb
check("`open` did NOT become a real verb", "open" not in world._VERB_HANDLERS)
check("`search` did NOT become a real verb", "search" not in world._VERB_HANDLERS)

# ------------------------------------------- operator-facing, not villager
flags = engine.flags()
check("flags() calls it out", any("UNRECOGNIZED VERBS" in f for f in flags))
check("...loudest first", any(f.startswith("UNRECOGNIZED VERBS: open x2")
                              for f in flags))
check("vitals carry it", engine.vitals().get("unknown_verbs", {}).get("open") == 2)

# A VILLAGER MUST NOT BE ABLE TO READ THIS. The tally is ours, not theirs:
# telling a town which verbs it has been getting wrong is teaching, and
# teaching is steering.
seen_by_villagers = " ".join(
    e.get("text", "") for e in world.recent_events(200)
) if hasattr(world, "recent_events") else ""
check("no villager-visible event mentions the tally",
      "UNRECOGNIZED" not in seen_by_villagers)

# ===================================================================
#  THE CRASH THAT KILLED TOWNS (v3.9.4)
#  `_VERB_HANDLERS.get(act)` raises TypeError on an unhashable key, and
#  nothing between execute() and Engine.step() catches it. A villager
#  emitting {"action": {"build": "the pool"}} took the whole town down.
#  Pre-existing on every version up to v3.9.3; verified against a stashed
#  main before it was fixed.
# ===================================================================
from sim.causality import Command

for label, payload in (("a dict as the verb",  {"action": {"build": "the pool"}}),
                       ("a list as the verb",  {"action": ["open"]}),
                       ("an int as the verb",  {"action": 7}),
                       ("a null verb",         {"action": None}),
                       ("a deeply nested verb", {"action": {"a": {"b": [1, 2]}}})):
    try:
        res = world.apply_command(Command(kind="action", source="test",
                                          actor=who.name, payload=payload))
        check(f"{label} idles instead of killing the town", res.accepted)
    except Exception as exc:
        check(f"{label} idles instead of killing the town — RAISED {exc!r}", False)

check("a malformed verb is counted by TYPE, not swallowed",
      world.unknown_verbs.get("<dict>") == 2)
check("...and type-failures are visually distinct from invented verbs",
      all(k.startswith("<") == (k in ("<dict>", "<list>", "<int>", "<NoneType>"))
          for k in world.unknown_verbs))

# ---------------------------------------------------- bounded, but loudly
for i in range(400):
    world.execute(who, {"action": f"junk{i}"})
world.execute(who, {"action": "z" * 5000})
check("the tally is bounded at 200 distinct verbs", len(world.unknown_verbs) == 200)
check("...and no key is longer than 48 characters",
      max(len(k) for k in world.unknown_verbs) <= 48)
check("...and everything dropped is COUNTED, never silently discarded",
      world.unknown_verbs_overflow > 0)
check("...and flags() says so out loud",
      any("beyond the 200-verb tally" in f for f in engine.flags()))

# ------------------------------------------------------ never persisted
snap = world.snapshot() if hasattr(world, "snapshot") else {}
check("the tally is not in the world snapshot",
      "unknown_verbs" not in str(snap))

print()
if FAILED:
    print(f"FAILED {len(FAILED)}")
    for label in FAILED:
        print(f"  - {label}")
    raise SystemExit(1)
print("uncounted_verbs: all checks passed")
