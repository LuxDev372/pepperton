"""The tick loop — where Pepperton actually happens."""

import random
import sqlite3
import threading

import config
from sim import brains
from sim.agents import generate_cast
from sim.bus import BusRoute
from sim.causality import Command
from sim.director import Director
from sim.experiment import ExperimentLedger
from sim.radio import Radio
from sim.store import TownStore
from sim.townsfolk import Townsfolk
from sim.world import World

# importance heuristics for stored memories
_IMPORTANCE = {
    "speech_to_me": 6, "speech_overheard": 4, "radio": 5,
    "own_action": 3, "own_speech": 4, "movement": 1.5,
    "urgent": 7, "reflection": 8,
}


STATE_PATH = getattr(config, "STATE_PATH", "data/world_state.json")


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
                 "pending", "urgent_flag", "possessions", "last_source"]


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
            state = TownStore.migrate_checkpoint(state)
            self.seed = state["seed"]
            self.world_id = state["world_id"]
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
        self.store = TownStore(world_id=self.world_id, state_path=STATE_PATH)
        try:
            if state and state.get("_legacy_identity_pending"):
                # Persist the new identity before claiming ambiguous legacy
                # projections. If migration is interrupted, the marker makes
                # the operation safely retry on the next start.
                self.store.save_checkpoint(state)
                self.store.migrate_legacy_projections()
                state.pop("_legacy_identity_pending", None)
                self.store.save_checkpoint(state)
        except Exception:
            self.store.close()
            raise
        self.world = World(cast, world_id=self.world_id, store=self.store)
        self.world.engine = self   # backref: the ledger writes memories
        self.memory = self.store.memory
        self.brains = {a.name: brains.build_brain(a, self.seed) for a in cast}
        self.radio = Radio(self.seed)
        self.director = Director(self, self.seed)
        self.bus = BusRoute(self.world, random.Random(f"bus:{self.seed}"))
        self.folk = Townsfolk(self.world,
                              random.Random(f"townsfolk:{self.seed}"))
        self.world.folk = self.folk   # the work verb reaches odd jobs
        self.exp = ExperimentLedger()   # what prevents the TV from lying
        self.paused = False
        self.running = False
        self._stop_event = threading.Event()
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
            restored_event_seq = state.get("event_seq")
            dropped_events = self.store.rewind_event_tail(
                restored_tick, restored_event_seq, world_id=self.world_id)
            dropped_memories = self.memory.delete_after(
                restored_tick, state.get("memory_seq"))
            if dropped_events or dropped_memories:
                print(f"[ENGINE] reconciled {dropped_events} future events "
                      f"and {dropped_memories} future memories "
                      f"(post-checkpoint timeline erased)", flush=True)
            # Backfill the rolling UI feed from the surviving durable stream.
            evs = self.store.read_events(
                world_id=self.world_id, max_tick=restored_tick)[-150:]
            if evs:
                world.events = evs
            world._event_seq = state.get(
                "event_seq", max((e.get("seq", 0) for e in evs), default=0))
            world._command_seq = state.get("command_seq", 0)
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
            world.work_streaks = state.get("work_streaks", {})
            world.turned_away_today = set(state.get("turned_away_today", []))
            world.odd_jobs_today = set(state.get("odd_jobs_today", []))
            world.outside_flow = state.get("outside_flow", 0.0)
            world.last_foreclosure_day = state.get("last_foreclosure_day", 0)
            tf = state.get("townsfolk") or {}
            self.folk.people = tf.get("people", [])
            self.folk.offers = tf.get("offers", {})
            self.folk.brought_in = tf.get("brought_in", 0.0)
            self.folk._seeded = tf.get("seeded", False)
            self.folk._shut_said = tf.get("shut_said", {})
            if tf.get("rng"):
                self.folk.rng.setstate(_rng_load(tf["rng"]))
            if hasattr(self.radio, "_used"):
                self.radio._used = set(state.get("radio_used", []))
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
            bus_state = state.get("bus") or {}
            self.bus.day_done = bus_state.get("day_done", 0)
            self.bus.visiting = bus_state.get("visiting", 0)
            self.bus.spent_today = bus_state.get("spent_today", {})
            self.bus.brought_in = bus_state.get("brought_in", 0.0)
            if bus_state.get("rng"):
                self.bus.rng.setstate(_rng_load(bus_state["rng"]))
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
            "event_seq": self.world._event_seq,
            "command_seq": self.world._command_seq,
            "memory_seq": self.memory.high_watermark(),
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
            "work_streaks": self.world.work_streaks,
            "turned_away_today": sorted(self.world.turned_away_today),
            "odd_jobs_today": sorted(getattr(self.world, "odd_jobs_today", ())),
            "outside_flow": self.world.outside_flow,
            "last_foreclosure_day": self.world.last_foreclosure_day,
            "townsfolk": {
                "people": self.folk.people,
                "offers": self.folk.offers,
                "brought_in": self.folk.brought_in,
                "seeded": self.folk._seeded,
                "shut_said": self.folk._shut_said,
                "rng": _rng_dump(self.folk.rng.getstate()),
            },
            "radio_used": sorted(getattr(self.radio, "_used", set())),
            "bell_day_done": self.world._bell_day_done,
            "attendance_day_done": self.world._attendance_day_done,
            "bus": {
                "day_done": self.bus.day_done,
                "visiting": self.bus.visiting,
                "spent_today": self.bus.spent_today,
                "brought_in": self.bus.brought_in,
                "rng": _rng_dump(self.bus.rng.getstate()),
            },
        }
        self.store.save_checkpoint(state)
        # the ledger reads the checkpoint it just wrote, so it goes after
        self.exp.update(self)
        self.exp.write()

    @staticmethod
    def load_state():
        return TownStore.load_checkpoint_file(STATE_PATH)

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
                # re-ask every tick, not just at hiring (v3.3.1)
                if not self.world.shift_ended_by_insolvency(agent):
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
        if self.exp.run is None:
            self.exp.open_run(self, restored=self.world.tick_no > 0)
        self.world.tick_no += 1
        self.world.clock.tick()
        self.radio.maybe_broadcast(self.world)
        self.director.step()
        if getattr(config, "ECONOMY", False):
            # Rent lands BEFORE the settlement sweep, not after (v2.9.2). The
            # other order let a broke town fund hold a few dollars of fresh
            # rent for the rest of the 08:00 tick — long enough for the
            # payroll gate to read "till > 0" and take on workers it was
            # still thousands short of paying, adding new back-wage debt on
            # top of debt already over the cap. It looked like recovery and
            # was compounding insolvency. Money owed gets paid the instant
            # money arrives.
            if self.world.clock.at("08:00"):
                self.world.morning_ledger()
            self.world.settle_business_debts()
        if self.world.clock.at("08:00"):
            self.world.permit_sweep()
        if getattr(config, "ATTENDANCE_ENABLED", True):
            if self.world.clock.at(getattr(config, "WORKDAY_BELL", "08:00")):
                self.world.morning_bell()
            if self.world.clock.at(getattr(config, "ATTENDANCE_TIME", "18:00")):
                self.world.attendance_ledger()
        self.bus.step()   # the coach route (v2.8) — outside money, open doors
        self.folk.step()  # the townsfolk (v3.0) — presence, doors, odd jobs

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
            self._record_provenance(agent, reason)
            # observational only — never touches provenance or the bar
            from sim import prompts as _p
            if _p.bracketed_aside(action):
                self.exp.note_aside()
            # ekinnee's causal envelope (PR #3): every villager action goes
            # through apply_command, so an event can name what caused it.
            # Behaviour-neutral — the golden hash does not move.
            result = self.world.apply_command(Command(
                kind="action", source="villager", actor=agent.name,
                payload=action or {},
            ))
            ok, summary = result.accepted, result.summary
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
                # every /api write in the system funnels through here with
                # a label. An experiment with an unrecorded intervention in
                # it is not an experiment.
                self.exp.note_intervention(
                    "api", label, self.world.tick_no, self.world.clock.day)
                try:
                    fn()
                except Exception:
                    print(f"[ENGINE] queued command {label!r} failed "
                          f"(town continues):", flush=True)
                    traceback.print_exc()

    def start_background(self):
        if self.world._closed:
            raise RuntimeError("cannot restart a closed engine")
        self._stop_event.clear()
        self.running = True

        def loop():
            import traceback
            pace = config.PACING[config.PACING_MODE]["real_seconds_per_tick"]
            while self.running:
                if not self.paused:
                    try:
                        with self.lock:
                            self.step()
                    except (OSError, sqlite3.Error):
                        self.running = False
                        print(f"[ENGINE] fatal persistence failure at tick "
                              f"{self.world.tick_no + 1}; town stopped:",
                              flush=True)
                        traceback.print_exc()
                        self._close_books("died: persistence failure")
                        self.world.close()
                        return
                    except Exception:
                        # one bad tick must NEVER kill a town — log and live on
                        print(f"[ENGINE] tick {self.world.tick_no + 1} crashed "
                              f"(town continues):", flush=True)
                        traceback.print_exc()
                    if self.world.tick_no % 4 == 0:
                        try:
                            self.save_state()
                        except Exception:
                            self.running = False
                            print("[ENGINE] checkpoint failed; town stopped "
                                  "before advancing beyond unsaved state:",
                                  flush=True)
                            traceback.print_exc()
                            self._close_books("died: checkpoint failure")
                            self.world.close()
                            return
                # between ticks the lock is free — apply anything the
                # observatory asked for while the town was thinking.
                self._drain_commands()
                self._stop_event.wait(pace)

        self._thread = threading.Thread(target=loop, daemon=True, name="pepperton-engine")
        self._thread.start()

    def _close_books(self, reason):
        """Shut the experiment ledger, once, from any path — including the
        ones that end in a traceback (v3.6.2).

        THE LEDGER EXISTS SO A RUN CANNOT LIE ABOUT ITSELF AFTERWARD. Until
        now the only thing that closed it was stop(), reachable solely
        through the server's clean-shutdown handler — which calls
        save_state() first, in the same try. So a town killed by a
        *persistent* disk or database fault raised again on the way out, the
        exception was swallowed, stop() never ran, and data/experiments.json
        recorded `"ended_reason": "running"` for a town that was dead on the
        floor. Forever.

        That is the v3.2.1 SHUTDOWN GAP exactly, reached down a crash path
        the original fix never looked at. (Found by review, v3.6.2.)

        Never raises: the books closing is not allowed to mask the failure
        that is closing them, and a ledger that cannot be written is still
        better news than one that quietly says the town is fine."""
        if getattr(self, "_books_closed", False):
            return
        self._books_closed = True
        try:
            self.exp.close(self, reason)
        except Exception:
            print(f"[ENGINE] could not close the experiment ledger "
                  f"({reason}) — the run will read as unfinished",
                  flush=True)

    def stop(self):
        self.running = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        # books closed AFTER the last tick lands and BEFORE the handles go
        self._close_books("stopped")
        self.world.close()

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
                 "stalled": bool(p.get("stalled")),
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
            "vitals": self.vitals(),
            "townsfolk": self.folk.snapshot(),
        }

    @staticmethod
    def decision_source(reason):
        """Who actually chose: the villager's own model, or a stand-in.

        This exists because a ten-day experiment was silently corrupted.
        Ollama's request queue overflowed under three towns' load; the
        engine treated a rejected request exactly like a dead host and
        quietly seated MockBrain. Mock is deliberately lifelike — it goes
        to work between 8 and noon, it buys a cheap house when it's flush
        — so for days we watched a script and called it character. Nothing
        in the transcript said otherwise, because nothing in the transcript
        recorded WHO was thinking. Now it does.
        """
        r = (reason or "").lower()
        if r.startswith("possessed"):
            return "possessed"
        if "understudy acted" in r or r.startswith("mock"):
            return "understudy"
        if "unparseable" in r:
            return "unparsed"
        if "echoed the template" in r:
            return "echoed"
        if "host unreachable" in r:
            return "dark"
        return "model"

    def _record_provenance(self, agent, reason):
        src = self.decision_source(reason)
        self.exp.note_decision(src, day=self.world.clock.day)
        was = getattr(agent, "last_source", None)
        agent.last_source = src
        # A MIND THAT WAS NEVER UP MUST STILL ANNOUNCE ITSELF (v3.8).
        # This used to fire only on a CHANGE, so a villager who came back
        # from a restart already understudied went straight to `understudy`
        # with nothing to compare against — and run.log said nothing at all.
        # Hazel Pike ran as a script for fourteen days, voided a whole
        # window, and the loudest instrument we own was silent about it,
        # because silence was indistinguishable from health. Again.
        # Not in MOCK_MODE: a deliberately scripted town is not a surprise,
        # and the mode badge already says so. This warning is for a town
        # that believes it has live minds and does not.
        if was is None and src != "model" \
                and not getattr(config, "MOCK_MODE", False):
            print(f"[MINDS] {agent.name}: came up as {src.upper()} — "
                  f"{agent.model}@{agent.host} never answered. Anything "
                  f"this villager does is NOT evidence.", flush=True)
        if was is not None and was != src:
            # a mind going dark or coming back is worth a line in run.log —
            # it is the difference between a finding and an artifact
            if src == "model":
                print(f"[MINDS] {agent.name}: own model has the wheel again "
                      f"({agent.model}@{agent.host})", flush=True)
            elif src in ("understudy", "dark"):
                print(f"[MINDS] {agent.name}: {agent.model}@{agent.host} "
                      f"failed — {src} is acting. Anything this villager "
                      f"does now is NOT evidence.", flush=True)

    def minds_report(self):
        """Per-villager provenance, for the vitals strip and any experiment
        that wants to know whether its instruments were honest.

        The denominator is the AWAKE, because a sleeping villager makes no
        decisions and therefore carries no stamp. Counting the sleeping as
        missing minds turned a quiet night into a red alarm, and an alarm
        that cries wolf is worse than no alarm at all. (Caught by Brad, who
        noticed every town reading 6/7 had exactly one villager in bed.)
        """
        agents = list(self.world.agents.values())
        awake = [a for a in agents if not a.asleep]
        by_src = {}
        for a in awake:
            by_src.setdefault(getattr(a, "last_source", None) or "unknown",
                              []).append(a.name)
        understudied = sorted(by_src.get("understudy", [])
                              + by_src.get("dark", []))
        unparsed = sorted(by_src.get("unparsed", []))
        echoed = sorted(by_src.get("echoed", []))
        unknown = sorted(by_src.get("unknown", []))
        live = len(by_src.get("model", [])) + len(by_src.get("possessed", []))
        # red only when somebody is genuinely being faked OR handing our own
        # schema back; amber while a waking villager has yet to be observed;
        # green otherwise
        state = ("bad" if (understudied or unparsed or echoed)
                 else "warn" if unknown else "good")
        return {
            "live": live,
            "awake": len(awake),
            "total": len(agents),
            "asleep": len(agents) - len(awake),
            "state": state,
            "understudied": understudied,
            "unparsed": unparsed,
            "echoed": echoed,
            "unknown": unknown,
        }

    def vitals(self):
        """Aggregate town health, at a glance.

        Built because a real defect hid in plain sight: the town fund's
        back-wage debt grew to five times its cap while the town LOOKED
        recovered — public workers were getting shifts again — and the only
        way to see it was to instrument the code by hand. A town can be
        quietly dying in a way no single villager's line of dialogue will
        ever tell you. These numbers will.

        Pure observation: reads state, changes nothing. Safe to add while an
        experiment is running.
        """
        world = self.world
        if not getattr(config, "ECONOMY", False):
            return None
        agents = list(world.agents.values())
        purses = sorted(a.money for a in agents)
        mid = len(purses) // 2
        median = (purses[mid] if len(purses) % 2
                  else (purses[mid - 1] + purses[mid]) / 2) if purses else 0.0
        employed = [a for a in agents if a.workplace()]
        open_debt = [d for d in world.debts if d["status"] == "open"]
        villager_debt = sum(d["amount"] for d in open_debt
                            if d["debtor"] in world.agents)
        business_debt = sum(d["amount"] for d in open_debt
                            if d["debtor"] not in world.agents)
        for_sale = [k for k, v in world.locations.items() if v.get("for_sale")]
        homeless = [a for a in agents if not a.home]
        last_fc = getattr(world, "last_foreclosure_day", 0)
        return {
            "wealth_median": round(median, 2),
            "wealth_total": round(sum(purses), 2),
            "purse_poorest": round(purses[0], 2) if purses else 0.0,
            "purse_richest": round(purses[-1], 2) if purses else 0.0,
            "employed": len(employed),
            "worked_today": len([a for a in employed
                                 if a.name in world.worked_today]),
            # THE STRIP AND THE LEDGER MUST NOT DISAGREE (v3.6.2). A worker
            # sent home mid-shift by insolvency lands in BOTH worked_today
            # and turned_away_today, and attendance_ledger() checks
            # worked_today first — so the town's own public record narrates
            # them ON SHIFT while this counter called them turned away. Two
            # records of the same afternoon, disagreeing. The ledger's
            # reading is the fairer one and it is the one the villagers can
            # see, so the dashboard defers to it. (Found by review, v3.6.2.)
            #
            # NOTE FOR WHOEVER READS THIS NUMBER NEXT: it counts VILLAGERS
            # REFUSED A SHIFT because their employer could not pay. It has
            # never counted TOWNSFOLK who tried a door and found it shut —
            # that number is counted by nothing we own, which is the more
            # interesting hole. (claude/DAY131.md)
            "turned_away_today": len(
                set(getattr(world, "turned_away_today", ()))
                - set(getattr(world, "worked_today", ()))),
            # STRANGERS WHO TRIED A DOOR AND LEFT WITH THEIR MONEY (v3.8).
            # The number the Townsfolk experiment is actually about, and
            # until now it was counted by nothing. Distinct from the line
            # above, which is villagers refused a shift by their own
            # employer. Two different failures; two different counters.
            "customers_turned_away_today": getattr(
                world, "customers_turned_away_today", 0),
            "longest_absence": max(
                [world.absence_streaks.get(a.name, 0) for a in employed],
                default=0),
            "longest_run": max(
                [world.work_streaks.get(a.name, 0) for a in employed],
                default=0),
            "debt_villagers": round(villager_debt, 2),
            "debt_businesses": round(business_debt, 2),
            "houses_for_sale": len(for_sale),
            "homeless": len(homeless),
            "days_since_foreclosure": (world.clock.day - last_fc
                                       if last_fc else None),
            "outside_flow": round(getattr(world, "outside_flow", 0.0), 2),
            "bus_brought_in": round(getattr(self.bus, "brought_in", 0.0), 2),
            "town_fund": round(world.tills.get(config.TOWN_FUND, 0.0), 2),
            "poor_box": round(world.tills.get(
                getattr(config, "POOR_BOX", "the poor box"), 0.0), 2),
            "minds": self.minds_report(),
            "flags": self.flags(),
        }

    def flags(self):
        """Anomalies CALLED OUT, not merely logged. (v3.9.1)

        THE INSTRUMENT THAT WORKED AND NOBODY READ:

            d163 ATTENDANCE LEDGER — EARNED ELSEWHERE (paid work, own door
                 shut): the Rusty Tap — Nora took paid work elsewhere
            d164 EARNED ELSEWHERE: Rosie's Diner — Walt took paid work
            d167 EARNED ELSEWHERE: the plaza — Quinn took paid work

        Three times in five days the evening ledger stated this project's
        central finding in plain English, by villager and by door — and it
        still took a forensic investigation to surface it, because it was a
        normal-looking line in a transcript nobody greps.

        Every other instrument failure this week was an instrument that
        could not speak. This was one that spoke every evening into an empty
        room. **The fix for that is a detector, not a rereading.**
        (Design owed to review, v3.9.1.)

        Pure observation, computed from state that already exists. Changes
        nothing, moves no hash, cannot make a day dirty. It only decides
        what gets a line of its own instead of a line in a log."""
        world = self.world
        out = []
        agents = list(world.agents.values())
        if not agents:
            # A TOWN WITH NOBODY IN IT READS `good` ON EVERY OTHER GAUGE,
            # because every gauge we own asks a question about villagers.
            return ["EMPTY TOWN — no villagers"]

        employed = [a for a in agents if a.workplace()]
        # earned money today, but their own door never opened
        elsewhere = sorted(
            a.name for a in employed
            if a.name not in world.worked_today
            and world.earned_today.get(a.name, 0) > 0)
        for name in elsewhere:
            streak = world.absence_streaks.get(name, 0)
            out.append(f"EARNED ELSEWHERE: {name} took paid work while "
                       f"{world.agents[name].workplace()} stayed shut "
                       f"(day {streak} of the streak)")

        if employed and not (set(world.worked_today) & {a.name for a in employed}):
            out.append(f"NO DOOR OPENED TODAY — {len(employed)} villagers "
                       f"hold a workplace and none of them opened it")

        hungry = [a for a in agents if a.needs.get("fullness", 100) <= 0]
        if len(hungry) >= max(2, len(agents) // 2):
            out.append(f"FAMINE: {len(hungry)} of {len(agents)} at fullness "
                       f"zero")

        # how much of their own life can they actually reach? Volume drives
        # it, not window size. (claude/WHY-THE-DOORS-STAY-SHUT.md)
        try:
            day = max(1, world.clock.day)
            per_day = [self.memory.count(a.name) / day for a in agents]
            busiest = max(per_day)
            k = max(1, int(getattr(config, "MEMORY_TOP_K", 8)))
            hours = round(24.0 * k / busiest, 1) if busiest else None
            if hours is not None and hours < 24:
                out.append(f"REACHABLE PAST ~{hours}h — the busiest villager "
                           f"writes {busiest:.0f} memories a day against "
                           f"MEMORY_TOP_K={k}")
        except Exception:
            pass

        vacant = world.open_positions() if hasattr(world, "open_positions") else {}
        jobless = [a for a in agents if not a.workplace()]
        if vacant and jobless:
            out.append(f"{len(vacant)} post(s) vacant and {len(jobless)} "
                       f"villager(s) with no workplace — "
                       f"{', '.join(sorted(vacant))}")
        return out

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
            "source": getattr(a, "last_source", None),
            "memory_count": self.memory.count(name),
            "recent_memories": self.memory.recent(name, 20),
            "debts_owed": self.world.open_debts(debtor=name),
            "debts_owed_to_me": self.world.open_debts(creditor=name),
            "promises": [p for p in self.world.promises
                         if p["status"] == "open" and
                         name in (p["maker"], p["to"])],
        }
