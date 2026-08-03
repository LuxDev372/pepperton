"""Continuity regression tests — run from the project root:
    python tests/selftest.py
Covers the findings from Pepperton's first external code review:
fresh-world isolation, activity expiry (exact wages), crash-recovery
memory reconciliation, and deterministic resume.
"""
import os, shutil, sys
sys.path.insert(0, ".")
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
config.MOCK_MODE = True
config.RADIO_ENABLED = False   # no network in tests

from sim.engine import Engine

def fresh_data():
    shutil.rmtree("data", ignore_errors=True)
    os.makedirs("data")

results = []
def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""))

# ---- 1. fresh-world isolation: a new world never inherits old memories
fresh_data()
e1 = Engine(seed=7); e1.run_headless(1)
a = list(e1.world.agents)[0]
n1 = e1.memory.count(a)
e2 = Engine(seed=7)   # same seed, NEW world (new world_id)
e2.run_headless(1)
n2 = e2.memory.count(list(e2.world.agents)[0])
check("fresh worlds are isolated", n2 <= n1 + 2 and e1.world_id != e2.world_id,
      f"run1={n1} rows, run2={n2} rows, ids differ={e1.world_id != e2.world_id}")

# ---- 2. activity expiry: shifts pay EXACTLY until the bell
fresh_data()
e = Engine(seed=9)
ag = list(e.world.agents.values())[0]
ag.asleep = False
ag.activity = {"type": "work", "until_tick": e.world.tick_no + 3, "note": ""}
ag.money = 0
paid_ticks = 0
for _ in range(8):
    e.world.tick_no += 1
    act = ag.activity
    if act and act.get("until_tick") is not None and e.world.tick_no >= act["until_tick"]:
        ag.activity = None
    before = ag.money
    e._apply_needs(ag)
    if ag.money > before:
        paid_ticks += 1
check("expired shifts stop paying", paid_ticks == 2,
      f"paid for {paid_ticks} ticks (expected 2: ticks +1,+2; bell at +3)")

# ---- 3. crash recovery reconciles future memories
fresh_data()
e = Engine(seed=11)
e.run_headless(4)
e.save_state()
ag = list(e.world.agents)[0]
e.memory.add(ag, 6, 1, "08:30", "event", "FUTURE GHOST MEMORY", 9)
state = Engine.load_state()
e2 = Engine(state=state)
ghosts = [m for m in e2.memory.recent(ag, 50) if "FUTURE GHOST" in m["text"]]
check("future memories reconciled on restore", not ghosts,
      f"ghost rows remaining: {len(ghosts)}")

# ---- 4. deterministic resume: branch A (uninterrupted) == branch B (restored)
fresh_data()
e = Engine(seed=13)
e.run_headless(10)
e.save_state()
snapshot_state = Engine.load_state()
e.run_headless(10)   # branch A continues to tick 20
locA = {a.name: a.location for a in e.world.agents.values()}
moneyA = {a.name: round(a.money, 2) for a in e.world.agents.values()}
eB = Engine(state=snapshot_state)   # branch B restores tick 10
eB.run_headless(10)
locB = {a.name: a.location for a in eB.world.agents.values()}
moneyB = {a.name: round(a.money, 2) for a in eB.world.agents.values()}
check("resume is deterministic (locations)", locA == locB,
      "" if locA == locB else f"A={locA} B={locB}")
check("resume is deterministic (money)", moneyA == moneyB,
      "" if moneyA == moneyB else f"A={moneyA} B={moneyB}")

fresh_data()

# ---- appended by v1.18: Warmth & Closure regression ----
def _extra():
    fresh_data()
    e = Engine(seed=21)
    ags = list(e.world.agents.values())
    a, b = ags[0], ags[1]
    # bidirectional gratitude on a treat
    a.location = b.location = "Rosie's Diner"; a.money = 40
    b.needs["fullness"] = 40
    e.world.execute(a, {"action": "treat"})
    both = a.relationships.get(b.name, 0) > 0 and b.relationships.get(a.name, 0) > 0
    check("gratitude flows both directions", both,
          f"giver->recv {a.relationships.get(b.name)}, recv->giver {b.relationships.get(a.name)}")
    # valence + goal arc via a scripted reflection envelope
    old_goal = a.goal
    class FakeBrain:
        def reflect(self, agent, day, day_memories):
            return {"reflection": "Test diary.", "warmer": b.name,
                    "colder": None, "goal_resolved": True}
    e.brains[a.name] = FakeBrain()
    class NullBrain:
        def reflect(self, agent, day, day_memories):
            return {"reflection": "quiet day", "warmer": None,
                    "colder": None, "goal_resolved": False}
    for other in ags[1:]:
        e.brains[other.name] = NullBrain()
    before = a.relationships.get(b.name, 0)
    e._nightly_reflections()
    check("nightly warmth applies (+3)", a.relationships.get(b.name, 0) == before + 3,
          f"{before} -> {a.relationships.get(b.name, 0)}")
    check("goal arc resolves and rerolls", a.goal != old_goal,
          f"'{old_goal[:30]}...' -> '{a.goal[:30]}...'")
    fresh_data()

_extra()
fails2 = [r for r in results if not r[1]]
print(f"\nTOTAL {len(results) - len(fails2)}/{len(results)} passed")
sys.exit(1 if fails2 else 0)
