"""Continuity regression tests — run from the project root:
    python tests/selftest.py
Covers the findings from Pepperton's first external code review:
fresh-world isolation, activity expiry (exact wages), crash-recovery
memory reconciliation, and deterministic resume.
"""
import os, shutil, sys

# Run inside a scratch directory, never the project root. fresh_data()
# below does `rmtree("data")`, and the paths in config are relative — run
# from the root while a town is live (or even just afterwards) and this
# file deletes the saved world, its database and its transcripts. It has
# done exactly that once. sys.path points at the real root so `import
# config` still finds the real config.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(ROOT, ".selftest")
sys.path.insert(0, ROOT)
os.makedirs(SCRATCH, exist_ok=True)
os.chdir(SCRATCH)
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

# ---- appended by v2.1: Room at the Inn + withholding ----
def _inn_and_taxes():
    fresh_data()
    e = Engine(seed=41)
    w = e.world
    ags = list(w.agents.values())
    a = ags[0]

    # withholding: wages are taxed at the till, straight to the fund
    config.INCOME_TAX = 0.15
    a.job = "cook"
    w.tills["Rosie's Diner"] = 50.0
    fund0 = w.tills[config.TOWN_FUND]
    money0 = a.money
    w.pay_wage(a, 4)
    check("wages are taxed to the fund",
          round(a.money - money0, 2) == 3.4 and
          round(w.tills[config.TOWN_FUND] - fund0, 2) == 0.6,
          f"net +{round(a.money - money0, 2)}, fund +{round(w.tills[config.TOWN_FUND] - fund0, 2)}")

    # late wages don't dodge the withholding (income is income)
    a.money = 0
    w.tills["Rosie's Diner"] = 0.0
    w.pay_wage(a, 4)                   # till is dry: all of it becomes IOU
    w.tills["Rosie's Diner"] = 10.0
    fund_before = w.tills[config.TOWN_FUND]
    w.settle_business_debts()
    check("late wages are taxed like prompt ones",
          round(a.money, 2) == 3.4 and
          round(w.tills[config.TOWN_FUND] - fund_before, 2) == 0.6,
          f"worker +{round(a.money, 2)}, fund +{round(w.tills[config.TOWN_FUND] - fund_before, 2)}")

    # an inn-shaped proposal is shelter infrastructure, not a monument
    for stock in w.projects[:2]:
        stock["complete"] = True   # free up board slots (cap is 3 open)
    a.location = "the plaza"
    ok, why = w.execute(a, {"action": "propose",
                            "project": "the Pepperton Motel",
                            "site": "the plaza", "work": 10})
    proj = next(p for p in w.projects if "Motel" in p["name"])
    check("a motel proposal is recognized as an inn",
          ok and proj.get("inn") and not proj.get("housing"), why[:50])
    # "dinner theater" must NOT read as an inn
    ok2, _ = w.execute(a, {"action": "propose", "project": "dinner theater",
                           "site": "the plaza", "work": 10})
    proj2 = next((p for p in w.projects if "theater" in p["name"]), None)
    check("'dinner' does not summon an inn",
          ok2 and proj2 is not None and not proj2.get("inn"), "")

    # completion opens real beds; a homeless villager's rent goes to the fund
    proj["done"] = proj["work"] - 1
    a.activity = {"type": "build", "project": proj["name"],
                  "until_tick": w.tick_no + 8}
    e._apply_needs(a)   # finishes the build
    inn_locs = [k for k, v in w.locations.items() if v.get("inn")]
    check("a finished inn opens its doors", len(inn_locs) == 1,
          f"{inn_locs}")
    drifter = ags[1]
    drifter.home = None
    drifter.location = inn_locs[0]
    drifter.money = 20
    drifter.needs["energy"] = 30
    fund1 = w.tills[config.TOWN_FUND]
    ok, msg = w.execute(drifter, {"action": "rest"})
    check("inn beds shelter the homeless, fund the town",
          ok and drifter.asleep and
          round(w.tills[config.TOWN_FUND] - fund1, 2) == config.INN_ROOM_COST,
          msg[:60])
    fresh_data()


# ---- appended by v2.0: the Invisible Hand regression ----
def _economy():
    fresh_data()
    config.INCOME_TAX = 0.0   # v2.0 mechanics tested untaxed; tax has its own test
    e = Engine(seed=31)
    w = e.world
    ags = list(w.agents.values())
    a, b = ags[0], ags[1]
    bank = w.bank_name()

    # money is CONSERVED: a closed loop, chaos off, over two sim-days
    config.CHAOS["enabled"] = False
    def total():
        return round(sum(x.money for x in w.agents.values())
                     + sum(w.tills.values()), 2)
    t0 = total()
    e.run_headless(192)
    t1 = total()
    check("money is conserved (closed loop)", abs(t0 - t1) < 0.01,
          f"start ${t0} -> end ${t1}")
    config.CHAOS["enabled"] = True

    # meals land in the till
    fresh_data()
    e = Engine(seed=31); w = e.world
    ags = list(w.agents.values()); a, b = ags[0], ags[1]
    bank = w.bank_name()
    a.location = "Rosie's Diner"; a.money = 20; a.needs["fullness"] = 40
    till0 = w.tills["Rosie's Diner"]
    ok, _ = w.execute(a, {"action": "eat"})
    till_after = w.tills["Rosie's Diner"]
    check("meals land in the till", ok and till_after == till0 + config.MEAL_COST,
          f"till ${till0} -> ${till_after}")

    # a dry till pays what it can and books the rest as back wages
    a.job = "cook"; a.location = "Rosie's Diner"; a.money = 0
    w.tills["Rosie's Diner"] = 1.0
    w.pay_wage(a, 4)
    owed = w.open_debts(creditor=a.name)
    check("dry till books back wages", a.money == 1.0 and owed and
          owed[0]["amount"] == 3.0,
          f"paid ${a.money}, booked {owed[0]['amount'] if owed else 0}")
    # ...and settles automatically when cash comes in
    w.tills["Rosie's Diner"] = 10.0
    w.settle_business_debts()
    check("refilled till settles back wages", a.money == 4.0 and
          not w.open_debts(creditor=a.name),
          f"money now ${a.money}")

    # person-to-person pay clears a ledger debt
    b.location = a.location
    w.add_debt(a.name, b.name, 6, "test debt")
    a.money = 10; bmoney0 = b.money
    ok, _ = w.execute(a, {"action": "pay", "to": b.name, "amount": 6})
    check("pay settles person debts", ok and not w.open_debts(debtor=a.name)
          and b.money == bmoney0 + 6,
          f"a=${a.money}, b +${b.money - bmoney0}")

    # promises: spoken debt is recorded, payment keeps it, silence breaks it
    a.location = b.location = "the plaza"
    w.execute(a, {"action": "say", "text": "I'll pay you back on Friday, I swear.",
                  "to": b.name})
    open_prom = [p for p in w.promises if p["status"] == "open"]
    check("a spoken promise is recorded", len(open_prom) == 1,
          f"{len(open_prom)} open promises")
    a.money = 5
    w.execute(a, {"action": "pay", "to": b.name, "amount": 2})
    kept = [p for p in w.promises if p["status"] == "kept"]
    check("payment keeps a promise", len(kept) == 1, "")
    # a second promise, left to rot (texted from across town)
    b.location = "the library"
    w.execute(a, {"action": "text", "to": b.name,
                  "text": "About the rest — I owe you, I'll get you the money."})
    w.clock.day += config.PROMISE_GRACE_DAYS + 1
    w.ledger.swept_day = 0
    w.morning_ledger()
    broken = [p for p in w.promises if p["status"] == "broken"]
    check("silence breaks a promise", len(broken) == 1,
          f"{len(broken)} broken")

    # the Sam Fletcher Amendment: gratitude idioms are not contracts
    n_before = len(w.promises)
    w.execute(a, {"action": "say", "text": "Thanks for that! I owe you one.",
                  "to": b.name}) if b.location == a.location else None
    b.location = a.location
    w.execute(a, {"action": "say", "text": "Really — I owe you one, friend.",
                  "to": b.name})
    check("'I owe you one' is not a contract", len(w.promises) == n_before,
          f"{len(w.promises) - n_before} bogus promises recorded")
    # ...but an idiom WITH money attached still counts
    w.execute(a, {"action": "say",
                  "text": "I owe you one — twenty bucks, and I'll pay you back Friday.",
                  "to": b.name})
    check("money promises still register",
          len(w.promises) == n_before + 1, "")
    # grandfather clause: a pre-amendment idiom promise lapses, no verdict
    w.promises.append({"id": 99, "maker": a.name, "to": b.name,
                       "text": "Thanks, I owe you one!", "day": w.clock.day,
                       "due_day": w.clock.day - 1, "status": "open"})
    rel_before = w.agents[b.name].relationships.get(a.name, 0)
    w.ledger.swept_day = 0
    w.morning_ledger()
    g = next(p for p in w.promises if p["id"] == 99)
    check("old idiom promises lapse without verdict",
          g["status"] == "lapsed" and
          w.agents[b.name].relationships.get(a.name, 0) == rel_before,
          f"status={g['status']}")

    # the bank: thin credit is capped, loans move real money, ledger records
    fresh_data()
    e = Engine(seed=33); w = e.world
    a = list(w.agents.values())[0]
    bank = w.bank_name()
    a.location = bank; a.money = 0
    ok, why = w.execute(a, {"action": "borrow", "amount": 999})
    check("thin credit is capped", not ok and "notice board" in why, why[:60])
    ok, _ = w.execute(a, {"action": "borrow", "amount": 10})
    debt = w.open_debts(debtor=a.name, creditor=bank)
    check("loans move real money", ok and a.money == 10 and debt and
          debt[0]["amount"] == round(10 * (1 + config.LOAN_INTEREST), 2),
          f"money ${a.money}, owes {debt[0]['amount'] if debt else 0}")
    ok, why = w.execute(a, {"action": "borrow", "amount": 5})
    check("one loan at a time", not ok, why[:50])

    # rent: charged on rent day, booked as debt for the broke
    fresh_data()
    e = Engine(seed=35); w = e.world
    housed = [x for x in w.agents.values() if x.home][:2]
    r1, r2 = housed[0], housed[1]
    r1.money = 20; r2.money = 0
    w.clock.day = config.RENT_EVERY_DAYS + 1   # a rent day
    fund0 = w.tills[config.TOWN_FUND]
    w.morning_ledger()
    check("rent flows to the town fund", r1.money == 20 - config.RENT_COST and
          w.tills[config.TOWN_FUND] >= fund0 + config.RENT_COST,
          f"payer ${r1.money}, fund ${w.tills[config.TOWN_FUND]}")
    rdebt = w.open_debts(debtor=r2.name, creditor=config.TOWN_FUND)
    check("unpayable rent becomes debt", len(rdebt) == 1 and
          rdebt[0]["amount"] == config.RENT_COST,
          f"{len(rdebt)} rent debts")
    fresh_data()

_economy()
_inn_and_taxes()

# ---- appended by v2.2: the Poor Box ----
def _poor_box():
    fresh_data()
    e = Engine(seed=51)
    w = e.world
    ags = list(w.agents.values())
    rich, broke = ags[0], ags[1]
    box = config.POOR_BOX

    # donations land in the jar, publicly, from anywhere
    rich.money = 50
    ok, msg = w.execute(rich, {"action": "pay", "to": "the poor box",
                               "amount": 12})
    check("donations fill the poor box", ok and w.tills[box] == 12.0 and
          rich.money == 38, msg[:50])

    # a broke villager at the diner eats ON the box: jar pays the diner
    broke.money = 2
    broke.location = "Rosie's Diner"
    broke.needs["fullness"] = 30
    diner0 = w.tills["Rosie's Diner"]
    full0 = broke.needs["fullness"]
    ok, msg = w.execute(broke, {"action": "eat"})
    check("the box buys meals for the broke",
          ok and broke.money == 2 and w.tills[box] == 12.0 - config.MEAL_COST
          and w.tills["Rosie's Diner"] == diner0 + config.MEAL_COST
          and broke.needs["fullness"] > full0,
          msg[:60])

    # empty jar: the old cold reality returns
    w.tills[box] = 0.0
    broke.needs["fullness"] = 30
    ok, msg = w.execute(broke, {"action": "eat"})
    check("an empty box feeds no one", not ok, msg[:50])
    fresh_data()

_poor_box()

# ---- appended by v2.4: the Fall Fair Act ----
def _permits():
    fresh_data()
    e = Engine(seed=61)
    w = e.world
    ags = list(w.agents.values())
    a = ags[0]

    # genesis projects carry permits
    stamped = [p for p in w.projects if p.get("permit_due")]
    check("charter projects carry permits", len(stamped) == len(w.projects),
          f"{len(stamped)}/{len(w.projects)} stamped")

    # a proposal takes out a permit sized to the work
    for stock in w.projects[:2]:
        stock["complete"] = True
    a.location = "the plaza"
    ok, _ = w.execute(a, {"action": "propose", "project": "the bandstand",
                          "site": "the plaza", "work": 40})
    proj = next(p for p in w.projects if p["name"] == "the bandstand")
    expect = w.clock.day + w.permit_window(40)
    check("proposals take out permits", ok and proj["permit_due"] == expect,
          f"due day {proj.get('permit_due')} (expected {expect})")

    # deadline passes: broke proposer is fined INTO THE LEDGER
    a.money = 2
    w.clock.day = proj["permit_due"] + 1
    w.permit_sweep()
    fine_debts = w.open_debts(debtor=a.name, creditor=config.TOWN_FUND)
    check("expired permit fines the proposer", proj.get("fined") and
          any("permit fine" in d["reason"] for d in fine_debts),
          f"fined={proj.get('fined')}, debts={len(fine_debts)}")

    # two days later, still unbuilt: condemned, slot freed (the sweep
    # also takes any other expired carcass — the inspector is thorough)
    w.clock.day = proj["condemn_day"] + 1
    w._permit_day_done = 0
    w.permit_sweep()
    check("the wrecking crew comes at dawn",
          all(p["name"] != "the bandstand" for p in w.projects) and
          all(p["complete"] for p in w.projects), "")

    # but a FINISHED project is untouchable, fined or not
    survivor = w.projects[0]
    survivor["fined"] = True
    survivor["condemn_day"] = w.clock.day - 1
    w._permit_day_done = 0
    w.permit_sweep()
    check("finished work cannot be condemned",
          any(p["name"] == survivor["name"] for p in w.projects), "")
    fresh_data()

_permits()

# ---- v2.4.1 regression: v2.4 must boot on a pre-2.4 config ----
def _old_config_boot():
    fresh_data()
    saved = {}
    for knob in ("PERMIT_MIN_DAYS", "PERMIT_SHIFTS_PER_DAY", "PERMIT_FINE",
                 "CONDEMN_GRACE_DAYS", "PERMITS_ENABLED"):
        if hasattr(config, knob):
            saved[knob] = getattr(config, knob)
            delattr(config, knob)
    try:
        e = Engine(seed=71)
        e.run_headless(50)
        ok = all(p.get("permit_due") for p in e.world.projects)
        check("v2.4 boots on a pre-2.4 config", ok,
              f"day {e.world.clock.day}")
    finally:
        for knob, val in saved.items():
            setattr(config, knob, val)
    fresh_data()

_old_config_boot()

# ---- appended by v2.5: Creature Comforts ----
def _goods():
    fresh_data()
    e = Engine(seed=81)
    w = e.world
    a = list(w.agents.values())[0]

    # buying moves money into the till and the item into the pocket
    a.location = "Pepper & Sons"; a.money = 40
    till0 = w.tills["Pepper & Sons"]
    ok, msg = w.execute(a, {"action": "buy", "item": "fine hat"})
    check("goods are bought at the counter", ok and
          "a fine hat" in a.possessions and
          w.tills["Pepper & Sons"] == till0 + 15 and a.money == 25,
          msg[:50])
    ok, msg = w.execute(a, {"action": "buy", "item": "a fine hat"})
    check("one hat is plenty", not ok, msg[:40])

    # the mattress makes home sleep better
    a.possessions.append("a proper mattress")
    a.location = a.home; a.asleep = True; a.needs["energy"] = 50
    e._apply_needs(a)
    check("the mattress pays off",
          abs(a.needs["energy"] - (50 + config.REST_RECOVERY + 1.5
                                   - config.NEEDS["fullness"]["decay"] * 0)) < 0.01
          or a.needs["energy"] == 50 + config.REST_RECOVERY + 1.5,
          f"energy 50 -> {a.needs['energy']}")

    # tools: the first swing counts double
    a.asleep = False
    proj = next(p for p in w.projects if not p["complete"])
    a.possessions.append("carpenter's tools")
    a.location = proj["site"]
    done0 = proj["done"]
    ok, _ = w.execute(a, {"action": "build", "project": proj["name"]})
    check("tools make the first swing count double", ok and
          proj["done"] == done0 + 1 and
          proj["contributors"].get(a.name, 0) >= 1, "")

    # coffee is consumed, not owned
    a.location = "Rosie's Diner"; a.money = 10; a.needs["energy"] = 40
    ok, _ = w.execute(a, {"action": "buy", "item": "coffee"})
    check("coffee is drunk, not shelved", ok and
          a.needs["energy"] == 52 and "a coffee" not in a.possessions, "")
    fresh_data()

_goods()
fails2 = [r for r in results if not r[1]]
print(f"\nTOTAL {len(results) - len(fails2)}/{len(results)} passed")
sys.exit(1 if fails2 else 0)
