"""The road between towns — one verb and a mailbox.

Pepperton, Pompeii and Claudeville are separate processes with separate
databases that have never exchanged a byte. They still don't. The road is a
shared folder they leave notes in: the departing town writes a traveller
file and deletes the villager; the receiving town's coach reads the folder
on arrival and steps a REAL person off the bus instead of inventing a
stranger. Neither process ever knows the other exists.

WHAT TRAVELS, AND WHAT DOES NOT (claude/WORLD2.md §3L)

    cash in pocket   yes — audited both sides through outside_flow
    memories         yes — the whole point; she arrives with a past
    property         NO  — you become an absentee owner, Rosie's condition
    your job         NO  — it becomes an unheld position and Situations
                           Vacant starts advertising it
    credit history   NO  — you arrive an unknown at a teller window
    debts            NO  — they stay with the town that holds them, and the
                           house you left can still be foreclosed while
                           you are away

The clocks do not sync and that is deliberate. A traveller arrives carrying
memories stamped with another town's calendar — a person with a detailed,
dated, completely unverifiable past. Which is what a stranger already is,
except this one's history is real and checkable by us and by nobody in the
room with her.

NOBODY IS PUT ON THE COACH. The verb appears in the action list only while
a coach is standing in the plaza, with its destination painted on the side.
No hint, no pending message, no encouragement. If nobody ever boards, that
is the answer.
"""

import json
import os
import time
import uuid

import config


def cfg():
    """Every knob getattr-defaulted; ships DARK (the v2.4.1 law)."""
    out = {
        "enabled": False,        # armed deliberately, never by surprise
        "road": "data/road",     # the shared folder both towns can see
        "destination": None,     # what the board on the coach reads
        "accepts": True,         # will this town take arrivals off its coach
        "memories": 200,         # how much of a life fits in one suitcase
    }
    out.update(getattr(config, "TRAVEL", {}))
    return out


def road_dir():
    path = os.path.expanduser(cfg()["road"])
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass
    return path


# ------------------------------------------------------------- departure
def write_traveller(world, agent, destination):
    """Pack a person into a file. Returns the path, or None if it failed.

    Everything here is what a person carries: who they are, what they were
    thinking about, who they knew, what is in their pocket, and what they
    remember. Nothing here is what a person owns."""
    c = cfg()
    engine = getattr(world, "engine", None)
    memories = []
    if engine is not None and getattr(engine, "memory", None) is not None:
        try:
            memories = engine.memory.recent(agent.name, c["memories"])
        except Exception:
            memories = []
    note = {
        "schema": 1,
        "id": uuid.uuid4().hex[:12],
        "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "from_town": getattr(config, "TOWN_NAME", "?"),
        "from_world": world.world_id,
        "from_day": world.clock.day,
        "to_town": destination,
        "name": agent.name,
        "traits": list(agent.traits or []),
        "quirk": agent.quirk,
        "goal": agent.goal,
        "model": agent.model,
        "host": agent.host,
        "money": round(float(agent.money), 2),
        "needs": dict(agent.needs),
        "relationships": dict(agent.relationships or {}),
        "memories": memories,
    }
    try:
        path = os.path.join(road_dir(), f"traveller-{note['id']}.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(note, f)
        os.replace(tmp, path)
        return path
    except OSError:
        return None


# --------------------------------------------------------------- arrival
def take_traveller(destination):
    """Claim one waiting traveller bound for this town, or None.

    Claimed by RENAME, which is atomic — two towns polling the same folder
    cannot both take the same person."""
    c = cfg()
    if not c["accepts"]:
        return None
    town = getattr(config, "TOWN_NAME", "?")
    try:
        names = sorted(os.listdir(road_dir()))
    except OSError:
        return None
    for name in names:
        if not (name.startswith("traveller-") and name.endswith(".json")):
            continue
        path = os.path.join(road_dir(), name)
        try:
            with open(path, encoding="utf-8") as f:
                note = json.load(f)
        except (OSError, ValueError):
            continue
        if note.get("to_town") not in (town, None):
            continue
        claimed = path + ".arrived"
        try:
            os.replace(path, claimed)     # atomic: first town wins
        except OSError:
            continue
        note["_file"] = claimed
        return note
    return None


def land_traveller(engine, note):
    """Step a real person off the coach.

    They arrive with cash, memories, traits, quirk and goal — and with NO
    job, NO home, NO credit history and NO debts. A stranger with a past,
    which is the most dangerous kind. Returns the new agent, or None.
    """
    from sim.agents import Agent
    from sim import brains
    world = engine.world
    name = note.get("name")
    if not name or name in world.agents:
        return None                      # never overwrite a living villager
    agent = Agent(name=name, job="newcomer", traits=note.get("traits") or [],
                  quirk=note.get("quirk") or "", goal=note.get("goal") or "",
                  model=note.get("model"), host=note.get("host"), home=None)
    agent.location = world._coach_stop()
    agent.money = round(float(note.get("money") or 0.0), 2)
    agent.needs.update(note.get("needs") or {})
    agent.is_stranger = True
    # relationships do NOT survive the road: nobody here knows her, and she
    # knows nobody here. What she remembers about people is memory, not
    # standing.
    world.agents[name] = agent
    engine.brains[name] = brains.build_brain(agent, engine.seed)
    world.outside_flow = round(world.outside_flow + agent.money, 2)

    # her life, rewritten into this town's book under this town's world_id
    kept = 0
    memory = getattr(engine, "memory", None)
    if memory is not None:
        for row in (note.get("memories") or []):
            try:
                memory.add(name, world.tick_no, world.clock.day,
                           world.clock.hhmm, row.get("kind") or "event",
                           row.get("text") or "", row.get("importance") or 5)
                kept += 1
            except Exception:
                pass
        try:
            memory.add(name, world.tick_no, world.clock.day, world.clock.hhmm,
                       "event",
                       f"I got off the coach in {getattr(config, 'TOWN_NAME', 'this town')}. "
                       f"I have {agent.money:.0f} dollars, no house, no job, and "
                       f"nobody here has ever heard of me.", 9)
        except Exception:
            pass

    world.emit("world", None,
               f"The coach door opens and somebody steps down carrying "
               f"nothing but what fits in a coat. Not a tourist — the bus "
               f"leaves without them. They say their name is {name}.",
               agent.location)
    for other in world.agents.values():
        if other.name == name:
            continue
        other.pending.append({
            "text": (f"Somebody got OFF the coach and stayed. {name}. "
                     f"Not a sightseer — they have no house here and no job, "
                     f"and they are standing in {agent.location}."),
            "interrupt": True, "sim_time": world.clock.hhmm,
        })
    print(f"[ROAD] {name} arrived from {note.get('from_town')} "
          f"(day {note.get('from_day')}), {kept} memories in the suitcase",
          flush=True)
    return agent
