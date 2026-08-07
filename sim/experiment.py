"""The experiment ledger — what prevents the television from lying.

The observatory shows you a town. It cannot show you whether the town was
real while you were watching it.

We learned that the expensive way. Ollama's request queue overflowed under
three towns of load, the engine treated a rejected request exactly like a
dead host, and MockBrain quietly took the seat. Mock is written to be
lifelike — it walks to work between 8 and noon, it buys a foreclosed house
when it is flush — so for ten days two towns' lone workers clocked in and
bought up the property and it was narrated as emergent capitalism. It was a
script. Nothing in any record said otherwise, because nothing recorded what
was actually running.

v2.9.4 fixed half of that: every DECISION now carries provenance. This is
the other half. Provenance says who chose; the ledger says what the RUN was.

One record per engine lifetime, in data/experiments.json:

    run id, world id, code version, config hash, prompt-template hash,
    seed, the full cast with model and host, the tick and day it opened
    and closed, the checkpoint hash at both ends, every decision counted
    by source, every Director event, every manual intervention with the
    tick it landed on, and why the run ended.

A run where 12% of decisions came from an understudy is not a finding. It
is a story. The ledger is how you can tell the difference afterward,
instead of reconstructing it from memory and hope.

Read-only with respect to the simulation: it observes and writes its own
file. It consumes no seeded randomness and moves no golden hash.
"""

import hashlib
import json
import os
import time
import uuid

import config

LEDGER_PATH = "data/experiments.json"
KEEP_RUNS = 200


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def _file_hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()[:16]
    except OSError:
        return None


def source_files():
    """The two files that decide what a villager is asked and how the world
    answers. If either changes between runs, the runs are not comparable."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    return {
        "config": _file_hash(os.path.join(root, "config.py"))
                  or _file_hash("config.py"),
        "prompts": _file_hash(os.path.join(here, "prompts.py")),
    }


class ExperimentLedger:
    """One record per engine lifetime. Append-only across runs."""

    def __init__(self, path=None):
        self.path = path or getattr(config, "LEDGER_PATH", LEDGER_PATH)
        self.run = None
        self.enabled = getattr(config, "EXPERIMENT_LEDGER", True)

    # ------------------------------------------------------------- lifecycle
    def open_run(self, engine, restored=False):
        if not self.enabled:
            return
        world = engine.world
        hashes = source_files()
        self.run = {
            "run_id": uuid.uuid4().hex[:12],
            "world_id": engine.world_id,
            "town": getattr(config, "TOWN_NAME", "?"),
            "code_version": getattr(
                __import__("sim.engine", fromlist=["VERSION"]), "VERSION",
                "dev"),
            "config_hash": hashes["config"],
            "prompts_hash": hashes["prompts"],
            "seed": engine.seed,
            "mock_mode": bool(getattr(config, "MOCK_MODE", False)),
            "pacing": getattr(config, "PACING_MODE", "?"),
            "restored": bool(restored),
            "opened_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "opened_tick": world.tick_no,
            "opened_day": world.clock.day,
            "opened_state_hash": self.state_hash(),
            "cast": [{"name": a.name, "job": a.job, "model": a.model,
                      "host": a.host} for a in world.agents.values()],
            "laws_armed": self.laws_armed(),
            "decisions": {},
            "director_events": {},
            "interventions": [],
            "closed_utc": None,
            "ended_reason": "running",
        }
        self.write()

    def close(self, engine, reason="stopped"):
        if not self.enabled or not self.run:
            return
        self.update(engine)
        self.run["closed_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime())
        self.run["ended_reason"] = reason
        self.run["closed_state_hash"] = self.state_hash()
        self.write()

    # ---------------------------------------------------------------- notes
    def note_decision(self, source):
        if not self.enabled or not self.run:
            return
        d = self.run["decisions"]
        d[source] = d.get(source, 0) + 1

    def note_director(self, name, manual, tick, day):
        if not self.enabled or not self.run:
            return
        d = self.run["director_events"]
        d[name] = d.get(name, 0) + 1
        if manual:
            self.note_intervention("director", name, tick, day)

    def note_intervention(self, kind, detail, tick, day):
        """A human reached into the town. Every one of these is recorded,
        because an experiment with an unrecorded intervention in it is not
        an experiment."""
        if not self.enabled or not self.run:
            return
        self.run["interventions"].append({
            "tick": tick, "day": day, "kind": kind, "detail": str(detail)[:160],
            "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })

    # --------------------------------------------------------------- derived
    @staticmethod
    def laws_armed():
        """Which optional statutes were live for this run. Two runs with
        different answers here are two different worlds."""
        def on(name, default=True):
            block = getattr(config, name, None)
            if isinstance(block, dict):
                return bool(block.get("enabled", default))
            return bool(block if block is not None else default)
        return {
            "economy": bool(getattr(config, "ECONOMY", False)),
            "permits": on("PERMITS_ENABLED"),
            "attendance": on("ATTENDANCE_ENABLED"),
            "poor_box": on("POOR_BOX_ENABLED"),
            "hiring": on("HIRING_ENABLED"),
            "foreclosure": on("FORECLOSURE"),
            "bus": on("BUS"),
            "loyalty": on("LOYALTY", False),
            "townsfolk": on("TOWNSFOLK", False),
        }

    def state_hash(self):
        return _file_hash(getattr(config, "STATE_PATH",
                                  "data/world_state.json"))

    def integrity(self):
        """The headline: what fraction of this run was actually thought."""
        if not self.run:
            return None
        d = self.run["decisions"]
        total = sum(d.values())
        live = d.get("model", 0) + d.get("possessed", 0)
        return {
            "decisions": total,
            "live": live,
            "live_pct": round(100.0 * live / total, 1) if total else None,
            "understudy": d.get("understudy", 0) + d.get("dark", 0),
            "unparsed": d.get("unparsed", 0),
            "interventions": len(self.run["interventions"]),
            "clean": total > 0 and (d.get("understudy", 0)
                                    + d.get("dark", 0)) == 0,
        }

    # ----------------------------------------------------------------- io
    def update(self, engine):
        if not self.enabled or not self.run:
            return
        world = engine.world
        self.run["closed_tick"] = world.tick_no
        self.run["closed_day"] = world.clock.day
        self.run["integrity"] = self.integrity()

    def read_all(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data.get("runs", [])
        except (OSError, ValueError):
            return []

    def write(self):
        if not self.enabled or not self.run:
            return
        runs = [r for r in self.read_all()
                if r.get("run_id") != self.run["run_id"]]
        runs.append(self.run)
        runs = runs[-KEEP_RUNS:]
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "runs": runs}, f, indent=1)
            os.replace(tmp, self.path)
        except OSError:
            pass    # a ledger that cannot write must never stop a town
