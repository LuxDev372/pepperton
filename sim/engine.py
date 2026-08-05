"""The tick loop — where Pepperton actually happens."""

import random
import threading
import time

import config
from sim import brains
from sim.agents import generate_cast
from sim.director import Director
from sim.memory import MemoryStore
from sim.radio import Radio
from sim.world import World

# importance heuristics for stored memories
_IMPORTANCE = {
    "speech_to_me": 6, "speech_overheard": 4, "radio": 5,
    "own_action": 3, "own_speech": 4, "movement": 1.5,
    "urgent": 7, "reflection": 8,
}


STATE_PATH = "data/world_state.json"


def _rng_dump(state):
    """random.getstate() -> JSON-safe nested lists."""
    def conv(x):
        return [conv(i) for i in x] if isinstance(x, tuple) else x
    return conv(state)


def _rng_load(data):
    """JSON nested lists -> setstate-compatible nested tuples."""
    def conv(x):
        return tuple(conv(i) for i in x) if isinstance(x, list) else x
    return conv(data)


def _read_version():
    import os
    for p in ("VERSION", os.path.join(os.path.dirname(__file__), "..", "VERSION")):
        try:
            return open(p).read().strip()
        except OSError:
            continue
    return "dev"

VERSION = _read_version()

_AGENT_FIELDS = ["job", "traits", "quirk", "goal", "model", "host", "home",
                 "location", "money", "pantry", "needs", "asleep", "activity",
                 "relationships", "is_stranger", "drink_ticks", "talk_streak",
                 "last_say", "last_text", "soapbox", "last_decision_tick",
                 "pending", "urgent_flag", "possessions"]


def _mock_with_rng(brain):
    """Walk a brain's understudy chain to the MockBrain that owns the seeded
    RNG (possession seats wrap live brains, live brains wrap mocks)."""
    understudy = getattr(brain, "understudy", None)
    if understudy is not None and not hasattr(understudy, "rng"):
        understudy = getattr(understudy, "understudy", None)
    if understudy is not None and hasattr(understudy, "rng"):
        return understudy
    return None


class Engine:
    def __init__(self, seed=None, state=None):
        if state:
            self.seed = state["seed"]
            self.world_id = state.get("world_id", "legacy")
        else:
            self.seed = seed if seed is not None else random.randrange(1 << 30)
            import uuid
            self.world_id = uuid.uuid4().hex[:12]
        self.rng = random.Random(self.seed)
        if state:
            cast = []
            from sim.agents import Agent
            for name, af in state["agents"].items():
                a = Agent(name=name, job=af["job"], traits=af["traits"],
                          quirk=af["quirk"], goal=af["goal"], model=af["model"],
                          host=af["host"], home=af["home"])
                for f in _AGENT_FIELDS:
                    if f in af:
                        setattr(a, f, af[f])
                a.recent_own_says = [set(x) for x in af.get("recent_own_says", [])]
                cast.append(a)
        else:
            cast = generate_cast(self.seed)
        self.world = World(cast, world_id=self.world_id)
        self.world.engine = self   # backref: the ledger writes memories
        self.memory = MemoryStore(world_id=self.world_id)
        self.brains = {a.name: brains.build_brain(a, self.seed) for a in cast}
        self.radio = Radio(self.seed)
        self.director = Director(self, self.seed)
        self.paused = False
        self.running = False
        self.lock = threading.RLock()   # guards world/brains vs API threads
        self._cmdq = []                 # API writes, applied between ticks
        self._cmdlock = threading.Lock()   # guards _cmdq only — never held
                                           # while a model is thinking
        self._thread = None
        self._reflected_day = 0
        self._cached_snapshot = None    # rebuilt at the end of every tick
        self._inspect_cache = {}        # last full chart per villager
        if state:
            world = self.world
            restored_tick = state["tick_no"]
            # reconcile: erase any "memories from the future" written after
            # the checkpoint we are restoring to (crash-recovery integrity)
            dropped = self.memory.delete_after(restored_tick)
            if dropped:
                print(f"[ENGINE] reconciled {dropped} future memories "
                      f"(post-tick-{restored_tick} timeline erased)", flush=True)
            # backfill the event feed from the transcript (this world's
            # lines only, and none from the abandoned future)
            try:
                import json as _json
                with open(config.TRANSCRIPT_JSONL, encoding="utf-8") as f:
                    lines = f.readlines()
                evs = []
                for line in lines:
                    try:
                        e = _json.loads(line)
                    except ValueError:
                        continue
                    if e.get("wid", "legacy") == self.world_id and \
                            e.get("tick", 0) <= restored_tick:
                        evs.append(e)
                evs = evs[-150:]
                if evs:
                    world.events = evs
                    world._event_seq = max(e.get("seq", 0) for e in evs)
            except OSError:
                pass
            world.tick_no = state["tick_no"]
            world.clock.day = state["day"]
            world.clock.minutes = state["minutes"]
            world.locations = state["locations"]
            world.projects = state["projects"]
            # merge in locations added by newer versions (e.g. the bank):
            # an old town wakes up and there's a new building on the square
            new_locs = [k for k in config.LOCATIONS if k not in world.locations]
            for k in new_locs:
                world.locations[k] = dict(config.LOCATIONS[k])
            # the ledger: restore if saved; a pre-2.0 town gets fresh seeds
            first_economy_boot = getattr(config, "ECONOMY", False) and \
                "tills" not in state
            world.recent_says = [tuple(x) for x in state.get("recent_says", [])]
            world.tills.update(state.get("tills", {}))
            world.debts = state.get("debts", [])
            world.promises = state.get("promises", [])
            world.ledger.seq = state.get("ledger_seq", 0)
            world.ledger.rent_day_done = state.get("rent_day_done", 0)
            world.ledger.swept_day = state.get("ledger_day_done", 0)
            world._permit_day_done = state.get("permit_day_done", 0)
            world.worked_today = set(state.get("worked_today", []))
            world.earned_today = state.get("earned_today", {})
            world.absence_streaks = state.get("absence_streaks", {})
            world._bell_day_done = state.get("bell_day_done", 0)
            world._attendance_day_done = state.get("attendance_day_done", 0)
            # the Fall Fair Act reaches back: incomplete projects from
            # before v2.4 get permits stamped with a FRESH window from
            # today — the fall fair finally hears a clock ticking
            if getattr(config, "PERMITS_ENABLED", True):
                for proj in world.projects:
                    if not proj["complete"] and \
                            proj.get("permit_due") is None:
                        proj["permit_due"] = world.clock.day + \
                            world.permit_window(proj["work"])
            self._reflected_day = state.get("reflected_day", 0)
            self.director.strangers_added = state.get("strangers_added", 0)
            self.radio.dead_day = state.get("radio_dead_day")
            # deterministic resume: restore every RNG mid-stream
            rs = state.get("rng", {})
            try:
                if rs.get("engine"):
                    self.rng.setstate(_rng_load(rs["engine"]))
                if rs.get("director"):
                    self.director.rng.setstate(_rng_load(rs["director"]))
                if rs.get("radio") and getattr(self.radio, "rng", None):
                    self.radio.rng.setstate(_rng_load(rs["radio"]))
                for name, st_ in (rs.get("brains") or {}).items():
                    mock = _mock_with_rng(self.brains.get(name))
                    if mock is not None:
                        mock.rng.setstate(_rng_load(st_))
            except (TypeError, ValueError) as e:
                print(f"[ENGINE] RNG restore skipped: {e}", flush=True)
            # scrub ghost relationships (pre-1.17 None-key bug)
            for a in self.world.agents.values():
                a.relationships.pop(None, None)
                a.relationships.pop("null", None)
            world.emit("world", None,
                       f"{config.TOWN_NAME} continues. (Day {world.clock.day} — the "
                       f"town survived an upgrade; nobody noticed a thing.)",
                       "the plaza", deliver=False)
            if first_economy_boot:
                bank = world.bank_name()
                if bank:
                    world.emit("world", None,
                               f"Overnight, scaffolding came down nobody remembers "
                           f"going up: {bank} has opened its doors. Money is "
                           f"different now — businesses pay from their tills, "
                           f"rent falls due every {config.RENT_EVERY_DAYS} days, "
                           f"and the bank makes loans against your public record.",
                           bank)
                    for a in world.agents.values():
                        a.pending.append({
                            "text": (f"There is a BANK in town now — {bank}, "
                                     f"open for business. Loans against your "
                                     f"notice-board record (borrow, at the "
                                     f"bank), debts in a ledger, rent every "
                                     f"{config.RENT_EVERY_DAYS} days. The "
                                     f"economy is real now."),
                            "interrupt": False,
                            "sim_time": world.clock.hhmm,
                        })
        else:
            for a in cast:
                self._remember(a, "genesis",
                               f"I am {a.persona_line()} I live at {a.home}.",
                               _IMPORTANCE["reflection"])
            self.world.emit("world", None,
                            f"{config.TOWN_NAME} wakes up. (seed {self.seed}, "
                            f"{'mock' if config.MOCK_MODE else 'live'} minds)",
                            "the plaza", deliver=False)

    # ------------------------------------------------------------- persist
    def save_state(self):
        import json as _json
        import os as _os
        agents = {}
        for a in self.world.agents.values():
            af = {f: getattr(a, f) for f in _AGENT_FIELDS}
            af["recent_own_says"] = [sorted(x) for x in a.recent_own_says]
            agents[a.name] = af
        brains_rng = {}
        for name, brain in self.brains.items():
            mock = _mock_with_rng(brain)
            if mock is not None:
                brains_rng[name] = _rng_dump(mock.rng.getstate())
        state = {
            "seed": self.seed,
            "world_id": self.world_id,
            "rng": {
                "engine": _rng_dump(self.rng.getstate()),
                "director": _rng_dump(self.director.rng.getstate()),
                "radio": (_rng_dump(self.radio.rng.getstate())
                          if getattr(self.radio, "rng", None) else None),
                "brains": brains_rng,
            },
            "tick_no": self.world.tick_no,
            "day": self.world.clock.day,
            "minutes": self.world.clock.minutes,
            "locations": self.world.locations,
            "projects": self.world.projects,
            "agents": agents,
            "reflected_day": self._reflected_day,
            "strangers_added": self.director.strangers_added,
            "radio_dead_day": self.radio.dead_day,
            "recent_says": [list(t) for t in self.world.recent_says],
            "tills": self.world.tills,
            "debts": self.world.debts,
            "promises": self.world.promises,
            "ledger_seq": self.world.ledger.seq,
            "rent_day_done": self.world.ledger.rent_day_done,
            "ledger_day_done": self.world.ledger.swept_day,
            "permit_day_done": self.world._permit_day_done,
            "worked_today": sorted(self.world.worked_today),
            "earned_today": self.world.earned_today,
            "absence_streaks": self.world.absence_streaks,
            "bell_day_done": self.world._bell_day_done,
            "attendance_day_done": self.world._attendance_day_done,
        }
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump(state, f)
        _os.replace(tmp, STATE_PATH)

    @staticmethod
    def load_state():
        import json as _json
        import os as _os
        if not _os.path.exists(STATE_PATH):
            return None
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return _json.load(f)
        except (ValueError, OSError):
            return None

    # ------------------------------------------------------------ memory
    def _remember(self, agent, kind, text, importance):
        self.memory.add(agent.name, self.world.tick_no, self.world.clock.day,
                        self.world.clock.hhmm, kind, text, importance)

    def _absorb_perceptions(self, agent, perceptions):
        for p in perceptions:
            if p["text"].startswith("The radio:"):
                imp = _IMPORTANCE["radio"]
                kind = "radio"
            elif p["text"].startswith("Your phone buzzes — news alert"):
                imp = _IMPORTANCE["radio"]
                kind = "news"
            elif p["text"].startswith("Your phone buzzes"):
                imp = _IMPORTANCE["speech_to_me"]
                kind = "text"
            elif "said to you" in p["text"]:
                imp = _IMPORTANCE["speech_to_me"]
                kind = "speech"
            elif "said" in p["text"] or "overheard" in p["text"]:
                imp = _IMPORTANCE["speech_overheard"]
                kind = "speech"
            else:
                imp = _IMPORTANCE["movement"]
                kind = "observation"
            self._remember(agent, kind, p["text"], imp)

    # ---------------------------------------------------------- decisions
    def _needs_decision(self, agent):
        if agent.asleep:
            return False
        if any(p.get("interrupt") for p in agent.pending):
            return True
        act = agent.activity
        if act is None:
            return True
        until = act.get("until_tick")
        if until is not None and self.world.tick_no >= until:
            return True
        if agent.urgent_flag:
            return True
        if self.world.tick_no - agent.last_decision_tick >= config.MAX_TICKS_BETWEEN_DECISIONS:
            return True
        return False

    def _apply_needs(self, agent):
        was_urgent = set(agent.urgent_needs())
        if agent.asleep:
            recovery = config.REST_RECOVERY
            if "a proper mattress" in getattr(agent, "possessions", []) and \
                    agent.location == agent.home:
                recovery += 1.5   # creature comforts: the mattress pays off
            agent.needs["energy"] = min(100, agent.needs["energy"] + recovery)
            agent.needs["fullness"] = max(0, agent.needs["fullness"] - config.NEEDS["fullness"]["decay"] * 0.4)
        else:
            for k, v in config.NEEDS.items():
                agent.needs[k] = max(0, agent.needs[k] - v["decay"])
            if (agent.activity or {}).get("type") == "nap":
                agent.needs["energy"] = min(100, agent.needs["energy"] + config.NAP_RECOVERY)
            if (agent.activity or {}).get("type") == "work":
                self.world.pay_wage(agent, config.WAGE_PER_SHIFT_TICK)
            if (agent.activity or {}).get("type") == "build":
                proj = self.world.find_project(agent.activity.get("project"))
                if proj is None:
                    agent.activity = None
                else:
                    proj["done"] += 1
                    proj["contributors"][agent.name] = \
                        proj["contributors"].get(agent.name, 0) + 1
                    if proj["done"] >= proj["work"]:
                        proj["complete"] = True
                        names = ", ".join(
                            f"{n.split()[0]} ({c})" for n, c in
                            sorted(proj["contributors"].items(),
                                   key=lambda kv: -kv[1]))
                        firsts = " and ".join(
                            n.split()[0] for n in proj["contributors"])
                        self.world.emit(
                            "world", None,
                            f"IT'S DONE — {proj['name']} is finished! "
                            f"Built by: {names}. The town has something "
                            f"it didn't have yesterday.",
                            proj["site"])
                        # the world is permanently changed: the site's own
                        # description now carries the built thing + credit
                        loc = self.world.locations.get(proj["site"])
                        if proj.get("inn"):
                            # shelter infrastructure: beds for ANY homeless
                            # villager, nightly rate to the town fund
                            iname = proj["name"]
                            if iname in self.world.locations:
                                iname = f"{iname} (new)"
                            cost = getattr(config, "INN_ROOM_COST", 5)
                            self.world.locations[iname] = {
                                "desc": (f"a hand-built inn — a warm bed for "
                                         f"anyone without a roof, ${cost}/night "
                                         f"to the town fund (built by {firsts})"),
                                "inn": True,
                            }
                            self.world.emit(
                                "world", None,
                                f"{iname} is OPEN — beds for anyone without a "
                                f"roof, ${cost} a night, proceeds to the town "
                                f"fund. No one has to sleep in the park again "
                                f"(unless they're broke).",
                                proj["site"])
                            for other in self.world.agents.values():
                                other.pending.append({
                                    "text": (f"{iname} is open: any villager "
                                             f"without a home can sleep there "
                                             f"for ${cost}/night (rest action, "
                                             f"at {iname}). Proceeds go to the "
                                             f"town fund."),
                                    "interrupt": other.home is None,
                                    "sim_time": self.world.clock.hhmm,
                                })
                        elif proj.get("housing"):
                            # a housing project becomes a REAL new home
                            hname = proj["name"]
                            if hname in self.world.locations:
                                hname = f"{hname} (new)"
                            homeless = [x for x in self.world.agents.values()
                                        if x.home is None]
                            if homeless:
                                tenant = homeless[0]
                                self.world.locations[hname] = {
                                    "desc": f"a small hand-built home ({firsts} built it)",
                                    "home_of": tenant.name,
                                }
                                tenant.home = hname
                                tenant.is_stranger = False
                                self.world.emit(
                                    "world", None,
                                    f"{tenant.name} has a HOME. {hname} is theirs "
                                    f"— no more park benches.",
                                    proj["site"])
                                self._remember(
                                    tenant, "event",
                                    f"The town built me a home — {hname}. "
                                    f"{firsts} swung the hammers. I won't forget it.",
                                    _IMPORTANCE["reflection"])
                            else:
                                self.world.locations[hname] = {
                                    "desc": "a small hand-built home, standing empty",
                                    "home_of": None, "vacant": True,
                                }
                                self.world.emit(
                                    "world", None,
                                    f"{hname} is finished and stands empty — "
                                    f"Pepperton has a vacancy.",
                                    proj["site"])
                        elif loc is not None and proj.get("adds"):
                            loc["desc"] = (f"{loc['desc']}; {proj['adds']} "
                                           f"(built by {firsts})")
                        # EVERYONE learns it exists — phones buzz, memories form
                        for other in self.world.agents.values():
                            if other.name in proj["contributors"]:
                                c2 = proj["contributors"][other.name]
                                self._remember(
                                    other, "event",
                                    f"We finished {proj['name']}. I put in {c2} "
                                    f"shifts of real work on it. I BUILT something.",
                                    _IMPORTANCE["reflection"])
                            else:
                                self._remember(
                                    other, "event",
                                    f"{proj['name'].capitalize()} is finished at "
                                    f"{proj['site']} — {firsts} built it.",
                                    6)
                                other.pending.append({
                                    "text": (f"Word spreads fast: {proj['name']} is "
                                             f"FINISHED at {proj['site']} — built by "
                                             f"{firsts}."),
                                    "interrupt": True,
                                    "sim_time": self.world.clock.hhmm,
                                })
                        agent.activity = None
        now_urgent = set(agent.urgent_needs())
        if now_urgent - was_urgent:
            agent.urgent_flag = True
        # collapse: physics doesn't negotiate
        if agent.needs["energy"] <= 2 and not agent.asleep:
            agent.asleep = True
            agent.activity = {"type": "rest", "until_tick": None, "note": "collapsed"}
            self.world.emit("action", agent.name,
                            "swayed on their feet and fell asleep right there",
                            agent.location)
            self._remember(agent, "event",
                           f"I was so exhausted I passed out at {agent.location}. Embarrassing.",
                           _IMPORTANCE["urgent"])

    def _maybe_wake(self, agent):
        if not agent.asleep:
            return
        c = self.world.clock
        morning = 6 * 60 + 30 <= c.minutes < 9 * 60
        if (morning and agent.needs["energy"] > 60) or \
                (agent.needs["energy"] >= 95 and not c.is_night):
            agent.asleep = False
            agent.activity = None
            self.world.emit("action", agent.name, "woke up", agent.location, deliver=False)

    # -------------------------------------------------------------- step
    def step(self):
        self.world.tick_no += 1
        self.world.clock.tick()
        self.radio.maybe_broadcast(self.world)
        self.director.step()
        if getattr(config, "ECONOMY", False):
            self.world.settle_business_debts()
            if self.world.clock.at("08:00"):
                self.world.morning_ledger()
        if self.world.clock.at("08:00"):
            self.world.permit_sweep()
        if getattr(config, "ATTENDANCE_ENABLED", True):
            if self.world.clock.at(getattr(config, "WORKDAY_BELL", "08:00")):
                self.world.morning_bell()
            if self.world.clock.at(getattr(config, "ATTENDANCE_TIME", "18:00")):
                self.world.attendance_ledger()

        order = list(self.world.agents.values())
        self.rng.shuffle(order)
        for agent in order:
            act = agent.activity
            if act and act.get("until_tick") is not None and \
                    self.world.tick_no >= act["until_tick"]:
                agent.activity = None   # shifts END: no wages/labor past the bell
            self._apply_needs(agent)
            self._maybe_wake(agent)
            if not self._needs_decision(agent):
                continue
            perceptions, agent.pending = agent.pending, []
            agent.urgent_flag = False
            self._absorb_perceptions(agent, perceptions)
            query = " ".join(p["text"] for p in perceptions[-4:]) or \
                f"{agent.location} {agent.goal}"
            memories = self.memory.retrieve(agent.name, query, self.world.tick_no)
            brain = self.brains[agent.name]
            action, raw, reason = brain.decide(agent, self.world, perceptions, memories)
            agent.last_reason = reason
            agent.last_decision_tick = self.world.tick_no
            ok, summary = self.world.execute(agent, action)
            if (action or {}).get("action") in ("say", "text"):
                agent.talk_streak += 1
            else:
                agent.talk_streak = 0
            kind = "speech" if (action or {}).get("action") == "say" else "action"
            imp = _IMPORTANCE["own_speech"] if kind == "speech" else _IMPORTANCE["own_action"]
            self._remember(agent, kind, f"I {summary}", imp if ok else imp + 1)

        # nightly reflection: diary + self-judged warmth + goal arcs
        if self.world.clock.at(config.REFLECTION_TIME) and \
                self._reflected_day < self.world.clock.day:
            self._reflected_day = self.world.clock.day
            self._nightly_reflections()

        # refresh the observatory's lock-free snapshot (see snapshot())
        self._cached_snapshot = self._snapshot_locked(0)

    def _nightly_reflections(self):
        for agent in self.world.agents.values():
            agent.pantry = 3    # overnight restock
            day_mem = self.memory.day_memories(agent.name, self.world.clock.day)
            r = self.brains[agent.name].reflect(agent, self.world.clock.day, day_mem)
            if isinstance(r, str):   # legacy brain
                r = {"reflection": r, "warmer": None, "colder": None,
                     "goal_resolved": False}
            self._remember(agent, "reflection", r["reflection"],
                           _IMPORTANCE["reflection"])
            self.world.emit("reflect", agent.name, r["reflection"],
                            agent.location, deliver=False)
            # valence: the villager's own nightly judgment moves the needle
            for key, delta in (("warmer", 3), ("colder", -3)):
                who = self.world._resolve_agent(r.get(key))
                if who and who != agent.name:
                    agent.relationships[who] = \
                        agent.relationships.get(who, 0) + delta
            # goal arcs: closure, then a fresh preoccupation
            if r.get("goal_resolved"):
                old = agent.goal
                pool = [g for g in config.GOALS if g != old]
                agent.goal = self.rng.choice(pool)
                self._remember(agent, "event",
                               f"Settled at last: {old}. Done with it. A new "
                               f"preoccupation takes root: {agent.goal}",
                               _IMPORTANCE["reflection"])
                self.world.emit("reflect", agent.name,
                                f"(a chapter closes: no longer '{old}' — "
                                f"now: '{agent.goal}')",
                                agent.location, deliver=False)

    # --------------------------------------------------------------- run
    def run_headless(self, ticks):
        for _ in range(ticks):
            self.step()

    # ----------------------------------------------------------- commands
    def submit(self, fn, label=""):
        """Queue a world mutation to run at the next tick boundary.

        API threads must not take engine.lock themselves: in live mode a
        tick holds it while five models think, so a Director click could
        block an HTTP request for the full model timeout (measured at 107s
        on a busy GPU). Writes are queued and applied between ticks — the
        same contract possession already uses for queued actions."""
        with self._cmdlock:
            self._cmdq.append((fn, label))

    def _drain_commands(self):
        import traceback
        with self._cmdlock:
            pending, self._cmdq = self._cmdq, []
        if not pending:
            return
        with self.lock:
            for fn, label in pending:
                try:
                    fn()
                except Exception:
                    print(f"[ENGINE] queued command {label!r} failed "
                          f"(town continues):", flush=True)
                    traceback.print_exc()

    def start_background(self):
        self.running = True

        def loop():
            import traceback
            pace = config.PACING[config.PACING_MODE]["real_seconds_per_tick"]
            while self.running:
                if not self.paused:
                    try:
                        with self.lock:
                            self.step()
                    except Exception:
                        # one bad tick must NEVER kill a town — log and live on
                        print(f"[ENGINE] tick {self.world.tick_no + 1} crashed "
                              f"(town continues):", flush=True)
                        traceback.print_exc()
                    if self.world.tick_no % 4 == 0:
                        try:
                            self.save_state()
                        except Exception:
                            pass
                # between ticks the lock is free — apply anything the
                # observatory asked for while the town was thinking.
                self._drain_commands()
                time.sleep(pace)

        self._thread = threading.Thread(target=loop, daemon=True, name="pepperton-engine")
        self._thread.start()

    def stop(self):
        self.running = False

    # ------------------------------------------------------------- state
    def snapshot(self, since_seq=0):
        """Read path for the map and websocket. Serves the snapshot cached
        at the end of the last completed tick, WITHOUT taking the engine
        lock — in live mode a tick can hold that lock for many seconds
        while models think, and the observatory should watch the town, not
        the mutex. The cache is at most one tick stale, which is exactly
        as fresh as the sim itself. (Live-lock tradeoff flagged by an
        external review; this is the on-purpose decision.)"""
        cached = self._cached_snapshot
        if cached is None:
            with self.lock:
                return self._snapshot_locked(since_seq)
        out = dict(cached)
        out["paused"] = self.paused   # the one field that changes between ticks
        if since_seq:
            out["events"] = [e for e in cached["events"] if e["seq"] > since_seq]
        return out

    def _snapshot_locked(self, since_seq=0):
        world = self.world
        return {
            "town": config.TOWN_NAME,
            "version": VERSION,
            "sim_time": world.clock.label,
            "day": world.clock.day,
            "hhmm": world.clock.hhmm,
            "tick": world.tick_no,
            "is_night": world.clock.is_night,
            "paused": self.paused,
            "mock": config.MOCK_MODE,
            "seed": self.seed,
            "locations": [
                {"name": k, "desc": v.get("desc", ""),
                 "home_of": v.get("home_of"), "radio": bool(v.get("radio")),
                 "vacant": bool(v.get("vacant")),
                 "sells_food": bool(v.get("sells_food"))}
                for k, v in world.locations.items()
            ],
            "agents": [a.to_public() for a in world.agents.values()],
            "projects": [
                {"name": p["name"], "site": p["site"], "done": p["done"],
                 "work": p["work"], "complete": p["complete"],
                 "icon": p.get("icon"),
                 "contributors": p["contributors"]}
                for p in world.projects
            ],
            "events": [e for e in world.events if e["seq"] > since_seq][-120:],
            "economy": ({
                "tills": {k: round(v, 2) for k, v in world.tills.items()},
                "debts": [d for d in world.debts if d["status"] == "open"][-20:],
                "promises": [p for p in world.promises if p["status"] == "open"][-10:],
            } if getattr(config, "ECONOMY", False) else None),
        }

    def inspect(self, name):
        """The click-a-villager chart. In live mode the engine lock can be
        held for many seconds while models think — rather than freeze the
        inspector panel, wait briefly, then serve the last full chart we
        built (marked stale). The fresh one arrives when the tick clears."""
        if not self.lock.acquire(timeout=2.0):
            # Whatever else is stale, the seat is not: possession lives on
            # the brain wrapper, is set without the engine lock, and a
            # stale `possessed` would make the observatory's seat control
            # flip back under the operator's hands.
            b = self.brains.get(name)
            seat = {"possessed": bool(b and b.possessed)}
            cached = self._inspect_cache.get(name)
            if cached is not None:
                return {**cached, **seat, "stale": True}
            snap = self._cached_snapshot
            if snap:
                for a in snap["agents"]:
                    if a["name"] == name:
                        return {**a, **seat, "stale": True,
                                "last_reason": "(mid-thought — full chart "
                                               "when the tick clears)"}
            return None
        try:
            data = self._inspect_locked(name)
            if data is not None:
                self._inspect_cache[name] = data
            return data
        finally:
            self.lock.release()

    def _inspect_locked(self, name):
        a = self.world.agents.get(name)
        if not a:
            return None
        b = self.brains[name]
        return {
            **a.to_public(),
            "traits": a.traits, "quirk": a.quirk, "goal": a.goal,
            "home": a.home, "workplace": a.workplace(),
            "relationships": a.relationships,
            "possessed": b.possessed,
            "last_reason": a.last_reason,
            "last_action": a.last_action,
            "last_prompt": a.last_prompt,
            "last_reply": a.last_reply,
            "memory_count": self.memory.count(name),
            "recent_memories": self.memory.recent(name, 20),
            "debts_owed": self.world.open_debts(debtor=name),
            "debts_owed_to_me": self.world.open_debts(creditor=name),
            "promises": [p for p in self.world.promises
                         if p["status"] == "open" and
                         name in (p["maker"], p["to"])],
        }
