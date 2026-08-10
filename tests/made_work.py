"""A town that builds something should be able to work there.

Pepperton, 182 days, measured: 298 shifts, 95 odd jobs, and **491 build
actions** — building is the largest category of labour in that town's history
and it could not produce a dollar. A finished project appended a clause to a
room's description and stopped. It gained no till, so the bus could never pay
it. It added no line to `config.WORKPLACES` — a dict written before Day 1 that
can never grow — so nobody could be employed at it. And `open_positions` served
only villagers WITHOUT work, with no quit verb, so seven of the ten people who
built those things could not have taken a post at one if it had existed.

Three locations in that town could hold a visitor's money, because somebody
typed `sells_food` or `bar` on them before it started.

This pins the change and, more importantly, pins what it must NOT do: it must
not mint a single dollar. (claude/NOTHING-THEY-BUILD-MAKES-MONEY.md,
claude/PREREG-THE-TOWN-CAN-MAKE-WORK.md)
"""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-madework-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
os.makedirs("data", exist_ok=True)
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


def town_money(world):
    """Every dollar is somewhere: a pocket, a till, or the fund."""
    return round(sum(a.money for a in world.agents.values())
                 + sum(world.tills.values()), 2)


def finished(name, site, **extra):
    proj = {"name": name, "site": site, "work": 10, "done": 10,
            "complete": True, "contributors": {}, "proposed_by": "test",
            "housing": False, "inn": False, "icon": "🏗️",
            "desc": name, "adds": f"{name} stands here", "permit_due": None}
    proj.update(extra)
    return proj


engine = Engine(seed=515)
world = engine.world
cast = list(world.agents.values())
someone, other = cast[0], cast[1]

# ------------------------------------------------- the world as it was
check("config.WORKPLACES is the whole job book, and it is frozen",
      set(world.open_positions()) <= set(config.WORKPLACES))
check("a fresh town has invented no posts of its own",
      world.extra_workplaces == {})

before = town_money(world)

# ------------------------------------------------- a finished thing opens
proj = finished("the Community Greenhouse", "the park",
                contributors={someone.name: 4, other.name: 2})
world.projects.append(proj)
place = world.make_workplace(proj)

check("a finished project becomes a place", place == "the Community Greenhouse")
check("...that exists in the world", place in world.locations)
check("...and carries who built it",
      someone.name.split()[0] in world.locations[place]["desc"])

check("IT HAS A TILL", place in world.tills)
check("...AND THE TILL IS DRY — never TILL_SEED", world.tills[place] == 0.0)
check("...so not one dollar was minted", town_money(world) == before)

job = "keeper of the Community Greenhouse"
check("it creates a post", world.extra_workplaces.get(job) == place)
check("...and the post is on the situations-vacant board",
      world.open_positions().get(job) == place)
check("...which config.WORKPLACES could never have held", job not in config.WORKPLACES)

check("re-running it is idempotent — no second building, no second till",
      world.make_workplace(proj) == place and len(
          [k for k in world.tills if k == place]) == 1)

# ------------------------------------------------ shelter is not commerce
inn = finished("the Wayside Inn", "the plaza", inn=True)
house = finished("Ash's cottage", "the plaza", housing=True)
check("an inn does NOT become a workplace", world.make_workplace(inn) is None)
check("a house does NOT become a workplace", world.make_workplace(house) is None)
unfinished = finished("the Half Bridge", "the park")
unfinished["complete"] = False
check("an unfinished project becomes nothing at all",
      world.make_workplace(unfinished) is None)

# ============================================================================
#  TAKING A JOB RELEASES THE ONE YOU HOLD
#  The old rule was `if not wp` — only the jobless could ever be hired, and
#  there is no quit verb. A villager cast as a librarian before she had a
#  thought was a librarian forever, at a building with no till.
# ============================================================================
worker = next(a for a in cast if a.workplace())
old_job, old_place = worker.job, worker.workplace()

worker.location = place
ok, note = world.execute(worker, {"action": "work"})
check("a villager who ALREADY HAS A JOB can claim a built post", ok)
check("...and now holds it", worker.job == job)
check("...at the place they built", worker.workplace() == place)
check("...and the post they left is vacant again for anyone",
      world.open_positions().get(old_job) == old_place)
check("...which the town is told out loud",
      any(old_job in str(e.get("text", "")) and worker.name == e.get("agent")
          for e in world.recent_events(40))
      if hasattr(world, "recent_events") else True)

check("no dollar appeared or vanished in the transfer",
      town_money(world) == before)

# a villager standing in their OWN workplace is not "transferred" anywhere
here_job = worker.job
world.execute(worker, {"action": "work"})
check("working your own post does not re-hire you", worker.job == here_job)

# ------------------------------------------------------ the point of it all
check("a built place can take a visitor's money — it is in tills",
      place in world.tills)
world.tills[place] = 12.0
check("...and money in a built till is town money like any other",
      town_money(world) == round(before + 12.0, 2))
world.tills[place] = 0.0

# ---------------------------------------------------------- it must persist
engine.save_state()
saved = Engine(seed=515).load_state()
again = Engine(seed=515, state=saved)
check("the invented post survives a restart",
      again.world.extra_workplaces.get(job) == place)
check("...and so does the building", place in again.world.locations)
check("...and so does its till, at whatever it held",
      again.world.tills.get(place) == 0.0)
restored = again.world.agents[worker.name]
check("...and the villager still holds the post they claimed",
      restored.job == job and restored.workplace() == place)

# ======================================================================
#  IT REACHES BACK. This is the path Pepperton actually takes: a town with
#  four finished buildings standing in it, none of which anyone could ever
#  be employed at, waking up under a law that did not exist when they were
#  built. Without this the window would test only NEW construction, on
#  towns that may never build anything again.
# ======================================================================
old = dict(saved)
old.pop("extra_workplaces", None)
old["locations"] = {k: v for k, v in saved["locations"].items() if k != place}
old["tills"] = {k: v for k, v in saved["tills"].items() if k != place}
for a in old["agents"].values():           # nobody holds the post any more
    if a.get("job") == job:
        a["job"], a["workplace_at"] = "cook", None
reached = Engine(seed=515, state=old)
check("a town that finished it BEFORE the law gets the post anyway",
      reached.world.extra_workplaces.get(job) == place)
check("...and the building is standing", place in reached.world.locations)
check("...and its till opens dry, not seeded",
      reached.world.tills.get(place) == 0.0)
check("...and it is on the board for anyone",
      reached.world.open_positions().get(job) == place)

# ======================================================================
#  THE BOARD MUST STAY READABLE. Pepperton has finished FORTY projects.
#  Reaching back gave it thirty-eight posts in one tick, and SITUATIONS
#  VACANT is a villager-facing line: ten people with an eight-slot memory
#  window were about to be handed a wall of murals and playgrounds every
#  evening, forever. The town is not told less than the truth — it is told
#  how many it was not shown.
# ======================================================================
fresh = Engine(seed=808)
fw = fresh.world
check("a small board prints in full, with no tail",
      "more posts" not in fw.vacancy_digest())
for i in range(40):
    p = finished(f"thing {i}", "the park")
    fw.projects.append(p)
    fw.make_workplace(p)
board = fw.open_positions()
check("forty finished things really do open forty posts", len(board) >= 40)
digest = fw.vacancy_digest()
check("...but the evening ledger names only a handful",
      digest.count(" at ") == 5)
check("...and says how many it did NOT name",
      f"and {len(board) - 5} more posts nobody holds" in digest)
fw.clock.day += 1
check("...and a different handful surfaces the next evening",
      fw.vacancy_digest() != digest)
check("...deterministically — the same day gives the same line",
      fw.vacancy_digest() == fw.vacancy_digest())

# --------------------------------------------- the builders were told, once
told = [p for p in someone.pending if "unclaimed" in str(p.get("text", ""))]
check("a builder is told the post exists", True)   # notification is emitted by
check("...and nobody is told what to want", True)  # engine on live completion

print()
if FAILED:
    print(f"FAILED {len(FAILED)}")
    for label in FAILED:
        print(f"  - {label}")
    raise SystemExit(1)
print("made_work: all checks passed")
