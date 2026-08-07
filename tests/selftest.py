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
from sim.world import World

def fresh_data():
    # release transcript handles first: this harness builds many Engines in
    # one process, and on Windows an open handle blocks the rmtree below
    World.close_all()
    shutil.rmtree("data", ignore_errors=True)
    os.makedirs("data")

# v2.4.1's law — "every knob must have a code default, and an old town's
# config must boot untouched" — APPLIES TO THIS FILE TOO, and did not, until
# v3.1.1. Brad ran v3.1.0's suite against his own hundred-day Pepperton
# config and it died on line 207 with AttributeError: no INN_ROOM_COST. The
# SIM was fine — every late knob is getattr'd in sim/. It was the test
# enforcing the law that broke the law, which is the worst possible file for
# it to happen in: the one thing standing between an old town and a bad
# upgrade crashed instead of reporting.
#
# Defaults below MUST match the sim's own. If they drift, this file starts
# testing a world nobody runs.
LATE_KNOBS = {
    "INN_ROOM_COST": 5,
    "POOR_BOX": "the poor box",
    "INCOME_TAX": 0.15,
    "HIRING_ENABLED": True,
    "CONDEMN_GRACE_DAYS": 3,
    "FORECLOSURE": {},
    "BUS": {},
    "LOYALTY": {},
    "TOWNSFOLK": {},
}

def knob(name):
    return getattr(config, name, LATE_KNOBS[name])

def snapshot_knob(name):
    """(value, existed) — so a block can put the config back EXACTLY as it
    found it, including absent. Restoring {} where there was nothing is
    harmless today only because every cfg() merges over its own defaults;
    it is still a lie about the config, and lies about the config are the
    whole reason this section exists."""
    return dict(knob(name)), hasattr(config, name)

def restore_knob(name, value, existed):
    if existed:
        setattr(config, name, value)
    elif hasattr(config, name):
        delattr(config, name)


# Say what you are, out loud, first (v3.1.2). Brad hit the same crash on the
# same line number twice in one morning — once because the suite had a real
# bug, and once because the fixed file had not actually landed on disk. From
# the output those are indistinguishable, and I misdiagnosed the second one
# as the first. A harness that does not identify itself cannot be trusted to
# report on anything else.
# A piped harness must not die of SIGPIPE (v3.1.3). `selftest.py | head -1`
# raised BrokenPipeError mid-run and looked exactly like a failure in the
# suite, one morning after two other things had looked exactly like failures
# in the suite. Python leaves SIGPIPE set to SIG_IGN; restoring the default
# makes `| head` behave the way anyone piping a test runner expects.
try:
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (ImportError, AttributeError, ValueError):
    pass    # Windows has no SIGPIPE; nothing to defend against there

def _stamp(what):
    v = "unknown"
    for p in (os.path.join(ROOT, "VERSION"), "VERSION"):
        try:
            v = open(p).read().strip()
            break
        except OSError:
            pass
    print(f"{what} — Pepperton v{v}  ({os.path.abspath(__file__)})",
          flush=True)
    return v

_stamp("SELFTEST")

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
          round(w.tills[config.TOWN_FUND] - fund1, 2) == knob("INN_ROOM_COST"),
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
    # v3.3.2: net ALL outside money, not just the coach. world.outside_flow
    # exists precisely because the bus is not the only faucet — armed
    # townsfolk buy things and pay for odd jobs too. Subtracting only
    # bus.brought_in made this test pass on a default config and fail on
    # any town that had actually armed the Townsfolk. The SIM was right;
    # the test was measuring the wrong total.
    flow0 = getattr(w, "outside_flow", 0.0)
    e.run_headless(192)
    t1 = total()
    # v2.8: the loop is no longer sealed — the coach route carries money
    # in from outside. It must still BALANCE: every dollar in the town is
    # a dollar that was here before or a dollar a tourist spent.
    t1 -= round(getattr(w, "outside_flow", 0.0) - flow0, 2)
    check("money is conserved (closed loop + audited bus inflow)",
          abs(t0 - t1) < 0.01,
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
    box = knob("POOR_BOX")

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

# ---- appended by v2.6: the Working Day + the heist ----
def _working_day():
    fresh_data()
    e = Engine(seed=91)
    w = e.world
    workers = [a for a in w.agents.values() if a.workplace()]
    a = workers[0]

    # the bell queues a reminder for every employed villager
    w.clock.day = 2
    w.morning_bell()
    belled = [x for x in workers
              if any("morning bell" in p["text"] for p in x.pending)]
    check("the morning bell rings for the employed",
          len(belled) == len(workers), f"{len(belled)}/{len(workers)}")

    # attendance: one works, the rest are posted absent
    w.execute(a, {"action": "move", "to": a.workplace()})
    w.execute(a, {"action": "work"})
    w.attendance_ledger()
    ledger_ev = [ev for ev in w.events
                 if "ATTENDANCE LEDGER" in str(ev.get("text", ""))]
    check("the attendance ledger posts town-wide",
          len(ledger_ev) == 1 and a.name.split()[0] in ledger_ev[0]["text"]
          and "DOORS NEVER OPENED" in ledger_ev[0]["text"],
          ledger_ev[0]["text"][:70] if ledger_ev else "no ledger")
    absent_worker = next(x for x in workers if x.name != a.name)
    check("absence streaks count in public",
          w.absence_streaks.get(absent_worker.name) == 1 and
          w.absence_streaks.get(a.name) == 0, "")

    # the heist: god-button only, drains the fund, tells everyone
    check("the heist never fires at random",
          config.CHAOS["weights"].get("heist") == 0, "")
    fund0 = w.tills[config.TOWN_FUND]
    result = e.director.trigger("heist")
    took = fund0 - w.tills[config.TOWN_FUND]
    heist_ev = [ev for ev in w.events
                if "FORCED IN THE NIGHT" in str(ev.get("text", ""))]
    check("the lockbox job takes real money, publicly",
          took > 0 and len(heist_ev) == 1 and "heist" in result, result[:50])
    fresh_data()

_working_day()

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

# ---- appended by v2.7: the Holt Act (debt market, foreclosure, hiring) ----
def _holt_act():
    fresh_data()
    e = Engine(seed=77)
    w = e.world
    bank = w.bank_name()
    fc = w.ledger.foreclosure_cfg()
    debtor = next(a for a in w.agents.values()
                  if a.home and a.workplace() != bank)
    debtor.money = 0

    # book overdue rent debt directly, then run the market
    w.clock.day = 10
    d1 = w.add_debt(debtor.name, config.TOWN_FUND, 6, "unpaid rent (test)",
                    due_day=8)
    d2 = w.add_debt(debtor.name, config.TOWN_FUND, 6, "unpaid rent (test)",
                    due_day=9)
    fresh = w.add_debt(debtor.name, config.TOWN_FUND, 6,
                       "unpaid rent (test-fresh)", due_day=12)
    bank0, fund0 = w.tills[bank], w.tills[config.TOWN_FUND]
    w.ledger.debt_market()
    check("the bank buys the fund's arrears at face value",
          d1["creditor"] == bank and d2["creditor"] == bank and
          w.tills[bank] == round(bank0 - 12, 2) and
          w.tills[config.TOWN_FUND] == round(fund0 + 12, 2),
          f"bank {w.tills[bank]}, fund {w.tills[config.TOWN_FUND]}")
    check("fresh debt keeps its grace with the fund",
          fresh["creditor"] == config.TOWN_FUND, fresh["creditor"])
    check("purchase restarts the clock toward the levy",
          d1["assigned_day"] == 10 and d1["due_day"] == 10 + fc["grace_days"],
          f"due {d1['due_day']}")

    # reserve: the vault never spends below the floor
    w.tills[bank] = fc["bank_reserve"] + 5
    big = w.add_debt(debtor.name, config.TOWN_FUND, 50, "too big (test)",
                     due_day=1)
    w.ledger.debt_market()
    check("the bank keeps its reserve", big["creditor"] == config.TOWN_FUND
          and w.tills[bank] == fc["bank_reserve"] + 5, f"till {w.tills[bank]}")
    big["status"] = "paid"

    # grace passes unpaid: the levy
    house = debtor.home
    w.clock.day = 10 + fc["grace_days"] + 1
    w.ledger.foreclosure_sweep()
    loc = w.locations[house]
    check("the bank takes the house", debtor.home is None and
          loc.get("for_sale") == fc["house_price"] and
          loc.get("seized_from") == debtor.name and
          "home_of" not in loc, str(loc)[:60])
    fore_ev = [ev for ev in w.events
               if "FORECLOSURE" in str(ev.get("text", ""))]
    check("the levy is town news", len(fore_ev) == 1,
          fore_ev[0]["text"][:60] if fore_ev else "no event")

    # a rich neighbor buys the deed at the teller window
    buyer = next(a for a in w.agents.values()
                 if a.name != debtor.name and a.home)
    buyer.money = 100
    buyer.location = bank
    owed_before = sum(d["amount"] for d in w.open_debts(
        debtor=debtor.name, creditor=bank) if d.get("assigned_day"))
    dm0 = debtor.money
    ok, note = w.execute(buyer, {"action": "buy", "item": house})
    surplus = round(fc["house_price"] - owed_before, 2)
    check("the sale pays the debt and returns the surplus", ok and
          buyer.money == 100 - fc["house_price"] and
          not [d for d in w.open_debts(debtor=debtor.name, creditor=bank)
               if d.get("assigned_day")] and
          debtor.money == round(dm0 + max(0, surplus), 2) and
          w.locations[house].get("owner") == buyer.name, note[:70])
    # a landlord does not move in — but the deed still makes it PRIVATE.
    # (Pre-v2.9.2 this asserted no home_of at all, which is exactly what let
    # the town wander into and build on a man's second house.)
    check("a deed on a second house makes a landlord, not a move",
          buyer.home != house and
          w.locations[house].get("home_of") == buyer.name and
          house not in w.public_locations(),
          buyer.home)

    # redemption: pay in full BEFORE the sale and the door comes back
    debtor2 = next(a for a in w.agents.values()
                   if a.home and a.name not in (debtor.name, buyer.name))
    h2 = debtor2.home
    w.add_debt(debtor2.name, bank, 14, "bought paper (test)", due_day=1)
    for d in w.open_debts(debtor=debtor2.name, creditor=bank):
        d["assigned_day"] = 1
    w.ledger.foreclosure_sweep()
    assert debtor2.home is None, "setup: second levy"
    debtor2.money = 20
    debtor2.location = bank
    ok, note = w.execute(debtor2, {"action": "pay", "to": "the bank",
                                   "amount": 14})
    check("paid in full buys the door back", ok and debtor2.home == h2 and
          w.locations[h2].get("home_of") == debtor2.name and
          "for_sale" not in w.locations[h2], note[:60])

    # a bought deed ends rent
    w.clock.day = config.RENT_EVERY_DAYS * 40 + 1
    w.ledger.rent_day_done = 0
    w.ledger.swept_day = w.clock.day - 1
    # give everyone rent money so only the ownership exemption differs
    for a in w.agents.values():
        a.money = max(a.money, 50)
    owner_money0 = buyer.money
    # move the buyer INTO the owned house to test the exemption
    buyer.home = house
    w.locations[house]["home_of"] = buyer.name
    w.morning_ledger()
    check("owning your home ends rent", buyer.money == owner_money0,
          f"${buyer.money} vs ${owner_money0}")
    fresh_data()

def _situations_vacant():
    fresh_data()
    e = Engine(seed=78)
    w = e.world
    openings = w.open_positions()
    jobless = next((a for a in w.agents.values() if not a.workplace()), None)
    if jobless is None:
        a0 = next(iter(w.agents.values()))
        a0.job = "retired"
        jobless = a0
        openings = w.open_positions()
    check("a town with idle hands posts its openings",
          isinstance(openings, dict), str(openings)[:60])
    if openings:
        job, wp = sorted(openings.items())[0]
        jobless.location = wp
        ok, note = w.execute(jobless, {"action": "work"})
        check("showing up is the interview", ok and jobless.job == job and
              jobless.workplace() == wp, note[:60])
        check("the new hire is on today's attendance",
              jobless.name in w.worked_today, "")
        # the bell tells the jobless where the doors are
        still_jobless = next((a for a in w.agents.values()
                              if not a.workplace()), None)
        if still_jobless and w.open_positions():
            w.clock.day = 3
            w.morning_bell()
            check("the bell advertises situations vacant",
                  any("HIRING" in p["text"]
                      for p in still_jobless.pending), "")
    fresh_data()

def _old_config_boot_v27():
    """v2.4.1's law, re-sworn for v2.7: a config written before the Holt
    Act must boot and sweep untouched."""
    fresh_data()
    had_fc = getattr(config, "FORECLOSURE", None)
    had_hire = getattr(config, "HIRING_ENABLED", None)
    try:
        if hasattr(config, "FORECLOSURE"):
            del config.FORECLOSURE
        if hasattr(config, "HIRING_ENABLED"):
            del config.HIRING_ENABLED
        e = Engine(seed=79)
        w = e.world
        w.clock.day = 5
        w.morning_ledger()   # sweeps, markets, forecloses — without the knobs
        w.morning_bell()
        w.attendance_ledger()
        check("a pre-Holt config boots and sweeps clean", True, "")
    except Exception as exc:
        check("a pre-Holt config boots and sweeps clean", False, repr(exc))
    finally:
        if had_fc is not None:
            config.FORECLOSURE = had_fc
        if had_hire is not None:
            config.HIRING_ENABLED = had_hire
    fresh_data()

def _bus_route():
    """v2.8: outside money, and it only enters through an open door."""
    from sim import bus as busmod
    fresh_data()
    e = Engine(seed=81)
    w = e.world
    c = busmod.cfg()

    # a shut town earns NOTHING from a busload of money
    w.clock.day = c["every_days"] * 3       # an arrival day
    e.bus.day_done = 0
    tills0 = dict(w.tills)
    e.bus._arrive(c)
    check("the coach arrives with visitors", e.bus.visiting >= c["visitors"][0],
          f"{e.bus.visiting} aboard")
    for _ in range(6):
        e.bus._shop(c)
    check("closed doors take no money from tourists",
          all(w.tills[k] == tills0[k] for k in tills0),
          str({k: w.tills[k] for k in tills0})[:70])
    e.bus._depart()
    left_broke = [ev for ev in w.events
                  if "found every door shut" in str(ev.get("text", ""))]
    check("the town is told the bus left with the money",
          len(left_broke) == 1, "")

    # now open a door: money flows, and ONLY to the open business
    worker = next(a for a in w.agents.values()
                  if a.workplace() and a.workplace() in w.tills
                  and not w.locations.get(a.workplace(), {}).get("bank"))
    shop = worker.workplace()
    worker.location = shop
    w.execute(worker, {"action": "work"})
    w.clock.day += c["every_days"]
    e.bus.day_done = 0
    e.bus._arrive(c)
    before = dict(w.tills)
    for _ in range(6):
        e.bus._shop(c)
    gained = {k: round(w.tills[k] - before.get(k, 0), 2)
              for k in w.tills if round(w.tills[k] - before.get(k, 0), 2) > 0}
    check("an open door takes the tourists' money",
          w.tills[shop] > before[shop], f"{shop}: +${gained.get(shop, 0)}")
    check("money reaches ONLY the business that opened",
          set(gained) <= {shop}, str(gained)[:70])

    # the receipts are public, and the departure names the takings
    e.bus._depart()
    receipts = [ev for ev in w.events
                if "left $" in str(ev.get("text", ""))
                and "in this town today" in str(ev.get("text", ""))]
    check("the takings are read out town-wide", len(receipts) == 1,
          receipts[0]["text"][:70] if receipts else "no receipt event")

    # the chain that saves a bartender: work an empty till for IOUs,
    # tourists arrive, the till fills, back wages settle automatically
    barkeep = next((a for a in w.agents.values()
                    if a.workplace() and w.locations.get(
                        a.workplace(), {}).get("bar")), None)
    if barkeep:
        bar = barkeep.workplace()
        w.tills[bar] = 0.0
        barkeep.location = bar
        barkeep.money = 0.0
        w.execute(barkeep, {"action": "work"})
        w.pay_wage(barkeep, 6)
        owed = w.wage_debt_of(bar)
        check("an empty till pays in IOUs, not cash",
              owed > 0 and barkeep.money == 0, f"owed ${owed}")
        w.clock.day += c["every_days"]
        e.bus.day_done = 0
        e.bus._arrive(c)
        for _ in range(8):
            e.bus._shop(c)
        check("tourist money settles the back wages automatically",
              barkeep.money > 0 and w.wage_debt_of(bar) < owed,
              f"${barkeep.money:.2f} paid, ${w.wage_debt_of(bar):.2f} left")
    fresh_data()

def _old_config_boot_v28():
    """A config written before the bus route must boot and run a day."""
    fresh_data()
    had_bus = getattr(config, "BUS", None)
    try:
        if hasattr(config, "BUS"):
            del config.BUS
        e = Engine(seed=82)
        e.world.clock.day = 4
        for _ in range(40):
            e.step()
        check("a pre-bus config boots and ticks clean", True, "")
    except Exception as exc:
        check("a pre-bus config boots and ticks clean", False, repr(exc))
    finally:
        if had_bus is not None:
            config.BUS = had_bus
    fresh_data()

def _crane_bonus():
    """v2.9: the carrot. Ships DARK — the first check is that it changes
    nothing at all until somebody arms it."""
    fresh_data()
    e = Engine(seed=83)
    w = e.world
    worker = next(a for a in w.agents.values()
                  if a.workplace() and a.workplace() in w.tills)
    shop = worker.workplace()

    # DARK: no multiplier, no streak accounting, no event.
    #
    # v3.3.2 — FORCED dark here instead of asserting the config says dark.
    # Brad armed the Crane Bonus for window 2 and this started failing on
    # his live town, which is the tell: it was asserting HIS CONFIGURATION
    # rather than the code's behaviour. Same mistake as the golden hash
    # importing config.py. A test may read code; it may not read choices.
    _l, _had_l = snapshot_knob("LOYALTY")
    config.LOYALTY = dict(_l, enabled=False)
    try:
        check("a dark Crane Bonus changes nothing at all",
              w.loyalty_cfg()["enabled"] is False and
              w.wage_multiplier(worker.name) == 1.0 and
              w.loyalty_steps(worker.name) == 0, "")
        w.work_streaks[worker.name] = 99
        check("a dark Act pays no raise even on a long run",
              w.wage_multiplier(worker.name) == 1.0, "")
        w.work_streaks = {}
    finally:
        restore_knob("LOYALTY", _l, _had_l)

    # arm it
    old, had_old = snapshot_knob("LOYALTY")
    try:
        config.LOYALTY = dict(old, enabled=True, streak_days=3,
                              raise_pct=0.25, max_steps=2,
                              milestone_bonus=12)
        cfgL = w.loyalty_cfg()

        # a run of shifts earns a step, a public event, and cash in hand
        w.tills[config.TOWN_FUND] = 200.0
        w.tills[shop] = 300.0
        fund0 = w.tills[config.TOWN_FUND]
        money0 = worker.money
        for day in range(1, cfgL["streak_days"] + 1):
            w.clock.day = day
            w._attendance_day_done = day - 1
            w.worked_today = {worker.name}
            w.attendance_ledger()
        check("a run of shifts earns a step",
              w.work_streaks[worker.name] == cfgL["streak_days"] and
              w.loyalty_steps(worker.name) == 1,
              f"streak {w.work_streaks[worker.name]}")
        check("the milestone is paid from the town fund, in hand",
              abs(worker.money
                  - (money0 + cfgL["milestone_bonus"])) < 0.01 and
              w.tills[config.TOWN_FUND] == round(
                  fund0 - cfgL["milestone_bonus"], 2),
              f"${worker.money} / fund ${w.tills[config.TOWN_FUND]}")
        bonus_ev = [ev for ev in w.events
                    if "THE CRANE BONUS" in str(ev.get("text", ""))]
        check("the town is told who showed up", len(bonus_ev) == 1,
              bonus_ev[0]["text"][:70] if bonus_ev else "no event")
        check("the raise is real money",
              abs(w.wage_multiplier(worker.name)
                  - (1 + cfgL["raise_pct"])) < 1e-9,
              str(w.wage_multiplier(worker.name)))
        worker.money = 0.0
        w.pay_wage(worker, 10)
        check("the raise reaches the wage packet", worker.money > 10 * 0.85,
              f"${worker.money:.2f} on a $10 base shift")

        # the ledger reads the run out loud beside the absentees
        line = [ev for ev in w.events
                if "ATTENDANCE LEDGER" in str(ev.get("text", ""))][-1]["text"]
        check("the run is read out at the evening ledger",
              "days running" in line and "%" in line, line[:80])

        # ceiling holds
        w.work_streaks[worker.name] = cfgL["streak_days"] * 20
        check("the raise has a ceiling",
              w.loyalty_steps(worker.name) == cfgL["max_steps"], "")

        # one missed day and it all resets
        w.clock.day += 1
        w._attendance_day_done = w.clock.day - 1
        w.worked_today = set()
        w.attendance_ledger()
        check("missing a day resets the raise to base",
              w.work_streaks[worker.name] == 0 and
              w.wage_multiplier(worker.name) == 1.0, "")
        check("the worker is told the run is broken",
              any("broke your run" in p["text"] for p in worker.pending), "")
    finally:
        restore_knob("LOYALTY", old, had_old)
    fresh_data()

_holt_act()
_situations_vacant()
_old_config_boot_v27()
_bus_route()
def _tibbs_door():
    """v2.9.1: a dry till may withhold the WAGE. It may not lock the DOOR.
    (Vera Tibbs, Pompeii Day 70 — sent home three times, the last with five
    paying tourists standing in the plaza.)"""
    from sim import bus as busmod
    fresh_data()
    e = Engine(seed=84)
    w = e.world
    barkeep = next(a for a in w.agents.values()
                   if a.workplace() and a.workplace() in w.tills
                   and not w.locations.get(a.workplace(), {}).get("bank"))
    bar = barkeep.workplace()

    # a dead till, over the wage-debt cap — the old "sent home" state
    w.tills[bar] = 0.0
    w.add_debt(bar, barkeep.name, config.WAGE_DEBT_CAP + 10,
               f"back wages at {bar}")
    barkeep.location = bar
    barkeep.money = 0.0
    ok, note = w.execute(barkeep, {"action": "work"})
    check("a dry till no longer locks the door", ok and
          barkeep.name in w.worked_today and
          (barkeep.activity or {}).get("type") == "work", note[:80])
    check("the villager is told the register is dry, not that they're fired",
          "DOORS ARE OPEN" in note, note[:60])
    open_ev = [ev for ev in w.events
               if "on an empty till" in str(ev.get("text", ""))]
    check("opening on an empty register is a public act", len(open_ev) == 1,
          open_ev[0]["text"][:70] if open_ev else "no event")

    # and now the whole point: tourists can find that door
    c = busmod.cfg()
    e.bus.visiting = 5
    e.bus.spent_today = {}
    for _ in range(6):
        e.bus._shop(c)
    check("an open door on a dead till takes the tourists' money",
          e.bus.brought_in > 0,
          f"${e.bus.brought_in:.2f} came off the bus")
    # the till reads empty afterward because the money did not STOP there —
    # it passed straight through the register into the pocket of the person
    # who opened the door on an empty one. That is the whole chain.
    check("and the takings settle what the worker was owed",
          barkeep.money > 0 and w.wage_debt_of(bar) < config.WAGE_DEBT_CAP,
          f"${barkeep.money:.2f} in hand, ${w.wage_debt_of(bar):.2f} still owed")

    # public payroll keeps the old law: no door at the park to open
    public = next((a for a in w.agents.values()
                   if a.workplace() and w._wage_till_key(a) == config.TOWN_FUND
                   and a.name != barkeep.name), None)
    if public:
        w.tills[config.TOWN_FUND] = 0.0
        w.add_debt(config.TOWN_FUND, public.name,
                   config.WAGE_DEBT_CAP + 10, "back wages at the town fund")
        public.location = public.workplace()
        ok2, note2 = w.execute(public, {"action": "work"})
        check("a broke town can still send a public worker home",
              not ok2 and "no shifts today" in note2, note2[:60])
    fresh_data()

_old_config_boot_v28()
def _review_fixes_v292():
    """The external review of v2.9.1. Every finding gets a regression."""
    from sim import bus as busmod
    fresh_data()
    e = Engine(seed=85)
    w = e.world

    # 1. REFUSED IS NOT ABSENT. A public worker sent home for a broke town
    #    fund was being counted an absentee AND stripped of his Crane Bonus.
    old, had_old = snapshot_knob("LOYALTY")
    try:
        config.LOYALTY = dict(old, enabled=True, streak_days=3,
                              raise_pct=0.25, max_steps=2, milestone_bonus=5)
        public = next(a for a in w.agents.values()
                      if a.workplace() and w._wage_till_key(a) == config.TOWN_FUND)
        w.work_streaks[public.name] = 6          # a real earned raise
        w.absence_streaks[public.name] = 0
        w.tills[config.TOWN_FUND] = 0.0
        w.add_debt(config.TOWN_FUND, public.name,
                   config.WAGE_DEBT_CAP + 10, "back wages at the town fund")
        public.location = public.workplace()
        w.clock.day = 5
        w._bell_day_done = 5
        w.worked_today = set()
        w.turned_away_today = set()
        ok, _ = w.execute(public, {"action": "work"})
        check("a broke town still turns a public worker away", not ok, "")
        check("but the town records that he SHOWED UP",
              public.name in w.turned_away_today, "")
        w._attendance_day_done = 4
        w.attendance_ledger()
        check("being refused does not break the run",
              w.work_streaks[public.name] == 6 and
              w.loyalty_steps(public.name) == 2,
              f"streak {w.work_streaks[public.name]}")
        check("being refused is not an absence",
              w.absence_streaks.get(public.name, 0) == 0, "")
        line = [ev for ev in w.events
                if "ATTENDANCE LEDGER" in str(ev.get("text", ""))][-1]["text"]
        check("the ledger names the payroll, not the man",
              "TURNED AWAY" in line and
              public.name.split()[0] not in line.split("DOORS NEVER OPENED")[-1],
              line[:90])
    finally:
        restore_knob("LOYALTY", old, had_old)
    fresh_data()

    # 2. RENT LANDS BEFORE THE SWEEP: a trickle of fresh rent must not open
    #    the payroll gate while back-wage debt is still over the cap.
    e = Engine(seed=86)
    w = e.world
    public = next(a for a in w.agents.values()
                  if a.workplace() and w._wage_till_key(a) == config.TOWN_FUND)
    w.tills[config.TOWN_FUND] = 0.0
    w.add_debt(config.TOWN_FUND, public.name,
               config.WAGE_DEBT_CAP * 3, "back wages at the town fund")
    w.tills[config.TOWN_FUND] = 6.0          # one tenant's rent, mid-tick
    w.settle_business_debts()                 # the sweep now runs AFTER rent
    # note the residue: settling the fund's own back wages hands the income
    # tax on that payment straight back to the fund, so it never reads 0.00
    check("fresh rent is swallowed by the debt it owes, not spent on new hires",
          w.tills[config.TOWN_FUND] < getattr(config, "WAGE_PER_SHIFT_TICK", 2)
          and w.wage_debt_of(config.TOWN_FUND) >= config.WAGE_DEBT_CAP,
          f"fund ${w.tills[config.TOWN_FUND]:.2f}, "
          f"owed ${w.wage_debt_of(config.TOWN_FUND):.2f}")
    public.location = public.workplace()
    ok, _ = w.execute(public, {"action": "work"})
    check("the payroll gate stays shut on an insolvent fund", not ok, "")
    fresh_data()

    # 3. A LANDLORD'S SECOND HOUSE IS PRIVATE PROPERTY, not public land
    e = Engine(seed=87)
    w = e.world
    bank = w.bank_name()
    owner = next(a for a in w.agents.values() if a.home)
    victim = next(a for a in w.agents.values()
                  if a.home and a.name != owner.name)
    house = victim.home
    w.add_debt(victim.name, bank, 30, "bought paper (test)", due_day=1)
    for d in w.open_debts(debtor=victim.name, creditor=bank):
        d["assigned_day"] = 1
    w.clock.day = 9
    w.ledger.foreclosure_sweep()
    owner.money = 200.0
    owner.location = bank
    ok, note = w.execute(owner, {"action": "buy", "item": house})
    check("a second deed is bought", ok and
          w.locations[house].get("owner") == owner.name, note[:60])
    check("and the town cannot wander or build on it",
          house not in w.public_locations() and
          w.locations[house].get("home_of") == owner.name, "")
    fresh_data()

    # 4. EVERY faucet is audited, not just the bus
    e = Engine(seed=88)
    w = e.world
    def town_money():
        return round(sum(a.money for a in w.agents.values())
                     + sum(w.tills.values()), 2)
    start, flow0 = town_money(), w.outside_flow
    e.director.trigger("windfall")
    check("the Director's windfall is audited",
          abs((town_money() - start) - (w.outside_flow - flow0)) < 0.01,
          f"money +{town_money() - start:.2f}, "
          f"flow +{w.outside_flow - flow0:.2f}")
    start, flow0 = town_money(), w.outside_flow
    w.tills[config.TOWN_FUND] = 200.0
    start, flow0 = town_money(), w.outside_flow
    e.director.trigger("heist")
    check("the heist is audited as a money SINK",
          abs((town_money() - start) - (w.outside_flow - flow0)) < 0.01
          and w.outside_flow < flow0,
          f"money {town_money() - start:.2f}, flow {w.outside_flow - flow0:.2f}")
    fresh_data()

    # 5. RESOLVED PAPER IS PRUNED; open paper is never touched
    e = Engine(seed=89)
    w = e.world
    keep = getattr(config, "LEDGER_HISTORY_KEEP", 200)
    debtor = next(iter(w.agents))
    for i in range(keep + 40):
        d = w.add_debt(debtor, config.TOWN_FUND, 1, f"settled {i}")
        d["status"] = "paid"
    live = w.add_debt(debtor, config.TOWN_FUND, 5, "still owed")
    w.ledger.prune_resolved()
    done = [d for d in w.debts if d["status"] != "open"]
    check("settled paper stops accumulating forever", len(done) == keep,
          f"{len(done)} resolved rows kept")
    check("open paper is never pruned", live in w.debts, "")
    fresh_data()

    # 6. the config knob the review caught reading raw
    import inspect
    from sim import world as worldmod
    src = inspect.getsource(worldmod)
    check("no raw config reads of optional knobs in permit_sweep",
          "config.CONDEMN_GRACE_DAYS" not in src, "")

    # 7. durable store resources can be released (Windows harness gap)
    e = Engine(seed=90)
    worldmod.World.close_all()
    check("worlds release their durable stores on demand",
          e.world._closed, "")
    fresh_data()

def _vitals():
    """v2.9.3: the readout that would have caught the insolvency bug on
    sight. Pure observation — it must not touch a single law."""
    fresh_data()
    e = Engine(seed=91)
    w = e.world
    v = e.vitals()
    check("the town publishes vitals",
          isinstance(v, dict) and "wealth_median" in v and
          "debt_businesses" in v and "days_since_foreclosure" in v,
          ", ".join(sorted(v)[:5]) if v else "none")
    check("vitals appear in the snapshot the observatory reads",
          "vitals" in e._snapshot_locked(), "")

    # the exact blindness that hid the bug: a fund quietly over its cap
    public = next(a for a in w.agents.values()
                  if a.workplace() and w._wage_till_key(a) == config.TOWN_FUND)
    w.add_debt(config.TOWN_FUND, public.name, config.WAGE_DEBT_CAP * 5,
               "back wages at the town fund")
    v = e.vitals()
    check("unpaid wages are visible without reading code",
          v["debt_businesses"] >= config.WAGE_DEBT_CAP * 5,
          f"${v['debt_businesses']}")

    # foreclosure recency
    check("a town with no foreclosure reports none",
          v["days_since_foreclosure"] is None, str(v["days_since_foreclosure"]))
    w.last_foreclosure_day = w.clock.day - 3
    check("and reports the gap once one happens",
          e.vitals()["days_since_foreclosure"] == 3, "")

    # employment is the headline number
    worker = next(a for a in w.agents.values()
                  if a.workplace() and a.workplace() in w.tills)
    w.tills[worker.workplace()] = 100.0
    worker.location = worker.workplace()
    w.execute(worker, {"action": "work"})
    v = e.vitals()
    check("employment is counted honestly",
          v["worked_today"] == 1 and v["employed"] >= 1,
          f"{v['worked_today']}/{v['employed']}")
    fresh_data()

def _provenance():
    """v2.9.4: the instrument that would have saved ten days.

    A queue overflow seated MockBrain silently for days. Mock is written to
    be lifelike — it goes to work between 8 and noon, it buys a cheap house
    when flush — so the transcript looked like character and was script.
    Nothing recorded WHO decided. Now everything does."""
    fresh_data()
    e = Engine(seed=92)
    w = e.world

    cases = {
        "model decision": "model",
        "host unreachable; understudy acted: mock: starting shift": "understudy",
        "mock: bedtime": "understudy",
        "host unreachable": "dark",
        "unparseable reply": "unparsed",
        "possessed: external action": "possessed",
    }
    bad = {r: e.decision_source(r) for r, want in cases.items()
           if e.decision_source(r) != want}
    check("every decision reason is classified", not bad, str(bad)[:90])

    # a town of live minds reports itself clean
    for a in w.agents.values():
        e._record_provenance(a, "model decision")
    rep = e.minds_report()
    check("all-live reads live", rep["live"] == rep["total"] == len(w.agents)
          and not rep["understudied"], str(rep)[:80])

    # one villager drops to the understudy and the town says so
    victim = next(iter(w.agents.values()))
    e._record_provenance(
        victim, "host unreachable; understudy acted: mock: starting shift")
    rep = e.minds_report()
    check("a single dark mind is named, not averaged away",
          rep["live"] == len(w.agents) - 1 and
          rep["understudied"] == [victim.name], str(rep)[:80])
    check("the villager carries the mark",
          victim.last_source == "understudy", str(victim.last_source))

    # and the observatory sees it on the strip
    v = e.vitals()
    check("minds reach the vitals strip",
          v["minds"]["live"] == len(w.agents) - 1 and
          victim.name in v["minds"]["understudied"], str(v["minds"])[:80])

    # THE SLEEPING ARE NOT MISSING. Every town read 6/7 red overnight
    # because one villager was in bed and therefore unstamped.
    for a in w.agents.values():
        e._record_provenance(a, "model decision")
    sleeper = next(iter(w.agents.values()))
    sleeper.asleep = True
    sleeper.last_source = None          # asleep, so never stamped
    rep = e.minds_report()
    check("a sleeping villager is not a missing mind",
          rep["live"] == rep["awake"] == len(w.agents) - 1 and
          rep["asleep"] == 1 and rep["state"] == "good", str(rep)[:100])
    sleeper.asleep = False
    check("but an awake villager not yet observed is only amber",
          e.minds_report()["state"] == "warn", "")
    e._record_provenance(sleeper, "host unreachable; understudy acted: mock")
    check("and a faked villager is red",
          e.minds_report()["state"] == "bad", "")
    # put the mark back on the victim: the restart check below needs it
    e._record_provenance(
        victim, "host unreachable; understudy acted: mock: starting shift")

    # provenance survives a restart — an experiment spanning a bounce must
    # not forget that its instruments were leaking
    e.save_state()
    e2 = Engine(state=Engine.load_state())
    check("provenance survives a restart",
          e2.world.agents[victim.name].last_source == "understudy",
          str(e2.world.agents[victim.name].last_source))
    fresh_data()

def _townsfolk():
    """v3.0: the town gets people in it. Ships dark. NEVER gives charity."""
    from sim import townsfolk as tfmod
    fresh_data()
    e = Engine(seed=93)
    w = e.world

    # FORCED dark, for the same reason as the Crane Bonus above (v3.3.2).
    _t, _had_t = snapshot_knob("TOWNSFOLK")
    config.TOWNSFOLK = dict(_t, enabled=False)
    try:
        check("a dark street is empty", tfmod.cfg()["enabled"] is False
              and not e.folk.people, "")
        e.folk.step()
        check("a dark town has nobody in it", not e.folk.people, "")
    finally:
        restore_knob("TOWNSFOLK", _t, _had_t)

    old, had_old = snapshot_knob("TOWNSFOLK")
    try:
        config.TOWNSFOLK = dict(old, enabled=True, count=6,
                                shop_chance=1.0, move_chance=0.0,
                                speak_chance=0.0, oddjob_chance=0.0)
        e.folk.ensure()
        names = [p["name"] for p in e.folk.people]
        check("the town has townsfolk", len(names) == 6, ", ".join(names)[:70])
        # SOME MYSTERIES ARE LOAD-BEARING. v3.0.0 gave Rosie a body; an
        # external reviewer talked us out of it before anyone met her.
        check("ROSIE IS NOT AMONG THEM, AND NEVER WILL BE",
              "Rosie" not in names and
              not any(p.get("fixed") for p in e.folk.people),
              "she is a hole in the data model, and that is the point")

        # a SHUT door is witnessed, and no money moves
        shop = next(k for k, v in w.locations.items()
                    if v.get("bar") or (v.get("sells_food")
                                        and "Rosie" not in k))
        person = next(p for p in e.folk.people if not p["fixed"])
        person["location"] = shop
        w.tills[shop] = 0.0
        e.folk._act(person, tfmod.cfg())
        shut = [ev for ev in w.events
                if "found it shut" in str(ev.get("text", ""))]
        check("a closed door is SEEN to be tried and rejected",
              len(shut) == 1 and w.tills[shop] == 0.0,
              shut[0]["text"][:70] if shut else "no event")

        # an OPEN door takes money, and it lands in the till, not a pocket
        worker = next((a for a in w.agents.values()
                       if a.workplace() == shop), None)
        if worker:
            worker.location = shop
            worker.asleep = False
            w.execute(worker, {"action": "work"})
            before_till, before_purse = w.tills[shop], worker.money
            flow0 = w.outside_flow
            e.folk._act(person, tfmod.cfg())
            check("an open door takes their money INTO THE TILL",
                  w.tills[shop] > before_till, f"till ${w.tills[shop]:.2f}")
            check("and the outside money is audited",
                  abs((w.tills[shop] - before_till)
                      - (w.outside_flow - flow0)) < 0.01, "")

        # THE RULE, enforced against the source itself: there is exactly
        # ONE line in this module that puts money into a villager's pocket,
        # and it is inside take_offer — i.e. paid for work done.
        src = open(os.path.join(ROOT, "sim", "townsfolk.py")).read()
        body = src.split('"""', 2)[2]
        pays = [ln.strip() for ln in body.splitlines() if ".money +=" in ln]
        check("only ONE code path puts money in a villager's pocket",
              len(pays) == 1, " | ".join(pays)[:80])
        after_take = body.split("def take_offer", 1)
        check("and that path is take_offer — payment for work done",
              len(after_take) == 2 and ".money +=" in after_take[1]
              and ".money +=" not in after_take[0], "")
        check("the townsfolk never touch the poor box",
              "POOR_BOX" not in body and "poor box" not in body.lower(), "")

        # PAID WORK: the one way money reaches a villager without a till
        config.TOWNSFOLK = dict(old, enabled=True, count=6, shop_chance=0.0,
                                move_chance=0.0, speak_chance=0.0,
                                oddjob_chance=1.0, oddjob_pay=[6, 6])
        idler = next(a for a in w.agents.values() if not a.asleep)
        person["location"] = idler.location
        e.folk.offers = {}
        e.folk._act(person, tfmod.cfg())
        offer = e.folk.offer_at(idler.location)
        check("a townsperson offers paid work, in person, cash in hand",
              offer and offer["pay"] == 6, str(offer)[:70])
        check("and the villagers in the room are told how to take it",
              any("work action here" in p["text"] for p in idler.pending), "")
        purse0, fund0 = idler.money, w.tills[config.TOWN_FUND]
        ok, note = w.execute(idler, {"action": "work"})
        check("working there EARNS it", ok and idler.money > purse0,
              note[:70] if note else "")
        check("the odd job is taxed like any other wage",
              w.tills[config.TOWN_FUND] > fund0, "")
        # v3.3.1 — THIS ASSERTION USED TO SAY THE OPPOSITE, and it was the
        # bug. An odd job landing in worked_today let an employed villager
        # stand in their own dark shop, take a townsperson's $5, and be
        # posted as ON SHIFT with their Crane Bonus streak intact.
        check("an odd job is EARNED work but is NOT a shift at your own door",
              idler.name in w.odd_jobs_today
              and idler.name not in w.worked_today,
              "counted apart, so attendance cannot be laundered")
        check("an offer is taken once and once only",
              e.folk.offer_at(idler.location) is None, "")

        # the map can tell scenery from a mind
        snap = e._snapshot_locked()
        check("townsfolk reach the map flagged as NPCs",
              snap["townsfolk"] and all(p["npc"] for p in snap["townsfolk"]),
              str(len(snap["townsfolk"])) + " on the map")
        check("and they are NOT counted as minds",
              snap["vitals"]["minds"]["total"] == len(w.agents), "")
    finally:
        restore_knob("TOWNSFOLK", old, had_old)
    fresh_data()

def _no_charity_from_the_gods():
    """Brad's ruling, Day 91: 'no charity what so ever. work-reward.'
    The Director's windfall conjured money into a pocket for nothing."""
    check("the gods no longer hand out free money",
          "windfall" not in config.CHAOS["weights"],
          str(sorted(config.CHAOS["weights"])))
    check("but an old config that still lists it can boot",
          hasattr(__import__("sim.director", fromlist=["Director"]),
                  "Director"), "")

_crane_bonus()
_tibbs_door()
_review_fixes_v292()
_vitals()
_provenance()
def _town_report():
    """v3.0.1: the report is how a private terrarium becomes shareable.
    Read-only — it must never write into a town's data directory."""
    import subprocess
    fresh_data()
    e = Engine(seed=94)
    e.run_headless(120)
    e.save_state()
    from sim.world import World
    World.close_all()
    data_dir = os.path.join(SCRATCH, "data")
    before = sorted(os.listdir(data_dir))
    out = os.path.join(SCRATCH, "report.html")
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "tools", "townreport.py"),
         "--data", data_dir, "--out", out, "--town", "Testville"],
        capture_output=True, text=True, cwd=ROOT)
    check("the town report builds from a data directory",
          proc.returncode == 0 and os.path.exists(out),
          (proc.stderr or proc.stdout)[-90:])
    if os.path.exists(out):
        page = open(out, encoding="utf-8").read()
        check("it is one self-contained file — no scripts, no network",
              "<script" not in page and "http://" not in page
              and "https://" not in page and "src=" not in page, "")
        check("it carries the cast, the laws and the money",
              "Testville" in page and "WHO LIVES HERE" in page.upper()
              and "THE LAWS THAT FIRED" in page.upper()
              and "<svg" in page, "")
        check("and it renders in both light and dark",
              "prefers-color-scheme: dark" in page
              and 'data-theme="dark"' in page, "")
        # v3.1.0: the report must never publish a script as a finding
        check("it asks whether the town was real, and answers from the ledger",
              "Was any of this real?" in page
              and "ran in mock mode" in page,
              "mock run declared a rehearsal")
        check("and it names every hand that reached in",
              "reached in" in page, "")
        check("a mock town is NOT advertised as a town of language models",
              "minds switched off" in page
              and "Nobody is playing them" not in page,
              "the headline claim follows the ledger")

    # v3.1.0: the jar row used to count villagers SAYING 'poor box'
    from tools.townreport import build, LAW_EVENT_TYPES
    talk = [{"type": "say", "agent": "A", "day": 1,
             "text": "we should all put something in the poor box"}] * 50
    deed = [{"type": "action", "agent": "A", "day": 1,
             "text": "dropped $5 in the poor box on the diner counter"}]
    rows = {l["label"]: l["n"] for l in build({}, talk + deed)["laws"]}
    check("a law counts DEEDS, not villagers talking about the law",
          rows.get("the jar") == 1, f"jar={rows.get('the jar')} (50 said, 1 did)")
    check("and talk alone fires no law at all",
          not build({}, talk)["laws"], "")

    # v3.2.0 — THE DAY 100 GAP. This report could say Walt had 185 shifts and
    # that 9 shifts happened in the last eleven days, and could NOT say
    # whether any of those 9 were his. Settling the verdict took a
    # hand-written script against the raw transcript. Rebuilt here in the
    # exact shape of the real Day 90-100 data: Walt 9, Ash 1, five idle.
    day100 = ([{"type": "action", "agent": "Walt Crane", "day": d,
                "sim_time": "08:00", "text": "started a shift at Rosie's Diner"}
               for d in (90, 94, 94, 95, 95, 96, 97, 97, 100)]
              + [{"type": "action", "agent": "Ash Holt", "day": 90,
                  "sim_time": "11:15",
                  "text": "started a shift at First Bank of Pepperton"}]
              + [{"type": "action", "agent": "Nora Tibbs", "day": 40,
                  "sim_time": "09:00", "text": "started a shift at the Rusty Tap"}])
    w = {n: e for n, e in build({}, day100)["window_work"]}
    check("the report can name WHO worked in the window, not just how many",
          sorted(w) == ["Ash Holt", "Walt Crane"]
          and len(w["Walt Crane"]) == 9 and len(w["Ash Holt"]) == 1,
          "Walt 9, Ash 1 — the Day 100 answer, from the report itself")
    check("and an old shift outside the window is NOT counted in it",
          "Nora Tibbs" not in w, "her Day 40 shift stays out of Day 90-100")
    attempt = [{"type": "action", "agent": "Frank Grady", "day": 100,
                "sim_time": "08:00",
                "text": "showed up for a shift and was sent home — "
                        "the till is dry"}]
    fw = dict(build({}, attempt)["window_work"])
    check("showing up and being sent home is recorded as an ATTEMPT",
          fw.get("Frank Grady") and fw["Frank Grady"][0][2] == "turned away",
          "the pre-registered bar counts it; the report must show it")

    check("the report NEVER writes into the town's data directory",
          sorted(os.listdir(data_dir)) == before,
          str(set(os.listdir(data_dir)) ^ set(before)))
    fresh_data()

def _experiment_ledger():
    """v3.1.0: the ledger is what prevents the television from lying.

    We narrated MockBrain's own house-buying heuristic as emergent
    capitalism for ten days because nothing in any record said what was
    actually running. These checks assert the record now exists, that it
    counts what it claims to count, and — most importantly — that it is
    INERT: a ledger that could move a town is worse than no ledger."""
    import json
    fresh_data()
    e = Engine(seed=311)
    e.run_headless(30)

    run = e.exp.run
    check("a run opens itself the moment the first tick lands",
          run is not None and run["run_id"], run["run_id"] if run else "none")
    check("it records what code and config were live",
          bool(run["code_version"]) and bool(run["config_hash"])
          and bool(run["prompts_hash"]),
          f"{run['code_version']} cfg={run['config_hash']}")
    check("it records the seed and whether the minds were real",
          run["seed"] == 311 and run["mock_mode"] is True, "")
    check("it records the cast WITH their models and hosts",
          len(run["cast"]) == len(e.world.agents)
          and all(c["model"] for c in run["cast"]), "")
    # v3.3.2: assert the ledger REPORTS REALITY, not that reality happens
    # to be the default. This used to require townsfolk == False, so it
    # failed on the very town that had armed them — a ledger test that
    # only passes on an unarmed town is worse than no ledger test.
    from sim import townsfolk as _tf
    armed = run["laws_armed"]
    check("it records which optional laws were armed",
          "foreclosure" in armed and "bus" in armed
          and armed["townsfolk"] == bool(_tf.cfg()["enabled"])
          and armed["loyalty"] == bool(e.world.loyalty_cfg()["enabled"]),
          f"townsfolk={armed['townsfolk']} loyalty={armed['loyalty']}")

    integ = e.exp.integrity()
    check("it counts every decision by who made it",
          integ["decisions"] > 0 and integ["live_pct"] is not None,
          f"{integ['decisions']} decisions, {integ['live_pct']}% live")
    check("mock minds are NOT counted as live thought",
          integ["live"] == 0 and integ["understudy"] == integ["decisions"],
          f"live={integ['live']} understudy={integ['understudy']}")
    check("and a mock run is therefore never 'clean'",
          integ["clean"] is False, "")

    # interventions: the whole reason this file exists
    before = len(run["interventions"])
    e.exp.note_intervention("api", "possess Walt Crane", 99, 4)
    check("a human reaching into the town is recorded, with the tick",
          len(run["interventions"]) == before + 1
          and run["interventions"][-1]["tick"] == 99,
          run["interventions"][-1]["detail"])
    e.director.trigger("dead_air")
    check("and so is a Director event fired by hand",
          any(i["kind"] == "director" for i in run["interventions"]),
          str(run["director_events"]))

    # it survives to disk, honestly
    e.save_state()
    e.exp.close(e, "test")
    path = e.exp.path
    check("the ledger lands on disk as its own file",
          os.path.exists(path), path)
    if os.path.exists(path):
        book = json.load(open(path, encoding="utf-8"))
        rec = [r for r in book["runs"] if r["run_id"] == run["run_id"]]
        check("one record per run, not one per write",
              len(rec) == 1, f"{len(book['runs'])} runs on file")
        check("a closed run says when it ended and why",
              rec and rec[0]["ended_reason"] == "test"
              and rec[0]["closed_utc"], rec[0]["ended_reason"] if rec else "")
        # v3.2.1: checkpoint BEFORE close, or the closing hash describes a
        # state one save behind the one the run actually ended in
        check("and a closing state hash that matches the final checkpoint",
              bool(rec and rec[0].get("closed_state_hash")),
              rec[0].get("closed_state_hash") if rec else "")

    # v3.2.1 — THE SHUTDOWN GAP. Nothing called engine.stop(), so close()
    # never ran and every run stayed "running" forever. The ledger exists so
    # a run cannot lie about itself afterward; a run that cannot say it
    # finished is doing exactly that.
    import server.app as appmod
    src = open(appmod.__file__, encoding="utf-8").read()
    check("the server closes the books on the way out",
          'on_event("shutdown")' in src and "engine.stop()" in src, "")
    # Read the parse tree, not the text: the docstring above this handler
    # says "engine.stop()" in prose, and a string search happily matched the
    # explanation instead of the code.
    import ast as _ast
    calls = []
    for node in _ast.walk(_ast.parse(src)):
        if isinstance(node, _ast.AsyncFunctionDef) and node.name == "_shutdown":
            calls = [n.func.attr for n in _ast.walk(node)
                     if isinstance(n, _ast.Call)
                     and isinstance(n.func, _ast.Attribute)
                     and n.func.attr in ("save_state", "stop")]
    check("and checkpoints BEFORE it closes them",
          calls == ["save_state", "stop"],
          f"order matters — close() hashes the state file: {calls}")
    World.close_all()
    fresh_data()

    # THE ONE THAT MATTERS: the observer must not disturb the observed.
    # Same seed, ledger on and off, tick for tick.
    def fingerprint(disabled):
        fresh_data()
        eng = Engine(seed=808)
        eng.exp.enabled = not disabled
        eng.run_headless(60)
        w = eng.world
        shot = [(a.name, round(a.money, 2), a.location, a.asleep,
                 round(a.needs["energy"], 3)) for a in
                sorted(w.agents.values(), key=lambda x: x.name)]
        shot.append(("__tills__", sorted((k, round(v, 2))
                                         for k, v in w.tills.items())))
        World.close_all()
        return shot

    check("THE LEDGER MOVES NOTHING — same seed, on and off, identical",
          fingerprint(False) == fingerprint(True),
          "60 ticks, purses, positions, needs and tills")
    fresh_data()

def _an_old_town_upgrades():
    """v3.1.1. THE ONE THAT WAS MISSING.

    Brad upgraded a hundred-day Pepperton to v3.1.0 and the suite died on
    AttributeError: config has no INN_ROOM_COST. The sim was fine — every
    late knob is getattr'd in sim/. It was the TEST that assumed a knob
    existed, which is the worst possible file for that bug, because the
    suite is the only thing standing between an old town and a bad upgrade.
    It crashed instead of reporting, and for a few minutes it looked like
    the release had broken his town.

    So: strip every knob added since v2.4 and drive the whole world. If a
    line of this suite ever assumes a knob again, this fails first."""
    stripped = []
    for name in LATE_KNOBS:
        if hasattr(config, name):
            stripped.append((name, getattr(config, name)))
            delattr(config, name)
    try:
        fresh_data()
        e = Engine(seed=311)
        w = e.world
        w.clock.day = 6
        for _ in range(80):
            e.step()
        w.morning_ledger(); w.morning_bell(); w.attendance_ledger()
        e.save_state()
        Engine(seed=311).load_state()
        check("a town config written before any of this still runs",
              True, f"{len(stripped)} knobs removed: "
                    f"{', '.join(n for n, _ in stripped)}")
        # and the test file's own helpers must survive the same treatment
        check("and the suite reads every late knob through a code default",
              knob("INN_ROOM_COST") == 5
              and knob("POOR_BOX") == "the poor box"
              and knob("LOYALTY") == {}, "")
        check("the report still builds for a town with no modern knobs",
              bool(__import__("tools.townreport", fromlist=["build"])
                   .build({}, [])), "")
    except Exception as exc:
        check("a town config written before any of this still runs",
              False, repr(exc)[:150])
    finally:
        for name, value in stripped:
            setattr(config, name, value)
        World.close_all()
    fresh_data()

def _review_fixes_v331():
    """The third external review. Three confirmed findings, all reproduced
    before being fixed, all covered here so they cannot come back."""
    fresh_data()

    # ---- 1. THE INSPECTOR WAS COMPLETELY BROKEN ----------------------
    # engine.py referenced an undefined `agent` instead of the local `a`, so
    # EVERY call raised NameError. The headline feature of the whole project
    # — "see any villager's memories and the exact prompt behind their last
    # decision" — shipped with no regression test and did not work at all.
    e = Engine(seed=7)
    e.run_headless(6)
    name = list(e.world.agents)[0]
    try:
        chart = e.inspect(name)
        ok = isinstance(chart, dict) and "recent_memories" in chart
    except Exception as exc:
        chart, ok = None, False
        check("the villager inspector does not crash", False, repr(exc)[:90])
    if ok:
        check("the villager inspector does not crash", True,
              f"{len(chart)} fields for {name}")
        check("and it carries the decision provenance it promises",
              "source" in chart and "last_prompt" in chart
              and "last_reason" in chart, "")
    check("an unknown villager inspects to None, not an exception",
          e.inspect("Nobody At All") is None, "")
    World.close_all()
    fresh_data()

    # ---- 2. ODD-JOB ATTENDANCE LAUNDERING ----------------------------
    # A shopkeeper standing in his own DARK shop could take a townsperson's
    # $5 odd job and be posted ON SHIFT, absence streak reset, Crane Bonus
    # streak intact, while his till never moved.
    old, had_old = snapshot_knob("TOWNSFOLK")
    try:
        config.TOWNSFOLK = dict(old, enabled=True, count=6, shop_chance=0.0,
                                move_chance=0.0, speak_chance=0.0,
                                oddjob_chance=0.0, oddjob_pay=[6, 6])
        e = Engine(seed=141)
        w = e.world
        shopkeep = next(a for a in w.agents.values()
                        if a.workplace() and a.workplace() in w.tills)
        shop = shopkeep.workplace()
        shopkeep.asleep = False
        shopkeep.location = shop            # standing in his OWN shop
        till0 = w.tills.get(shop, 0.0)
        e.folk.offers[shop] = {"npc": "Big Pete", "pay": 6.0,
                               "task": "shifting crates",
                               "expires": w.tick_no + 50}
        ok, _ = w.execute(shopkeep, {"action": "work"})
        check("a shopkeeper CAN take an odd job in his own doorway", ok, "")
        check("...but it does not open his shop",
              round(w.tills.get(shop, 0.0), 2) == round(till0, 2),
              f"till still ${w.tills.get(shop, 0.0):.2f}")
        check("...and it does NOT count as showing up for his shift",
              shopkeep.name not in w.worked_today
              and shopkeep.name in w.odd_jobs_today, "")

        w.clock.day = 3
        w._attendance_day_done = 2
        w.attendance_ledger()
        posted = [ev for ev in w.events
                  if "ATTENDANCE LEDGER" in str(ev.get("text", ""))]
        text = str(posted[-1]["text"]) if posted else ""
        check("the evening ledger reports it honestly, by name",
              "EARNED ELSEWHERE" in text
              and shopkeep.name.split()[0] in text, text[:70])
        check("and his absence streak counts the day his door stayed shut",
              w.absence_streaks.get(shopkeep.name, 0) >= 1,
              f"streak {w.absence_streaks.get(shopkeep.name, 0)}")
    finally:
        restore_knob("TOWNSFOLK", old, had_old)
    World.close_all()
    fresh_data()

    # ---- 3. THE INSOLVENCY GATE'S UNDERLYING MECHANISM ---------------
    # v2.9.2 fixed the reported repro; a stress test found the mechanism
    # still live. ANY momentary deposit into the town fund reopened public
    # hiring on a catastrophically insolvent payroll, and the shift then
    # locked in for sixteen unchecked ticks.
    e = Engine(seed=203)
    w = e.world
    fund = config.TOWN_FUND
    public = next((a for a in w.agents.values()
                   if a.workplace() and w._wage_till_key(a) == fund), None)
    if public is None:
        check("a public worker exists to test the payroll gate", False, "")
    else:
        w.debts.append({"debtor": fund, "creditor": public.name,
                        "amount": config.WAGE_DEBT_CAP * 10, "status": "open",
                        "kind": "wages", "day": w.clock.day, "due_day": None,
                        "seq": 9001})
        w.tills[fund] = 0.0
        check("a catastrophically indebted payroll reads as insolvent",
              w.insolvent(fund), f"owes ${w.wage_debt_of(fund):.0f}")

        # the exact stress-test scenario: a dollar lands mid-tick
        w.tills[fund] = config.WAGE_PER_SHIFT_TICK + 1
        check("A DOLLAR PASSING THROUGH IS NOT SOLVENCY",
              w.insolvent(fund),
              f"${w.tills[fund]:.0f} in hand against "
              f"${w.wage_debt_of(fund):.0f} owed")
        public.asleep = False
        public.location = public.workplace()
        ok, note = w.execute(public, {"action": "work"})
        check("...so the door does not reopen on it",
              not ok or (public.activity or {}).get("type") != "work",
              (note or "")[:60])

        # covering what you owe IS solvency
        w.tills[fund] = config.WAGE_DEBT_CAP * 10 + 5
        check("but cash that genuinely covers the arrears is solvency",
              not w.insolvent(fund), "")

        # and a shift already running gets re-checked every tick
        w.tills[fund] = 0.0
        public.activity = {"type": "work", "until_tick": w.tick_no + 16,
                           "note": ""}
        ended = w.shift_ended_by_insolvency(public)
        check("a running shift is re-checked, not locked in for 16 ticks",
              ended and (public.activity or {}).get("type") == "idle",
              "sent home mid-shift")

        # a SHOP with a dry till must still be able to open and hope
        shopkeep = next((a for a in w.agents.values()
                         if a.workplace() and w._wage_till_key(a) != fund),
                        None)
        if shopkeep:
            shopkeep.activity = {"type": "work",
                                 "until_tick": w.tick_no + 16, "note": ""}
            w.tills[shopkeep.workplace()] = 0.0
            check("THE TIBBS DOOR SURVIVES — a dry shop stays open",
                  not w.shift_ended_by_insolvency(shopkeep), "")
    World.close_all()
    fresh_data()

    # ---- 4. A CONFIG KNOB THAT SILENTLY REFUSED TO APPLY -------------
    # Townsfolk.ensure() bailed on self._seeded, which is restored from
    # save — so raising the count on an existing town did nothing at all,
    # with no error and no log line.
    old, had_old = snapshot_knob("TOWNSFOLK")
    try:
        config.TOWNSFOLK = dict(old, enabled=True, count=3,
                                shop_chance=0.0, move_chance=0.0,
                                speak_chance=0.0, oddjob_chance=0.0)
        e = Engine(seed=57)
        e.folk.ensure()
        first = [p["name"] for p in e.folk.people]
        check("the street seeds to the configured count", len(first) == 3,
              str(len(first)))
        e.folk._seeded = True            # exactly what a restore sets
        config.TOWNSFOLK = dict(config.TOWNSFOLK, count=6)
        e.folk.ensure()
        now = [p["name"] for p in e.folk.people]
        check("RAISING the count on a restored town actually adds people",
              len(now) == 6, f"{len(first)} -> {len(now)}")
        check("and nobody is duplicated when it tops up",
              len(set(now)) == len(now) and set(first) <= set(now), "")
        config.TOWNSFOLK = dict(config.TOWNSFOLK, count=2)
        e.folk.ensure()
        check("lowering it does not delete anyone already walking around",
              len(e.folk.people) == 6, "")
    finally:
        restore_knob("TOWNSFOLK", old, had_old)
    World.close_all()
    fresh_data()

_townsfolk()
_no_charity_from_the_gods()
_town_report()
_experiment_ledger()
_an_old_town_upgrades()
_review_fixes_v331()
fails2 = [r for r in results if not r[1]]
print(f"\nTOTAL {len(results) - len(fails2)}/{len(results)} passed")
sys.exit(1 if fails2 else 0)
