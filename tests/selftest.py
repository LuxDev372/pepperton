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
fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} passed")
sys.exit(1 if fails else 0)
