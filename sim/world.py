"""The world: locations, clock, and the physics that keep models honest.

Rule one, inherited from a previous administration: the world enforces
reality, not the models. An agent SAYS "I take the bread"; the world
decides whether there is bread. Liars get caught by physics.
"""

import re
import threading

import config
from sim.causality import Command, CommandResult, DomainEvent
from sim.ledger import Ledger
from sim.social import Commitment, Interaction, MAX_INTERACTION_ROUNDS, SPEECH_ACTS
from sim.store import TownStore, scrub_surrogates


_WORDS = re.compile(r"[a-z0-9']+")


def _toks(norm):
    return set(_WORDS.findall(norm))


# Models sometimes leak JSON fragments into their speech, e.g.
#   'Mornin' Roy!', 'to': 'Roy Tibbs'
# Cut everything from a leaked  ', 'to':  /  ", "to":  onward, then strip
# stray wrapping quotes.
_JSON_LEAK = re.compile(r"""['"]\s*,\s*['"]?(to|action|text|note)['"]?\s*:.*$""",
                        re.IGNORECASE | re.DOTALL)


def _sanitize_speech(text):
    stripped = _JSON_LEAK.sub("", text).strip()
    # if the leak-strip fired, it consumed the closing quote — drop the orphan opener
    if stripped != text.strip() and stripped[:1] in "'\"":
        stripped = stripped[1:].strip()
    text = stripped
    # unwrap matching pairs of stray outer quotes (repeat if nested)
    while len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


class Clock:
    def __init__(self):
        h, m = map(int, config.SIM_START.split(":"))
        self.day = 1
        self.minutes = h * 60 + m
        self.step = config.PACING[config.PACING_MODE]["sim_minutes_per_tick"]

    def tick(self):
        self.minutes += self.step
        if self.minutes >= 24 * 60:
            self.minutes -= 24 * 60
            self.day += 1

    @property
    def hhmm(self):
        return f"{self.minutes // 60:02d}:{self.minutes % 60:02d}"

    @property
    def label(self):
        return f"Day {self.day}, {self.hhmm}"

    @property
    def is_night(self):
        return self.minutes >= 22 * 60 + 30 or self.minutes < 6 * 60 + 30

    def at(self, hhmm):
        """True if the current tick covers sim time hhmm."""
        h, m = map(int, hhmm.split(":"))
        t = h * 60 + m
        prev = self.minutes - self.step
        if prev < 0:
            return t >= prev + 24 * 60 or t < self.minutes
        return prev < t <= self.minutes


# every World built in this process, so a test harness can release durable
# store resources before wiping data/ (see World.close_all)
_OPEN_WORLDS = []


class World:
    def __init__(self, cast, world_id="legacy", store=None):
        self.world_id = world_id
        self.store = store or TownStore(world_id=world_id)
        self.clock = Clock()
        self.tick_no = 0
        # v3.9.4 — verbs villagers reached for that do not exist. In memory
        # only: never serialised, never in a snapshot, never shown to a
        # villager. See _verb_idle.
        self.unknown_verbs = {}
        self.unknown_verb_actors = {}
        self.unknown_verbs_overflow = 0
        self.agents = {a.name: a for a in cast}
        self.locations = dict(config.LOCATIONS)
        for a in cast:
            self.locations[a.home] = {"desc": f"{a.name}'s house", "home_of": a.name}
        self.events = []           # rolling event feed for the UI
        self.recent_says = []      # (tick, location, normalized_text, speaker) for the Echo Ban
        self.projects = [
            {**p, "done": 0, "complete": False, "contributors": {}}
            for p in config.PROJECTS
        ]
        # the Fall Fair Act: even the town-charter projects carry permits
        if getattr(config, "PERMITS_ENABLED", True):
            for proj in self.projects:
                proj["permit_due"] = self.clock.day + self.permit_window(
                    proj["work"])
        self._permit_day_done = 0
        # THE TOWN CAN MAKE WORK (v3.11): posts this town created for itself
        # by finishing something. {job: location}. Merged over
        # config.WORKPLACES by open_positions(); persisted in save_state.
        # config.WORKPLACES is written before Day 1 and can never grow, which
        # is why 491 build actions in Pepperton produced no employment.
        self.extra_workplaces = {}
        # the Working Day: who clocked in today, what they earned, and
        # each worker's running absence streak (public record)
        self.worked_today = set()
        self.earned_today = {}
        self.absence_streaks = {}
        self.work_streaks = {}      # the Crane Bonus: absence's mirror
        self.turned_away_today = set()   # showed up; the town couldn't pay
        # money that walked into town and walked back out again (v3.8) —
        # a stranger who tried a door, found it shut, and left. Counted at
        # the door, not at the narration.
        self.customers_turned_away_today = 0
        # Took a townsperson's odd job. EARNED money, but did NOT open their
        # own door — tracked apart from worked_today, because conflating the
        # two let an employed villager launder a no-show into a perfect
        # attendance record and a live Crane Bonus streak while their
        # business stayed dark all day. (Found by review, v3.3.1.)
        self.odd_jobs_today = set()
        # Net money created from outside the loop, ever. The bus is not the
        # only faucet — the Director's windfall conjures cash into a pocket
        # and a stranger steps off the coach with a wallet, while the heist
        # burns money out of the world entirely. If the conservation check is
        # to mean anything it has to net ALL of them, not just the bus.
        # (Found by review, v2.9.2.)
        self.outside_flow = 0.0
        self.last_foreclosure_day = 0    # for the vitals readout
        self._bell_day_done = 0
        self._attendance_day_done = 0
        # economics live in sim/ledger.py; the World only hosts the physics
        self.ledger = Ledger(self)
        self._events_lock = threading.Lock()
        self._event_seq = 0
        self._command_seq = 0
        self._social_seq = 0
        self.interactions = {}
        self.commitments = {}
        self._active_command = None
        self._closed = False
        _OPEN_WORLDS.append(self)

    def close(self):
        """Release durable-store resources owned by this world."""
        if self._closed:
            return
        self._closed = True
        self.store.close()
        try:
            _OPEN_WORLDS.remove(self)
        except ValueError:
            pass

    @staticmethod
    def close_all():
        """Close every World this process has opened. Test harnesses call
        this before wiping data/."""
        for world in list(_OPEN_WORLDS):
            world.close()
        # ...and any bare MemoryStore nobody registered a World for (v3.8.6)
        from sim.memory import MemoryStore
        MemoryStore.close_all()

    # ------------------------------------------------------------ helpers
    def occupants(self, loc, exclude=None):
        return [a for a in self.agents.values()
                if a.location == loc and a.name != exclude and not a.asleep]

    def public_locations(self):
        return [k for k in self.locations if "home_of" not in self.locations[k]]

    def food_locations(self):
        return [k for k, v in self.locations.items() if v.get("sells_food")]

    def radio_location(self):
        for k, v in self.locations.items():
            if v.get("radio"):
                return k
        return None

    def bank_name(self):
        for k, v in self.locations.items():
            if v.get("bank"):
                return k
        return None

    # ------------------------------------------------ goods (v2.5)
    @staticmethod
    def goods_catalog():
        return getattr(config, "GOODS", {
            "a proper mattress": {"cost": 25, "sold_at": "Pepper & Sons",
                "pitch": "sleep like the righteous — rest at home recovers faster"},
            "carpenter's tools": {"cost": 20, "sold_at": "Pepper & Sons",
                "pitch": "arrive at any build site ready — your first swing counts double"},
            "a fine hat": {"cost": 15, "sold_at": "Pepper & Sons",
                "pitch": "people notice a hat like this"},
            "a pocket watch": {"cost": 30, "sold_at": "Pepper & Sons",
                "pitch": "the quiet signal of a person whose time matters"},
            "a paperback novel": {"cost": 6, "sold_at": "Pepper & Sons",
                "pitch": "something to be mid-way through; lends itself to conversation"},
            "a coffee": {"cost": 2, "sold_at": "Rosie's Diner",
                "consumable": True,
                "pitch": "hot, immediate, and cheaper than a meal"},
        })

    def goods_sold_here(self, location):
        return {k: v for k, v in self.goods_catalog().items()
                if v.get("sold_at") == location}

    def _verb_buy(self, agent, action):
        wanted = str(action.get("item") or "").strip().lower()
        if not wanted:
            return False, "reached for their wallet, forgot what for"
        # the Holt Act: seized houses are bought like anything else — by
        # name, at the bank's teller window or standing at the door
        # Prefer the house they NAMED; then the one they're standing at; only
        # then a generic "buy the house", and only if exactly one is listed —
        # otherwise two simultaneous foreclosures send a buyer to the wrong
        # address with a confusing refusal. (Found by review, v2.9.2.)
        for_sale = [h for h, l in self.locations.items() if l.get("for_sale")]
        named = [h for h in for_sale if wanted and wanted in h.lower()]
        here = [h for h in for_sale if h == agent.location]
        generic = (for_sale if len(for_sale) == 1
                   and any(w in wanted for w in ("house", "home", "deed"))
                   else [])
        for house in (named or here or generic):
            bank = self.bank_name()
            if agent.location not in (bank, house):
                return False, (f"the deed to {house} is signed at "
                               f"{bank} (or at the door itself)")
            return self.ledger.sell_seized_house(agent, house)
        catalog = self.goods_catalog()
        item = next((k for k in catalog if wanted in k.lower()
                     or k.lower() in wanted), None)
        if item is None:
            names = ", ".join(catalog)
            sale = [f"{h} (${l['for_sale']}, deed at the bank)"
                    for h, l in self.locations.items() if l.get("for_sale")]
            if sale:
                names += "; FOR SALE: " + ", ".join(sale)
            return False, f"nobody sells {action.get('item')!r} — the catalogs of this town: {names}"
        spec = catalog[item]
        if agent.location != spec["sold_at"]:
            return False, f"{item} is sold at {spec['sold_at']} — that's where the counter is"
        cost = spec["cost"]
        if agent.money < cost:
            return False, (f"{item} costs ${cost} and they have "
                           f"${agent.money:.0f} — something to work toward")
        if not spec.get("consumable") and item in agent.possessions:
            return False, f"already owns {item} — one is plenty"
        agent.money -= cost
        self._till_deposit(agent.location, cost)
        if spec.get("consumable"):
            agent.needs["energy"] = min(100, agent.needs["energy"] + 12)
            self.emit("action", agent.name,
                      f"ordered {item} at the counter (-${cost})",
                      agent.location)
            return True, f"had {item} (-${cost}) — feeling sharper"
        agent.possessions.append(item)
        self.emit("action", agent.name,
                  f"bought {item} at {agent.location} (-${cost}) — "
                  f"earned money, spent well", agent.location)
        return True, f"bought {item} (-${cost}); it's theirs now"

    # --------------------------------------------- the working day (v2.6)
    # ------------------------------------------- the Crane Bonus (v2.9)
    @staticmethod
    def loyalty_cfg():
        cfg = {"enabled": False, "streak_days": 5, "raise_pct": 0.20,
               "max_steps": 3, "milestone_bonus": 15}
        cfg.update(getattr(config, "LOYALTY", {}))
        return cfg

    def loyalty_steps(self, name):
        """How many raises this villager's record has earned. Zero when the
        Act is dark, which is how it ships."""
        cfg = self.loyalty_cfg()
        if not cfg["enabled"] or cfg["streak_days"] <= 0:
            return 0
        streak = self.work_streaks.get(name, 0)
        return min(cfg["max_steps"], streak // cfg["streak_days"])

    def wage_multiplier(self, name):
        cfg = self.loyalty_cfg()
        return 1.0 + cfg["raise_pct"] * self.loyalty_steps(name)

    def _loyalty_tick(self, worker, showed):
        """Called once per villager at the evening ledger. Returns a short
        public phrase for the ledger line, or ''."""
        cfg = self.loyalty_cfg()
        if not cfg["enabled"]:
            return ""
        name = worker.name
        if not showed:
            had = self.loyalty_steps(name)
            self.work_streaks[name] = 0
            if had:
                worker.pending.append({
                    "text": ("You broke your run. The raise you earned by "
                             "showing up every day is gone — it goes back to "
                             "the base wage, and it starts over tomorrow."),
                    "interrupt": True, "sim_time": self.clock.hhmm,
                })
            return ""
        before = self.loyalty_steps(name)
        self.work_streaks[name] = self.work_streaks.get(name, 0) + 1
        streak = self.work_streaks[name]
        after = self.loyalty_steps(name)
        if after > before:
            bonus = min(float(cfg["milestone_bonus"]),
                        self.tills.get(config.TOWN_FUND, 0.0))
            if bonus > 0:
                self.tills[config.TOWN_FUND] = round(
                    self.tills[config.TOWN_FUND] - bonus, 2)
                worker.money += bonus
            pct = int(cfg["raise_pct"] * after * 100)
            self.emit("world", None,
                      f"THE CRANE BONUS — {name} has worked {streak} days "
                      f"running. The town fund pays ${bonus:.0f} in hand and "
                      f"the wage goes up {pct}%, for as long as the run "
                      f"holds. This town pays for showing up.", "the plaza")
            worker.pending.append({
                "text": (f"{streak} days running. The town noticed: ${bonus:.0f} "
                         f"paid out of the fund, and your wage is up {pct}% "
                         f"until the day you don't show."),
                "interrupt": True, "sim_time": self.clock.hhmm,
            })
        if after:
            return (f", {streak} days running, +"
                    f"{int(cfg['raise_pct'] * after * 100)}%")
        return f", {streak} days running" if streak > 1 else ""

    def morning_bell(self):
        """08:00: every employed villager is reminded the shift starts —
        and that the town notices who shows. Resets the day's attendance."""
        if not getattr(config, "ATTENDANCE_ENABLED", True):
            return
        day = self.clock.day
        if self._bell_day_done >= day:
            return
        self._bell_day_done = day
        self.worked_today = set()
        self.customers_turned_away_today = 0
        self.turned_away_today = set()
        self.odd_jobs_today = set()
        self.earned_today = {}
        openings = (self.open_positions()
                    if getattr(config, "HIRING_ENABLED", True) else {})
        for worker in self.agents.values():
            workplace = worker.workplace()
            if not workplace:
                if openings:
                    sits = "; ".join(f"{j} at {w}"
                                     for j, w in sorted(openings.items()))
                    worker.pending.append({
                        "text": (f"The morning bell rings for everyone but "
                                 f"you — you have no work. The town is "
                                 f"HIRING: {sits}. Show up and use the "
                                 f"work action; showing up IS the "
                                 f"interview. Wages are real money."),
                        "interrupt": worker.money < config.MEAL_COST,
                        "sim_time": self.clock.hhmm,
                    })
                continue
            streak = self.absence_streaks.get(worker.name, 0)
            streak_bit = (f" You haven't opened those doors in {streak} "
                          f"days, and the ledger says so in public."
                          if streak >= 2 else "")
            steps = self.loyalty_steps(worker.name)
            run = self.work_streaks.get(worker.name, 0)
            if steps:
                pct = int(self.loyalty_cfg()["raise_pct"] * steps * 100)
                streak_bit += (f" You have worked {run} days running and the "
                               f"town pays you {pct}% over base for it — "
                               f"until the day you don't show.")
            elif run >= 2:
                streak_bit += (f" {run} days running so far; the town pays "
                               f"more for a long run.")
            worker.pending.append({
                "text": (f"The morning bell: your shift at {workplace} "
                         f"starts now. The town posts the attendance ledger "
                         f"every evening — who worked, who didn't."
                         f"{streak_bit}"),
                "interrupt": worker.money < config.MEAL_COST,
                "sim_time": self.clock.hhmm,
            })

    def attendance_ledger(self):
        """18:00: the day's labor, posted town-wide. Presence earns public
        credit; absence earns a public count."""
        if not getattr(config, "ATTENDANCE_ENABLED", True):
            return
        day = self.clock.day
        if self._attendance_day_done >= day:
            return
        self._attendance_day_done = day
        showed, absent, refused, oddjob = [], [], [], []
        for worker in self.agents.values():
            workplace = worker.workplace()
            if not workplace:
                continue
            if worker.name in self.worked_today:
                self.absence_streaks[worker.name] = 0
                earned = self.earned_today.get(worker.name, 0.0)
                loyal = self._loyalty_tick(worker, True)
                showed.append(f"{worker.name.split()[0]} ({workplace}"
                              + (f", +${earned:.0f}" if earned else "")
                              + loyal + ")")
            elif worker.name in self.turned_away_today:
                # present and refused: the run holds, the record is clean,
                # and the town is told whose payroll failed — not whose
                # character did
                refused.append(f"{workplace} — {worker.name.split()[0]} came "
                               f"in, no shift to give")
            else:
                self._loyalty_tick(worker, False)
                self.absence_streaks[worker.name] = \
                    self.absence_streaks.get(worker.name, 0) + 1
                run = self.absence_streaks[worker.name]
                if worker.name in getattr(self, "odd_jobs_today", ()):
                    # They worked. They earned. They did not open their own
                    # door, and the town can see the difference — which is
                    # the whole point of saying it out loud instead of
                    # quietly filing it under 'absent'.
                    oddjob.append(f"{workplace} — {worker.name.split()[0]} "
                                  f"took paid work elsewhere instead"
                                  + (f", day {run} running" if run > 1 else ""))
                else:
                    absent.append(f"{workplace} — {worker.name.split()[0]} absent"
                                  + (f", day {run} running" if run > 1 else ""))
        if not showed and not absent and not refused and not oddjob:
            return
        lines = []
        if showed:
            lines.append("ON SHIFT today: " + "; ".join(showed) + ".")
        if refused:
            lines.append("TURNED AWAY (showed up, no work to give): "
                         + "; ".join(refused) + ".")
        if oddjob:
            lines.append("EARNED ELSEWHERE (paid work, own door shut): "
                         + "; ".join(oddjob) + ".")
        if absent:
            lines.append("DOORS NEVER OPENED: " + "; ".join(absent) + ".")
        if getattr(config, "HIRING_ENABLED", True):
            digest = self.vacancy_digest()
            if digest:
                lines.append("SITUATIONS VACANT: " + digest
                             + " — show up and work.")
        text = "ATTENDANCE LEDGER — " + " ".join(lines)
        self.emit("world", None, text, "the plaza")
        for villager in self.agents.values():
            villager.pending.append({
                "text": f"The evening attendance ledger is posted: {text}",
                "interrupt": False,
                "sim_time": self.clock.hhmm,
            })

    # -------------------------------------------------- permits (v2.4)
    @staticmethod
    def permit_window(work):
        """Days the town grants to finish a project of this size."""
        import math as _math
        return max(getattr(config, "PERMIT_MIN_DAYS", 4),
                   _math.ceil(work / getattr(config, "PERMIT_SHIFTS_PER_DAY", 8)))

    def permit_sweep(self):
        """The 08:00 building-inspector pass: fines at the deadline,
        condemnation two days later. Idempotent per day."""
        if not getattr(config, "PERMITS_ENABLED", True):
            return
        day = self.clock.day
        if self._permit_day_done >= day:
            return
        self._permit_day_done = day
        for proj in list(self.projects):
            if proj["complete"] or proj.get("permit_due") is None:
                continue
            # the fine, once, at the deadline
            if day > proj["permit_due"] and not proj.get("fined"):
                proj["fined"] = True
                proj["condemn_day"] = day + getattr(config, "CONDEMN_GRACE_DAYS", 2)
                proposer = self.agents.get(proj.get("proposed_by") or "")
                # THE WORLD DOES NOT LIE TO THEM. If the wrecking crew has
                # been retired, the warning cannot threaten a demolition
                # that is never coming — it says what actually happens.
                grace = getattr(config, "CONDEMN_GRACE_DAYS", 2)
                wrecks = getattr(config, "CONDEMN_ENABLED", True)
                fate_you = (f"Finish it within {grace} days or the town tears "
                            f"it down." if wrecks else
                            f"In {grace} days it comes off the notice board "
                            f"and stays where it is, half-built. Nobody will "
                            f"tear it down and nobody will chase you about it "
                            f"again — it will just stand there unfinished for "
                            f"as long as it stands.")
                fate_town = (f"Condemnation in {grace} days unless it gets "
                             f"finished." if wrecks else
                             f"In {grace} days it comes off the board and is "
                             f"left standing unfinished.")
                fate_others = (f"It gets condemned in {grace} days unless "
                               f"someone finishes it." if wrecks else
                               f"In {grace} days it comes off the board — it "
                               f"stays standing at {proj['site']} either way, "
                               f"and anyone can still work on it.")
                fine_bit = ""
                if proposer is not None:
                    fine = getattr(config, "PERMIT_FINE", 10)
                    if proposer.money >= fine:
                        proposer.money -= fine
                        self.ledger.deposit(config.TOWN_FUND, fine)
                        fine_bit = (f" {proposer.name.split()[0]} is fined "
                                    f"${fine} as the permit holder.")
                    else:
                        self.ledger.add_debt(
                            proposer.name, config.TOWN_FUND, fine,
                            f"permit fine — {proj['name']} unfinished",
                            due_day=day + 2)
                        fine_bit = (f" {proposer.name.split()[0]} is fined "
                                    f"${fine} — on the ledger, since they "
                                    f"can't pay.")
                    proposer.pending.append({
                        "text": (f"The permit on YOUR project, "
                                 f"{proj['name']}, has expired at "
                                 f"{proj['done']}/{proj['work']} built."
                                 f"{fine_bit} {fate_you}"),
                        "interrupt": True, "sim_time": self.clock.hhmm,
                    })
                self.emit("world", None,
                          f"PERMIT EXPIRED: {proj['name']} sits at "
                          f"{proj['done']}/{proj['work']}.{fine_bit} "
                          f"{fate_town}", proj["site"])
                for villager in self.agents.values():
                    if villager.name != proj.get("proposed_by"):
                        villager.pending.append({
                            "text": (f"Notice board: the permit on "
                                     f"{proj['name']} has EXPIRED "
                                     f"({proj['done']}/{proj['work']} done). "
                                     f"{fate_others}"),
                            "interrupt": False, "sim_time": self.clock.hhmm,
                        })
                continue
            # the wrecking crew — or, since v3.5, the shelf
            if proj.get("condemn_day") and day > proj["condemn_day"]:
                # THE ONE THING THEY RELIABLY DO IS INVENT PROJECTS.
                #
                # A fall fair, a cookout, a feast festival, a flea market,
                # porch lighting, a gazebo, Hope Lighting, and a JOB HUB
                # built by unemployed people. Nobody prompted any of it. In
                # a hundred and thirty days it is the only appetite these
                # villagers have ever shown.
                #
                # And three times we tore one down for being slow and fined
                # the proposer money he did not have. The world's answer to
                # the only initiative it ever saw was a bulldozer.
                #
                # So it stops. The half-built thing STAYS STANDING, in the
                # world, where anyone can still pick up a hammer. It just
                # stops holding a slot on the notice board — because the
                # board is about permits and attention, and a shabby
                # unfinished structure in the park is about neither.
                # (Brad, 2026-08-08.)
                if not getattr(config, "CONDEMN_ENABLED", True):
                    proj["stalled"] = True
                    proj["condemn_day"] = None
                    self.emit("world", None,
                              f"{proj['name']} is off the notice board at "
                              f"{proj['done']}/{proj['work']}. Nobody tore it "
                              f"down. It is still standing at {proj['site']}, "
                              f"unfinished, and anyone who wants to pick it "
                              f"back up still can.", proj["site"])
                    for villager in self.agents.values():
                        shifts = proj["contributors"].get(villager.name, 0)
                        villager.pending.append({
                            "text": (f"{proj['name']} came off the notice "
                                     f"board unfinished at "
                                     f"{proj['done']}/{proj['work']}. It is "
                                     f"still there at {proj['site']}."
                                     + (f" Your {shifts} shifts are still in "
                                        f"it." if shifts else "")),
                            "interrupt": False, "sim_time": self.clock.hhmm,
                        })
                    continue
                self.projects.remove(proj)
                self.emit("world", None,
                          f"CONDEMNED: the half-built {proj['name']} "
                          f"({proj['done']}/{proj['work']}) is torn down and "
                          f"the materials reclaimed. The notice board has "
                          f"room again.", proj["site"])
                for villager in self.agents.values():
                    shifts = proj["contributors"].get(villager.name, 0)
                    if shifts:
                        villager.pending.append({
                            "text": (f"The town tore down {proj['name']} — "
                                     f"your {shifts} shifts of work went "
                                     f"with it. Somebody should have "
                                     f"finished what got started."),
                            "interrupt": True, "sim_time": self.clock.hhmm,
                        })
                    else:
                        villager.pending.append({
                            "text": (f"{proj['name']} was condemned and torn "
                                     f"down — the board has an open slot "
                                     f"again."),
                            "interrupt": False, "sim_time": self.clock.hhmm,
                        })

    # -------------- the ledger facade (economics: sim/ledger.py) ----------
    @property
    def tills(self):
        return self.ledger.tills

    @property
    def debts(self):
        return self.ledger.debts

    @debts.setter
    def debts(self, value):
        self.ledger.debts = value

    @property
    def promises(self):
        return self.ledger.promises

    @promises.setter
    def promises(self, value):
        self.ledger.promises = value

    def add_debt(self, debtor, creditor, amount, reason, due_day=None,
                 merge=False):
        return self.ledger.add_debt(debtor, creditor, amount, reason,
                                    due_day=due_day, merge=merge)

    def open_debts(self, debtor=None, creditor=None):
        return self.ledger.open_debts(debtor=debtor, creditor=creditor)

    def pay_wage(self, agent, wage):
        self.ledger.pay_wage(agent, wage)

    def wage_debt_of(self, till_key):
        return self.ledger.wage_debt_of(till_key)

    def insolvent(self, till_key):
        """Can this employer stand behind the shift it is about to commit to?

        v2.9.2 asked "is the till below one tick of wage", which a stress
        test then took apart: ANY momentary deposit — rent from an unrelated
        tenant, another worker's income-tax withholding, both of which land
        mid-tick AFTER that tick's one-shot debt settlement has already run —
        pushes the balance over the line for an instant and reopens hiring
        on a fund that is catastrophically in arrears. The shift then locks
        in for up to sixteen ticks. Measured by the reviewer: back wages
        climbing 404 → 430 through one such gap. (Found by review, v3.3.1.)

        A balance is a snapshot; a debt is a position. So ask the honest
        question instead: is this employer past its arrears cap AND unable
        to cover what it already owes? Cash that genuinely covers the back
        wages is solvency — a dollar passing through on its way somewhere
        else is not.
        """
        cap = getattr(config, "WAGE_DEBT_CAP", 40)
        owed = self.wage_debt_of(till_key)
        if owed < cap:
            return False        # still inside its overdraft; hire and hope
        return self.tills.get(till_key, 0.0) < owed

    def shift_ended_by_insolvency(self, agent):
        """Checked every paying tick, not just at hiring.

        The gate above only ran when a shift STARTED, and a shift runs for
        sixteen ticks. So one instant of apparent solvency bought sixteen
        ticks of unchecked borrowing, and the wage debt booked on each of
        those ticks never looked at the cap again.

        Public payroll only. A SHOP with a dry till must always be able to
        stay open and hope a customer walks in — that is the Tibbs Door, and
        it is not what this is for. There is no door at the park for a
        customer to walk through.
        """
        if not getattr(config, "ECONOMY", False):
            return False
        till_key = self._wage_till_key(agent)
        if till_key != getattr(config, "TOWN_FUND", "the town fund"):
            return False
        if not self.insolvent(till_key):
            return False
        agent.activity = {"type": "idle", "until_tick": self.tick_no + 4,
                          "note": "sent home mid-shift — the town ran dry"}
        self.turned_away_today.add(agent.name)
        self.emit("action", agent.name,
                  f"was sent home part-way through the shift — the town "
                  f"fund is ${self.wage_debt_of(till_key):.0f} behind on "
                  f"wages and cannot stand behind another hour",
                  agent.location)
        agent.pending.append({
            "text": ("You were sent home mid-shift. The town owes more in "
                     "back wages than it holds. What you already worked "
                     "today still stands on the books."),
            "interrupt": True, "sim_time": self.clock.hhmm,
        })
        return True

    def settle_business_debts(self):
        self.ledger.settle_business_debts()

    def credit_report(self, agent):
        return self.ledger.credit_report(agent)

    def morning_ledger(self):
        self.ledger.morning_sweep()

    def _wage_till_key(self, agent):
        return self.ledger.wage_till_key(agent)

    def _till_deposit(self, location, amount):
        self.ledger.deposit(location, amount)

    def _apply_to_debts(self, payer, payee, amount):
        return self.ledger.apply_payment_to_debts(payer, payee, amount)

    def _settle_promises_on_payment(self, payer, payee_name):
        self.ledger.settle_promises_on_payment(payer, payee_name)

    def _detect_promise(self, agent, target, norm, text):
        self.ledger.detect_promise(agent, target, norm, text)

    def _is_money_promise(self, norm):
        return self.ledger.is_money_promise(norm)

    # ------------------------------------------------------------- events
    def emit(self, etype, agent, text, loc, target=None, deliver=True,
             source=None, cause_event_id=None, command_id=None,
             salience=None, thread_id=None, topic=None):
        """Record an event; queue it as a perception for co-located agents."""
        # Scrub half-characters at the door (v3.3.3). A villager typed a flag
        # emoji, one half of the surrogate pair survived into the transcript,
        # and every subsequent start of a 101-day-old town died rewriting it.
        # This is the narrowest place to stand: every event in Pepperton — and
        # so every memory derived from one — passes through here.
        text = scrub_surrogates(text)
        with self._events_lock:
            self._event_seq += 1
            active = self._active_command
            source = source or (active.source if active else
                                ("villager" if agent else "world"))
            cause_event_id = cause_event_id or (
                active.cause_event_id if active else None)
            command_id = command_id or (active.command_id if active else None)
            salience = salience or (active.salience if active else "normal")
            thread_id = thread_id or (active.thread_id if active else None)
            if topic is None and active:
                topic = active.topic or active.payload.get("action")
            ev = DomainEvent(
                wid=self.world_id,
                seq=self._event_seq,
                event_id=f"{self.world_id}:event:{self._event_seq}",
                tick=self.tick_no,
                day=self.clock.day,
                sim_time=self.clock.hhmm,
                type=etype,
                agent=agent,
                location=loc,
                target=target,
                text=text,
                source=source,
                cause_event_id=cause_event_id,
                command_id=command_id,
                salience=salience,
                thread_id=thread_id,
                topic=topic,
            ).as_dict()
            who = f"{agent} -> {target}" if target else (agent or "WORLD")
            log_written = self.store.append_event_with_log(
                ev, f"[{self.clock.label}] [{loc}] {etype.upper():8s} "
                f"{who}: {text}\n")
            self.events.append(ev)
            if len(self.events) > 600:
                self.events = self.events[-400:]
        if not log_written:
            print("[STORE] transcript.log append failed; the durable event "
                  "stream will rebuild it on recovery", flush=True)
        if deliver:
            self._deliver(ev)
        return ev

    _START_TALK = ("start", "begin", "kick off", "kicking off", "get going",
                   "get this going", "plan ", "planning", "organize", "organizing",
                   "get started", "getting started")
    _BUILD_TALK = ("build", "work on", "working on", "help with", "volunteer",
                   "get to work")
    _TRAVEL_TALK = ("going to", "heading to", "head to", "headed to",
                    "on my way to", "i'll go to", "gonna go to", "let's go to",
                    "meet me at", "see you at")

    @staticmethod
    def _core_name(n):
        w0 = str(n).lower().split()
        while w0 and w0[0] in ("a", "an", "the"):
            w0 = w0[1:]
        return " ".join(w0)

    def _reality_check(self, agent, norm):
        """Veto talk that contradicts observable reality: 'planning' things
        already built or building, and announcing trips to where you stand."""
        for p in self.projects:
            core = self._core_name(p["name"])
            if not core or core not in norm:
                continue
            if p["complete"] and any(w in norm for w in
                                     self._START_TALK + self._BUILD_TALK +
                                     ("finish", "get this done", "get it done")):
                return (f"{p['name']} is FINISHED — it's standing at {p['site']} "
                        f"right now. Talking about starting or working on it makes "
                        f"no sense; go look at it, or pick something real to say")
            if not p["complete"]:
                if ("finished" in norm or "completed" in norm) and \
                        not any(w in norm for w in ("finish ", "finishing", "let's finish")):
                    return (f"{p['name']} is NOT finished — it's {p['done']}/"
                            f"{p['work']} done. Check the notice board before "
                            f"declaring things complete")
                if p["done"] > 0 and any(w in norm for w in self._START_TALK):
                    mine = p["contributors"].get(agent.name, 0)
                    extra = (f" — {agent.name.split()[0]} personally has {mine} shifts "
                             f"in it already" if mine else "")
                    return (f"{p['name']} is ALREADY under construction "
                            f"({p['done']}/{p['work']} done{extra}). It doesn't need "
                            f"starting or planning — it needs hammers")
                if agent.location == p["site"] and \
                        any(w in norm for w in ("finish", "get this done",
                                                "get it done", "let's build",
                                                "keep building", "lend a hand",
                                                "while we eat")):
                    return (f"is standing AT {p['site']}, next to the unfinished "
                            f"{p['name']} ({p['done']}/{p['work']}). Cheerleading "
                            f"from the site doesn't drive nails — use the build "
                            f"action or talk about something else")
        here = self._core_name(agent.location)
        if here and here in norm and any(w in norm for w in self._TRAVEL_TALK):
            return (f"is ALREADY standing in {agent.location} — announcing a trip "
                    f"to the room you're in fools nobody. Just do the thing")
        return None

    def match_projects(self, name):
        """All incomplete projects matching a name — exact core match wins
        outright; otherwise contains-matches (possibly several)."""
        core = self._core_name(name)
        if not core:
            return []
        exact = [p for p in self.projects if not p["complete"]
                 and self._core_name(p["name"]) == core]
        if exact:
            return exact
        return [p for p in self.projects if not p["complete"]
                and (core in self._core_name(p["name"])
                     or self._core_name(p["name"]) in core)]

    def find_project(self, name):
        name = str(name or "").strip().lower()
        for p in self.projects:
            if p["complete"]:
                continue
            if not name or name in p["name"].lower() or p["name"].lower() in name:
                return p
        return None

    def _deliver(self, ev):
        """Turn an event into perceptions for agents who'd notice it."""
        interrupts = ev["type"] == "radio" or (
            ev["type"] == "say" and config.AMBIENT_SPEECH_INTERRUPTS
        )
        for a in self.agents.values():
            if a.name == ev["agent"] or a.location != ev["location"] or a.asleep:
                continue
            if ev["type"] == "say":
                if ev["target"] == a.name:
                    line = f'{ev["agent"]} said to you: "{ev["text"]}"'
                elif ev["target"]:
                    line = f'You overheard {ev["agent"]} tell {ev["target"]}: "{ev["text"]}"'
                else:
                    line = f'{ev["agent"]} said: "{ev["text"]}"'
            elif ev["type"] == "radio":
                line = f"The radio: {ev['text']}"
            elif ev["type"] == "arrive":
                line = f"{ev['agent']} arrived at {ev['location']}."
            elif ev["type"] == "leave":
                line = f"{ev['agent']} left, heading to {ev['text']}."
            else:
                line = f"{ev['agent']}: {ev['text']}"
            a.pending.append({
                "text": line,
                "interrupt": interrupts or ev.get("target") == a.name,
                "sim_time": ev["sim_time"],
            })

    # ------------------------------------------------------------ physics
    # ------------------------------------------------------------ physics
    def _verb_move(self, agent, action):
        dest = action.get("to", "")
        dest = self._resolve_location(agent, dest)
        if not dest:
            return False, f"tried to go somewhere that doesn't exist ({action.get('to')!r}); stayed put"
        if dest == agent.location:
            return True, f"stayed at {dest}"
        self.emit("leave", agent.name, dest, agent.location)
        agent.location = dest
        agent.activity = None
        agent.soapbox = 0
        self.emit("arrive", agent.name, f"arrived at {dest}", dest)
        return True, f"went to {dest}"


    @staticmethod
    def _echoes_own_recent(agent, toks):
        """Paraphrase Act predicate: >50% Jaccard overlap with anything this
        villager has said or sent recently is a reworded broken record."""
        for old in agent.recent_own_says[-6:]:
            union = toks | old
            if union and len(toks & old) / len(union) > 0.5:
                return True
        return False

    def _verb_say(self, agent, action):
        text = _sanitize_speech((action.get("text") or "").strip())
        if not text:
            return False, "opened their mouth and nothing came out"
        norm = " ".join(text.lower().split())
        veto = self._reality_check(agent, norm)
        if veto:
            agent.activity = {"type": "idle", "until_tick": self.tick_no + 2,
                              "note": "opened their mouth, glanced at reality, closed it"}
            return False, veto
        if norm == agent.last_say:
            agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                              "note": "caught themselves about to repeat the same line and thought better of it"}
            self.emit("action", agent.name,
                      "started to say the same thing again, trailed off",
                      agent.location, deliver=False)
            return False, ("already said exactly that — repeating it changes nothing; "
                           "do something different")
        # Echo Ban: parroting a line anyone nearby said recently is not talking
        for (t, loc, n, who) in self.recent_says:
            if n == norm and loc == agent.location and \
                    self.tick_no - t <= 8 and who != agent.name:
                agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                                  "note": "almost repeated someone else's words verbatim, stopped"}
                return False, (f"that's word-for-word what {who} just said — "
                               "parroting isn't a contribution; say something of "
                               "your OWN or act")
        # Paraphrase Act: rewording your own broken record is still a broken record
        toks = _toks(norm)
        if self._echoes_own_recent(agent, toks):
            agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                              "note": "realized they've been saying the same thing all day"}
            return False, ("has been saying versions of that same thing over and "
                           "over — everyone has stopped listening. Do something "
                           "NEW: go somewhere else, text someone, take an action")
        # Soapbox Law: three speeches to an empty room is a cry for help
        audience = self.occupants(agent.location, exclude=agent.name)
        if not audience and not action.get("to"):
            agent.soapbox += 1
            if agent.soapbox >= 3:
                agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                                  "note": "looked around the empty room and let the speech die"}
                return False, ("there is NOBODY here — this is the third speech to an "
                               "empty room. Talking to furniture accomplishes nothing; "
                               "GO where the people are, or text someone")
        else:
            agent.soapbox = 0
        agent.recent_own_says.append(toks)
        if len(agent.recent_own_says) > 6:
            agent.recent_own_says = agent.recent_own_says[-6:]
        agent.last_say = norm
        self.recent_says.append((self.tick_no, agent.location, norm, agent.name))
        if len(self.recent_says) > 24:
            self.recent_says = self.recent_says[-16:]
        target = action.get("to")
        if target:
            target = self._resolve_agent(target)
            if not target or self.agents[target].location != agent.location:
                # talking to someone who isn't here — the town notices
                self.emit("say", agent.name, text, agent.location, target=None)
                return True, f"said (to no one in particular): {text}"
        self.emit("say", agent.name, text, agent.location, target=target)
        if target:
            agent.relationships[target] = agent.relationships.get(target, 0) + 1
            self._detect_promise(agent, target, norm, text)
        return True, f"said: {text}"


    def _verb_text(self, agent, action):
        text = _sanitize_speech((action.get("text") or "").strip())
        if not text:
            return False, "stared at their phone, typed nothing"
        # SMS repetition laws (same code as speech: no reruns, no paraphrase spam)
        norm = " ".join(text.lower().split())
        veto = self._reality_check(agent, norm)
        if veto:
            return False, veto
        if norm == agent.last_text:
            return False, "already sent exactly that text — sending it again helps no one"
        toks = _toks(norm)
        if self._echoes_own_recent(agent, toks):
            return False, ("that text says the same thing they've been "
                           "saying/sending all day — say something NEW or act")
        to_raw = str(action.get("to") or "").strip().lower()
        gname = config.TOWN_NAME.lower()
        if to_raw in ("everyone", "all", "town", "the town", "group", "group chat",
                      f"{gname}_gossip", f"{gname} gossip", "the group"):
            # No posting AT people who are standing next to you
            others_awake = [a for a in self.agents.values()
                            if a.name != agent.name and not a.asleep]
            if others_awake and all(a.location == agent.location for a in others_awake):
                return False, ("looked up from their phone — every single person "
                               "in town is standing in this room. Posting to the "
                               "group chat would be absurd; just SPEAK")
            # Pepperton_Gossip: the town group chat (built by popular demand)
            agent.last_text = norm
            agent.recent_own_says.append(toks)
            self.emit("gossip", agent.name, text, agent.location, deliver=False)
            for other in self.agents.values():
                if other.name == agent.name:
                    continue
                other.pending.append({
                    "text": (f'Your phone buzzes — {config.TOWN_NAME}_Gossip group chat, '
                             f'{agent.name} posted: "{text}" (the whole town saw '
                             f'this; reply to the group by texting "everyone", or '
                             f'text someone privately)'),
                    "interrupt": True,
                    "sim_time": self.clock.hhmm,
                })
            return True, f"posted to {config.TOWN_NAME}_Gossip: {text}"
        target = self._resolve_agent(action.get("to"))
        if not target:
            contacts = ", ".join(n for n in self.agents if n != agent.name)
            return False, (f"has no contact named {action.get('to')!r} — "
                           f"the only contacts in this phone are: {contacts}")
        if target == agent.name:
            return False, "almost texted themselves"
        recipient = self.agents[target]
        if recipient.location == agent.location and not recipient.asleep:
            return False, (f"{target} is standing RIGHT THERE in the same room — "
                           "texting them would be ridiculous; just talk to them")
        self.emit("text", agent.name, text, agent.location, target=target,
                  deliver=False)
        agent.last_text = norm
        agent.recent_own_says.append(toks)
        recipient.pending.append({
            "text": (f'Your phone buzzes — text from {agent.name}: "{text}" '
                     f'({agent.name} is NOT here with you — speaking out loud '
                     f'will not reach them; reply with the "text" action)'),
            "interrupt": True,
            "sim_time": self.clock.hhmm,
        })
        self._detect_promise(agent, target, norm, text)
        return True, f"texted {target}: {text}"

    def _verb_interact(self, agent, action):
        """Open a bounded, co-located social scene through world physics."""
        speech_act = str(action.get("act") or action.get("speech_act") or
                         "request").strip().lower()
        if speech_act in {"accept", "refuse", "defer"}:
            scene = self.interactions.get(str(action.get("scene_id") or ""))
            if scene is None or scene.status != "open":
                return False, "there is no open interaction with that id"
            if agent.name != scene.next_responder or agent.name != scene.target:
                return False, "it is not this person's turn in the interaction"
            initiator = self.agents.get(scene.initiator)
            if initiator is None or initiator.asleep or agent.asleep or \
                    initiator.location != scene.location or agent.location != scene.location:
                return False, "both participants must remain awake at the scene location"
            commitment = None
            if speech_act == "accept":
                commitment = action.get("commitment") or scene.proposal.get("commitment")
                if commitment is not None and (not isinstance(commitment, dict) or
                                               not str(commitment.get("condition") or "").strip()):
                    return False, "an accepted commitment needs a condition"
            scene.status = {"accept": "accepted", "refuse": "refused", "defer": "deferred"}[speech_act]
            scene.response_act = speech_act
            scene.next_responder = None
            scene.updated_tick = self.tick_no
            if speech_act == "accept":
                if commitment is not None:
                    self._social_seq += 1
                    commitment_id = f"{self.world_id}:commitment:{self._social_seq}"
                    self.commitments[commitment_id] = Commitment(
                        id=commitment_id, owner=agent.name, counterparty=scene.initiator,
                        condition=str(commitment["condition"]).strip(),
                        deadline_day=commitment.get("deadline_day"),
                        proof=dict(commitment.get("proof") or {}),
                        source_interaction_id=scene.id)
                    scene.commitment_id = commitment_id
            past = {"accept": "accepted", "refuse": "refused", "defer": "deferred"}[speech_act]
            event = self.emit("interaction", agent.name,
                f"{past} {scene.initiator}'s {scene.topic} interaction",
                scene.location, target=scene.initiator, topic=scene.topic,
                thread_id=scene.thread_id)
            scene.last_event_id = event["event_id"]
            if scene.commitment_id:
                self.commitments[scene.commitment_id].source_event_id = event["event_id"]
            return True, f"{scene.status} the {scene.topic} interaction"
        if speech_act == "counter":
            scene = self.interactions.get(str(action.get("scene_id") or ""))
            if scene is None or scene.status != "open":
                return False, "there is no open interaction with that id"
            if agent.name != scene.next_responder:
                return False, "it is not this person's turn in the interaction"
            proposal = action.get("proposal")
            if not isinstance(proposal, dict) or not proposal:
                return False, "a counter needs a non-empty proposal"
            scene.proposal = dict(proposal)
            scene.round += 1
            scene.response_act = "counter"
            scene.updated_tick = self.tick_no
            scene.next_responder = scene.initiator if agent.name == scene.target else scene.target
            if scene.round >= MAX_INTERACTION_ROUNDS:
                scene.status, scene.next_responder = "closed", None
            event = self.emit("interaction", agent.name,
                f"countered {scene.initiator}'s {scene.topic} interaction",
                scene.location, target=scene.initiator, topic=scene.topic,
                thread_id=scene.thread_id)
            scene.last_event_id = event["event_id"]
            return True, ("closed the interaction after too many counters"
                          if scene.status == "closed" else "countered the interaction")
        if speech_act not in SPEECH_ACTS - {"accept", "refuse", "counter", "defer", "leave"}:
            return False, f"unsupported opening speech act {speech_act!r}"
        target_name = self._resolve_agent(action.get("to") or action.get("target"))
        target = self.agents.get(target_name)
        if target is None or target.name == agent.name:
            return False, "an interaction needs another named villager"
        if target.asleep or target.location != agent.location:
            return False, f"{target.name} is not available here for an interaction"
        proposal = action.get("proposal") or {}
        if not isinstance(proposal, dict):
            return False, "proposal must be an object"
        self._social_seq += 1
        scene_id = f"{self.world_id}:scene:{self._social_seq}"
        topic = str(action.get("topic") or "general").strip() or "general"
        interaction = Interaction(
            id=scene_id, initiator=agent.name, target=target.name,
            location=agent.location, topic=topic, speech_act=speech_act,
            proposal=dict(proposal), thread_id=action.get("thread_id"),
            created_tick=self.tick_no, updated_tick=self.tick_no)
        self.interactions[scene_id] = interaction
        event = self.emit(
            "interaction", agent.name,
            f"opened a {speech_act} interaction with {target.name} about {topic}",
            agent.location, target=target.name, topic=topic,
            thread_id=interaction.thread_id)
        interaction.source_event_id = event["event_id"]
        interaction.last_event_id = event["event_id"]
        return True, f"opened {speech_act} interaction {scene_id}"

    def reconcile_commitments(self):
        """Resolve durable commitments from completed projects or deadlines."""
        changed = False
        for commitment in self.commitments.values():
            if commitment.status != "open":
                continue
            proof = commitment.proof or {}
            project = next((item for item in self.projects
                            if item.get("name") == proof.get("project")), None)
            if proof.get("kind") == "project_complete" and project and project.get("complete"):
                commitment.status = "fulfilled"
                text = f"{commitment.owner} fulfilled their commitment to {commitment.counterparty}"
            elif commitment.deadline_day is not None and self.clock.day > commitment.deadline_day:
                commitment.status = "broken"
                text = f"{commitment.owner} broke their commitment to {commitment.counterparty}"
            else:
                continue
            owner, other = self.agents.get(commitment.owner), self.agents.get(commitment.counterparty)
            if owner and other:
                delta = 2 if commitment.status == "fulfilled" else -3
                owner.relationships[other.name] = owner.relationships.get(other.name, 0) + delta
                other.relationships[owner.name] = other.relationships.get(owner.name, 0) + delta
                self.emit("commitment", owner.name, text, owner.location, target=other.name,
                          deliver=False, topic="commitment")
            changed = True
        return changed


    def _verb_eat(self, agent, action):
        if agent.needs["fullness"] >= 85:
            return False, "couldn't eat another bite — completely full"
        loc = self.locations.get(agent.location, {})
        if loc.get("sells_food"):
            if agent.money < config.MEAL_COST:
                # the poor box: charity mediated through commerce — the jar
                # pays the diner, the diner feeds the villager, the town sees
                box = getattr(config, "POOR_BOX", "the poor box")
                if getattr(config, "POOR_BOX_ENABLED", True) and \
                        self.tills.get(box, 0.0) >= config.MEAL_COST:
                    self.tills[box] = round(
                        self.tills[box] - config.MEAL_COST, 2)
                    self._till_deposit(agent.location, config.MEAL_COST)
                    agent.needs["fullness"] = min(
                        100, agent.needs["fullness"] + config.MEAL_FULLNESS)
                    agent.activity = {"type": "idle",
                                      "until_tick": self.tick_no + 2,
                                      "note": "digesting"}
                    self.emit("action", agent.name,
                              "had a meal on the poor box — the jar on the "
                              "counter is lighter", agent.location)
                    return True, (f"ate at {agent.location} on the poor box "
                                  f"(jar now ${self.tills.get(box, 0):.0f})")
                hungry_note = (" — visibly hungry" if
                               agent.needs["fullness"] <= config.NEEDS["fullness"]["urgent_below"]
                               else "")
                self.emit("action", agent.name,
                          f"counted their money and quietly put the menu "
                          f"down{hungry_note}", agent.location)
                home_hint = (f"; the pantry at {agent.home} is FREE"
                             if agent.home and agent.pantry > 0 else "")
                return False, (f"can't afford a meal here (${agent.money:.0f}, "
                               f"meals cost ${config.MEAL_COST}). Working a "
                               f"shift PAYS{home_hint}")
            agent.money -= config.MEAL_COST
            self._till_deposit(agent.location, config.MEAL_COST)
            agent.needs["fullness"] = min(100, agent.needs["fullness"] + config.MEAL_FULLNESS)
            agent.activity = {"type": "idle", "until_tick": self.tick_no + 2,
                              "note": "digesting"}
            self.emit("action", agent.name, f"had a meal at {agent.location}", agent.location)
            return True, f"ate at {agent.location} (-${config.MEAL_COST})"
        if agent.location == agent.home:
            if agent.pantry <= 0:
                return False, ("found the cupboard bare — the pantry restocks "
                               "overnight; a real meal means going into town")
            agent.pantry -= 1
            agent.needs["fullness"] = min(100, agent.needs["fullness"] + 30)
            agent.activity = {"type": "idle", "until_tick": self.tick_no + 2,
                              "note": "digesting"}
            self.emit("action", agent.name, "fixed something to eat at home", agent.location)
            return True, f"ate at home ({agent.pantry} servings left today)"
        return False, f"there's no food at {agent.location}"


    def _verb_build(self, agent, action):
        matches = self.match_projects(action.get("project"))
        if len(matches) > 1:
            names = " / ".join(p["name"] for p in matches)
            return False, (f"'{action.get('project')}' is ambiguous — be "
                           f"specific: {names}")
        proj = matches[0] if matches else None
        if not proj:
            boards = ", ".join(p["name"] for p in self.projects if not p["complete"])
            return False, (f"no such project — the notice board lists: {boards or 'nothing (all done!)'}")
        if agent.location != proj["site"]:
            return False, (f"{proj['name']} is being built at {proj['site']} — "
                           "you have to actually BE there to swing a hammer")
        agent.activity = {"type": "build", "project": proj["name"],
                          "until_tick": self.tick_no + 8}
        tooled = "carpenter's tools" in agent.possessions
        if tooled:
            proj["done"] += 1
            proj["contributors"][agent.name] = \
                proj["contributors"].get(agent.name, 0) + 1
        self.emit("action", agent.name,
                  f"rolled up their sleeves and got to work on {proj['name']}"
                  + (" — own tools, first swing counts double" if tooled
                     else ""),
                  agent.location)
        return True, f"working on {proj['name']} ({proj['done']}/{proj['work']})"


    def _verb_propose(self, agent, action):
        name = str(action.get("project") or "").strip()
        if not name:
            return False, "started to propose something, lost the thread"
        # shelved work still stands in the world but frees the board
        open_count = sum(1 for p in self.projects
                         if not p["complete"] and not p.get("stalled"))
        if open_count >= 3:
            return False, ("the notice board is full — three open projects "
                           "already; build one first")
        def _core(n):
            w0 = n.lower().split()
            while w0 and w0[0] in ("a", "an", "the"):
                w0 = w0[1:]
            return " ".join(w0)
        nl = _core(name)
        for p in self.projects:
            pc = _core(p["name"])
            if nl and pc and (nl in pc or pc in nl):
                return False, f"something like that is already on the board ({p['name']})"
        site = self._resolve_location(agent, action.get("site"))
        if not site or self.locations.get(site, {}).get("home_of"):
            return False, ("a project needs a real public site: " +
                           ", ".join(self.public_locations()))
        try:
            work = int(action.get("work", 30))
        except (TypeError, ValueError):
            work = 30
        work = max(10, min(80, work))
        # an inn-shaped name builds SHELTER INFRASTRUCTURE (beds for the
        # homeless, nightly rate to the town fund); a house-shaped name
        # builds one villager a home. Inn check first: "boarding house"
        # is an inn, not a house.
        name_words = set(nl.split())
        inn = "inn" in name_words or any(
            k in nl for k in getattr(config, "INN_KEYWORDS",
                                     ("motel", "hotel", "boarding house",
                                      "boardinghouse", "hostel", "lodge")))
        housing = (not inn) and any(k in nl for k in
                      ("house", "cottage", "cabin", "home", "room", "shack",
                       "apartment", "bunkhouse", "quarters"))
        self.projects.append({
            "name": name, "site": site, "work": work, "done": 0,
            "complete": False, "contributors": {},
            "proposed_by": agent.name, "housing": housing, "inn": inn,
            "icon": "🏨" if inn else ("🏠" if housing else "🏗️"),
            "desc": f"{name} — proposed by {agent.name.split()[0]}",
            "adds": f"{name} stands here, built by the townsfolk",
            "permit_due": (self.clock.day + self.permit_window(work)
                           if getattr(config, "PERMITS_ENABLED", True)
                           else None),
        })
        permit_bit = ""
        if getattr(config, "PERMITS_ENABLED", True):
            permit_bit = (f" Permit runs to day "
                          f"{self.clock.day + self.permit_window(work)} — "
                          f"unfinished projects get fined, then condemned.")
        self.emit("world", None,
                  f"NEW on the notice board: {name} at {site} "
                  f"(~{work} shifts) — proposed by {agent.name}."
                  f"{permit_bit}",
                  agent.location)
        for a in self.agents.values():
            if a.name != agent.name:
                a.pending.append({
                    "text": (f"The notice board has a new project: {name} at "
                             f"{site} (~{work} shifts), proposed by "
                             f"{agent.name}. Worth your hammer, or not?"),
                    "interrupt": False,
                    "sim_time": self.clock.hhmm,
                })
        return True, f"proposed {name} at {site} ({work} shifts)"


    def _verb_treat(self, agent, action):
        loc = self.locations.get(agent.location, {})
        if not loc.get("sells_food"):
            return False, f"can't buy anyone a meal at {agent.location} — no food sold here"
        hungry = [a for a in self.occupants(agent.location, exclude=agent.name)
                  if a.needs["fullness"] < 85]
        who = action.get("to")
        if who:
            t = self._resolve_agent(who)
            hungry = [a for a in hungry if a.name == t]
        if not hungry:
            return False, "offered to buy a round of food, but nobody here is hungry"
        cost = config.MEAL_COST * len(hungry)
        if agent.money < cost:
            return False, (f"wanted to treat {len(hungry)} people (${cost}) but "
                           f"only has ${agent.money:.0f}")
        agent.money -= cost
        self._till_deposit(agent.location, cost)
        names = []
        for guest in hungry:
            guest.needs["fullness"] = min(100, guest.needs["fullness"] + config.MEAL_FULLNESS)
            names.append(guest.name.split()[0])
            guest.pending.append({
                "text": (f"{agent.name} just bought you a meal at "
                         f"{agent.location}. That was genuinely kind."),
                "interrupt": False,
                "sim_time": self.clock.hhmm,
            })
            agent.relationships[guest.name] = agent.relationships.get(guest.name, 0) + 2
            guest.relationships[agent.name] = guest.relationships.get(agent.name, 0) + 2
        self.emit("action", agent.name,
                  f"bought a meal for {', '.join(names)} (-${cost})",
                  agent.location)
        return True, f"treated {', '.join(names)} to a meal (-${cost})"


    def _verb_drink(self, agent, action):
        loc = self.locations.get(agent.location, {})
        if not loc.get("bar"):
            return False, f"there's no bar at {agent.location} — the Rusty Tap is the place for that"
        if agent.money < config.DRINK_COST:
            self.emit("action", agent.name,
                      "checked their pockets and ordered a water", agent.location)
            return False, f"couldn't cover a drink (${agent.money:.0f})"
        agent.money -= config.DRINK_COST
        self._till_deposit(agent.location, config.DRINK_COST)
        agent.drink_ticks = [t for t in agent.drink_ticks
                             if self.tick_no - t <= config.TIPSY_TICKS * 2]
        agent.drink_ticks.append(self.tick_no)
        n = len(agent.drink_ticks)
        if n >= config.DRUNK_AT:
            self.emit("action", agent.name,
                      f"ordered round {n} — the room is starting to tilt",
                      agent.location)
        else:
            self.emit("action", agent.name, "ordered a drink at the bar",
                      agent.location)
        return True, f"had a drink (-${config.DRINK_COST}, round {n})"


    def _verb_pay(self, agent, action):
        if not getattr(config, "ECONOMY", False):
            return False, "the town has no ledger yet"
        try:
            amount = round(float(action.get("amount", 0)), 2)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return False, "offered to pay nothing, which is not paying"
        if amount > agent.money:
            return False, (f"tried to pay ${amount:.0f} but only has "
                           f"${agent.money:.0f}")
        to_raw = str(action.get("to") or "").strip().lower()
        # the poor box takes donations from anyone, owed or not — and
        # giving is PUBLIC (this town competes at legible virtue)
        if getattr(config, "POOR_BOX_ENABLED", True) and \
                any(w in to_raw for w in ("box", "charity", "donat", "poor")):
            box = getattr(config, "POOR_BOX", "the poor box")
            if box not in self.tills:
                return False, "there's no poor box in this town"
            agent.money -= amount
            self.ledger.deposit(box, amount)
            self.emit("action", agent.name,
                      f"dropped ${amount:.0f} in the poor box on the "
                      f"diner counter", agent.location)
            return True, (f"gave ${amount:.0f} to the poor box "
                          f"(the jar holds ${self.tills.get(box, 0):.0f})")
        bank = self.bank_name()
        inst = None
        if "bank" in to_raw:
            inst = bank
        elif "fund" in to_raw or "town" in to_raw or "rent" in to_raw:
            inst = config.TOWN_FUND
        if inst:
            owed = self.open_debts(debtor=agent.name, creditor=inst)
            if not owed:
                return False, f"owes {inst} nothing right now"
            agent.money -= amount
            leftover = self._apply_to_debts(agent.name, inst, amount)
            paid = round(amount - leftover, 2)
            agent.money += leftover   # institutions don't take tips
            till_key = inst if inst in self.tills else config.TOWN_FUND
            self.tills[till_key] = round(
                self.tills.get(till_key, 0.0) + paid, 2)
            cleared = not self.open_debts(debtor=agent.name, creditor=inst)
            self.emit("action", agent.name,
                      f"paid ${paid:.0f} to {inst}"
                      + (" — debt CLEARED" if cleared else ""),
                      agent.location)
            if inst == bank:
                # the Holt Act's mercy clause: paid in full before the
                # sale, the door comes back
                self.ledger.check_redemption(agent)
            return True, (f"paid {inst} ${paid:.0f}"
                          + (" (all square)" if cleared
                             else f" (still owes "
                                  f"${sum(d['amount'] for d in self.open_debts(debtor=agent.name, creditor=inst)):.0f})"))
        target = self._resolve_agent(action.get("to"))
        if not target or target == agent.name:
            return False, (f"can't pay {action.get('to')!r} — pay a person "
                           f"here with you, or 'the bank' / 'the town fund'")
        recipient = self.agents[target]
        if recipient.location != agent.location:
            return False, (f"{target} isn't here — cash changes hands in "
                           f"person")
        agent.money -= amount
        recipient.money += amount
        leftover = self._apply_to_debts(agent.name, target, amount)
        settled = round(amount - leftover, 2)
        self._settle_promises_on_payment(agent, target)
        if settled > 0:
            agent.relationships[target] = \
                agent.relationships.get(target, 0) + 1
            recipient.relationships[agent.name] = \
                recipient.relationships.get(agent.name, 0) + 2
            note = (f"paid {target} ${amount:.0f}"
                    + ("" if leftover <= 0
                       else f" (${leftover:.0f} over the debt — call it interest)"))
        else:
            recipient.relationships[agent.name] = \
                recipient.relationships.get(agent.name, 0) + 2
            note = f"pressed ${amount:.0f} into {target}'s hand"
        recipient.pending.append({
            "text": f"{agent.name} just handed you ${amount:.0f} in cash."
                    + (" That squares what they owed you."
                       if settled > 0 and not self.open_debts(debtor=agent.name, creditor=target)
                       else ""),
            "interrupt": False, "sim_time": self.clock.hhmm,
        })
        self.emit("action", agent.name, note, agent.location)
        return True, note


    def _verb_borrow(self, agent, action):
        if not getattr(config, "ECONOMY", False):
            return False, "there is no bank in this town yet"
        bank = self.bank_name()
        if not bank:
            return False, "there is no bank in this town"
        if agent.location != bank:
            return False, f"loans are made at the teller window — that's {bank}"
        try:
            amount = round(float(action.get("amount", 0)), 2)
        except (TypeError, ValueError):
            amount = 0
        if amount <= 0:
            return False, "asked the teller for a loan of nothing"
        tier, limit, shifts = self.credit_report(agent)
        if tier == "bad":
            return False, ("the teller pulled the ledger and found overdue "
                           "debts. No new credit until the old ink dries — "
                           "settle up first (the pay action)")
        if tier == "extended":
            return False, ("already carrying a bank loan — this bank does "
                           "one at a time")
        if amount > limit:
            if limit == config.LOAN_MAX_SHAKY:
                return False, (f"the teller glanced at the notice board: "
                               f"{shifts} build shifts on public record. A "
                               f"thin file — the bank will go to ${limit}, "
                               f"not ${amount:.0f}. Build something and "
                               f"come back")
            return False, f"the bank's ceiling is ${limit}, even for solid credit"
        if self.tills.get(bank, 0.0) < amount:
            return False, ("the bank itself is short on cash — come back "
                           "when deposits recover")
        self.tills[bank] = round(self.tills[bank] - amount, 2)
        agent.money += amount
        repay = round(amount * (1 + config.LOAN_INTEREST), 2)
        due = self.clock.day + config.LOAN_TERM_DAYS
        self.add_debt(agent.name, bank, repay,
                      f"bank loan (${amount:.0f} plus interest)",
                      due_day=due)
        self.emit("action", agent.name,
                  f"walked out of {bank} with a ${amount:.0f} loan — "
                  f"${repay:.0f} due by day {due}", agent.location)
        return True, (f"borrowed ${amount:.0f} (credit: {tier}; owes "
                      f"${repay:.0f} by day {due})")


    def open_positions(self):
        """Situations vacant: every job in the town's book that nobody
        holds. A villager without work claims one by SHOWING UP — use the
        work action at the place, and the job is theirs. (v2.7: passed
        alongside the Holt Act — a law with teeth owes the toothless a
        way to earn.)

        v3.11: the book is no longer only ours. A finished project adds its
        own post to `extra_workplaces`, so a town that builds something can
        be employed at it. And the filter that used to live at the call site
        — only the JOBLESS may claim — is gone: taking a post releases the
        one you hold. See _verb_work."""
        held = {a.job for a in self.agents.values()}
        book = dict(getattr(config, "WORKPLACES", {}))
        book.update(getattr(self, "extra_workplaces", {}))
        out = {}
        for job, wp in book.items():
            if wp and wp in self.locations and job not in held:
                out[job] = wp
        return out

    def vacancy_digest(self, limit=5):
        """SITUATIONS VACANT, capped and rotated. (v3.11)

        A town that has finished forty things has forty posts open. Pepperton
        has finished forty. Listing all of them every evening is not
        information, it is a wall — MEMORY_TOP_K is 8 and no villager can
        hold thirty-eight of anything. So it names a handful and SAYS HOW
        MANY IT DID NOT NAME, which is the honest shape: the town is not
        told less than the truth, it is told the truth in a size it can
        carry.

        The window rotates by day, so a different handful surfaces each
        evening instead of the alphabetical first five forever. Deterministic
        — it consumes no randomness and cannot move the golden hash."""
        openings = sorted(self.open_positions().items())
        if not openings:
            return ""
        total = len(openings)
        if total <= limit:
            shown, rest = openings, 0
        else:
            offset = self.clock.day % total
            shown = (openings + openings)[offset:offset + limit]
            rest = total - limit
        text = "; ".join(f"{j} at {w}" for j, w in shown)
        if rest:
            text += f"; and {rest} more posts nobody holds"
        return text

    def make_workplace(self, proj):
        """A finished project becomes a place a person can be PAID to stand in.

        Returns the new location name, or None if this project is not that
        kind of thing (inns give beds, houses give homes) or it already has
        one. Called on completion and once at boot for anything finished
        before this law existed.

        THE TILL OPENS AT ZERO — never TILL_SEED. Seeding a built till would
        mint money from nothing and break the only invariant this project has
        never violated. It opens dry, which the Tibbs Door already permits,
        so building something is a bet and not a bonus. It fills from the bus
        or it does not fill.

        No `sells_food`, no `bar`: _open_businesses gates on `in tills`, so a
        till is enough to take a visitor's money. A greenhouse is not a diner.
        """
        if proj.get("inn") or proj.get("housing") or not proj.get("complete"):
            return None
        name = str(proj.get("name") or "").strip()
        if not name:
            return None
        job = f"keeper of {name}"
        if job in getattr(self, "extra_workplaces", {}):
            return self.extra_workplaces[job]
        place = name if name not in self.locations else f"{name} (the building)"
        firsts = " and ".join(n.split()[0] for n in proj.get("contributors", {})) \
            or "the townsfolk"
        self.locations[place] = {
            "desc": f"{proj.get('adds') or name} — built by {firsts}",
            "built": True,
        }
        if getattr(config, "ECONOMY", False):
            self.tills.setdefault(place, 0.0)   # DRY. never seeded.
        self.extra_workplaces[job] = place
        return place

    def _verb_work(self, agent, action):
        # An odd job takes priority over everything: a townsperson is
        # standing right here with cash in hand for an hour's work. It is
        # the only money in this world that reaches a villager without a
        # shift, a till, or a wage — and it is still EARNED. (v3.0)
        folk = getattr(self, "folk", None)
        if folk is not None and folk.offer_at(agent.location):
            ok, note = folk.take_offer(agent, agent.location)
            if ok:
                # NOT worked_today. An odd job is real, earned work and it
                # counts as effort — but it is not a shift at your own
                # workplace, and the attendance ledger reads worked_today as
                # its only signal for "did this employed villager open their
                # door". Writing here let a shopkeeper take a $5 job standing
                # inside his own dark shop and be posted as ON SHIFT with his
                # Crane Bonus streak intact. (Found by review, v3.3.1.)
                #
                # Nothing stops them doing both: the odd job spends this
                # work action, their own shift is still there to take.
                self.odd_jobs_today.add(agent.name)
                return True, note
        wp = agent.workplace()
        if getattr(config, "HIRING_ENABLED", True):
            openings = self.open_positions()
            here = next((j for j, w in sorted(openings.items())
                         if w == agent.location), None)
            # v3.11: TAKING A JOB RELEASES THE ONE YOU HOLD. The old gate was
            # `if not wp` — only the jobless could ever be hired, and there is
            # no quit verb, so a villager cast as a librarian before she had a
            # thought was a librarian forever, at a building with no till. The
            # post she leaves goes back on the board the same tick, for anyone.
            # Nobody is assigned and nobody is nudged: showing up IS the
            # interview, exactly as it always was for the jobless.
            if here and here != agent.job:
                left = agent.job if wp else None
                agent.job = here
                agent.workplace_at = openings[here]
                wp = agent.workplace()
                if left:
                    self.emit("action", agent.name,
                              f"walked out of being the town {left} and was "
                              f"taken on as {here} at {wp} — the {left}'s post "
                              f"is open again, for anyone who shows up",
                              wp)
                else:
                    self.emit("action", agent.name,
                              f"was taken on as the town {here} at {wp} — "
                              f"showed up, got the job, first shift starts now",
                              wp)
            elif not wp and openings:
                sits = "; ".join(f"{j} at {w}"
                                 for j, w in sorted(openings.items()))
                return False, (f"is {agent.job} and has no shift to work — "
                               f"but the town is HIRING: {sits}. Go there "
                               f"and work; showing up IS the interview")
        if not wp:
            return False, f"is {agent.job} and has no shift to work"
        if agent.location != wp:
            return False, f"can't work here — their work is at {wp}"
        if getattr(config, "ECONOMY", False):
            till_key = self._wage_till_key(agent)
            # "Can't pay" means can't cover even one tick of wage — not
            # "holds exactly zero". A fund settling its own back wages gets
            # the income tax on that payment handed straight back to it, so
            # it can never actually reach 0.00 and the old `<= 0` proxy read
            # the residue as solvency. (Found by review, v2.9.2.)
            dry = self.insolvent(till_key)
            # THE TIBBS DOOR (v2.9.1). Vera Tibbs of Pompeii showed up for
            # her shift at the Rusty Tap three times in one day — the third
            # time with five paying tourists standing in the plaza — and the
            # payroll rule turned her away at her own door every time. It
            # didn't just refuse to pay her; it refused to let her OPEN, so
            # the till could never fill, so tomorrow she'd be refused again.
            # A dry till is a reason to work unpaid and hope for customers.
            # It is not a reason to lock the door. Showing up always opens
            # the door now; only the wage is in doubt.
            # Public payroll is the one exception: there is no door at the
            # park for a customer to walk through, so a broke town really
            # can't take you on. A SHOP can always open and hope.
            if dry and till_key == config.TOWN_FUND:
                where = ("the town" if till_key == config.TOWN_FUND
                         else till_key)
                agent.activity = {"type": "idle",
                                  "until_tick": self.tick_no + 8,
                                  "note": "sent home — no shifts today"}
                # THE TIBBS DOOR, second half (v2.9.2). Being refused is not
                # the same as not turning up, and the ledger must not confuse
                # them. A public worker sent home for a broke town fund used
                # to be counted an ABSENTEE — publicly named under "DOORS
                # NEVER OPENED" and stripped of the wage raise he'd earned by
                # showing up every day. Punished for arriving. He is recorded
                # here as present-and-refused: no absence streak, no loyalty
                # reset, and the town hears why the door stayed shut.
                self.turned_away_today.add(agent.name)
                self.emit("action", agent.name,
                          f"showed up for a shift and was sent home — "
                          f"{where} can't make payroll", agent.location)
                return False, (f"no shifts today: {where}'s books are "
                               f"${self.wage_debt_of(till_key):.0f} in the "
                               f"red and the till is empty. No revenue, no "
                               f"wages — a business needs CUSTOMERS "
                               f"(or you need another way to earn: build "
                               f"goodwill, or see the bank)")
        agent.activity = {"type": "work", "until_tick": self.tick_no + 16,
                          "note": action.get("note", "")}
        self.worked_today.add(agent.name)
        if getattr(config, "ECONOMY", False) and \
                self.tills.get(self._wage_till_key(agent), 0.0) <= 0:
            self.emit("action", agent.name,
                      f"opened {wp} on an empty till — the doors are open "
                      f"and the register is dry. Somebody has to be here "
                      f"when a customer walks in.", wp)
            return True, (f"working at {wp} on an empty register — no wage "
                          f"in hand until a customer spends here, but the "
                          f"DOORS ARE OPEN and anything the till takes "
                          f"settles what you're owed")
        self.emit("action", agent.name, f"started a shift at {wp}", wp)
        return True, f"working at {wp}"


    def _verb_rest(self, agent, action):
        if agent.needs["energy"] >= 85 and not self.clock.is_night:
            return False, ("is wide awake — lying down now would be pointless. "
                           "The day is out there, and so is everyone else")
        if agent.home is None and self.locations.get(agent.location, {}).get("inn"):
            cost = getattr(config, "INN_ROOM_COST", 5)
            if agent.money < cost:
                return False, (f"a bed here costs ${cost} a night and "
                               f"they've got ${agent.money:.0f} — the park "
                               f"bench is free")
            agent.money -= cost
            self.ledger.deposit(getattr(config, "TOWN_FUND", "the town fund"),
                                cost)
            agent.asleep = True
            agent.activity = {"type": "rest", "until_tick": None, "note": "inn"}
            self.emit("action", agent.name,
                      f"took a bed at {agent.location} for the night",
                      agent.location)
            return True, f"slept at {agent.location} (-${cost}, to the town fund)"
        if agent.home is None and self.locations.get(agent.location, {}).get("bar"):
            if agent.money < config.ROOM_COST:
                return False, (f"a room above the bar costs ${config.ROOM_COST} "
                               f"a night and they've got ${agent.money:.0f} — "
                               "the park bench is free")
            agent.money -= config.ROOM_COST
            self._till_deposit(agent.location, config.ROOM_COST)
            agent.asleep = True
            agent.activity = {"type": "rest", "until_tick": None, "note": "room"}
            self.emit("action", agent.name,
                      "paid for the room above the bar and turned in",
                      agent.location)
            return True, f"took the room above the Tap (-${config.ROOM_COST})"
        if agent.location == agent.home:
            agent.asleep = True
            agent.activity = {"type": "rest", "until_tick": None, "note": ""}
            self.emit("action", agent.name, "turned in to rest", agent.location)
            return True, "resting at home"
        if agent.location == "the park":
            agent.activity = {"type": "nap", "until_tick": self.tick_no + 8, "note": ""}
            self.emit("action", agent.name, "dozed off on a park bench", agent.location)
            return True, "napping in the park"
        return False, f"can't properly rest at {agent.location} — home or the park"


    def _verb_idle(self, agent, action):
        """think / anything unrecognized: pass the time, visibly.

        COUNT WHICH ROAD GOT HERE (v3.9.4).

        `execute()` dispatches on `_VERB_HANDLERS.get(act, _verb_idle)` — a
        bare dict lookup with a default. So this handler is reached two
        completely different ways and, until now, said the same sentence for
        both: a villager who CHOSE to rest, and a villager who named a verb
        this world does not have and was silently converted into resting.

        For 190 days nobody counted the second kind. `passed the time` reads
        like somebody sitting in the sun; it may be the sound of somebody
        hitting a wall, and in this log they are the same line.

        THIS COUNTS. IT DOES NOT FIX. No verb is added, no wording changes,
        nothing a villager can see is touched — the tally is operator-facing
        only, and deliberately NOT persisted into world state, so it cannot
        reach the golden hash. If the counts come back full of `open` and
        `serve`, that is an argument for a pre-registration, not a patch.
        (claude/THE-UNCOUNTED-VERBS.md)"""
        act = self._verb_key(action).strip()
        if act and act != "idle" and act not in self._VERB_HANDLERS:
            # Bounded, because a degenerate model can invent a new verb every
            # tick forever. NOT SILENTLY: anything dropped is counted, so the
            # tally can never read as complete when it is not.
            key = act if len(act) <= 48 else act[:47] + "\u2026"
            if key in self.unknown_verbs or len(self.unknown_verbs) < 200:
                self.unknown_verbs[key] = self.unknown_verbs.get(key, 0) + 1
                self.unknown_verb_actors.setdefault(key, set()).add(agent.name)
            else:
                self.unknown_verbs_overflow += 1
        note = (action.get("note") or action.get("text") or "passed the time").strip()
        agent.activity = {"type": "idle", "until_tick": self.tick_no + 4, "note": note}
        self.emit("action", agent.name, note, agent.location, deliver=False)
        return True, f"idled: {note}"

    # one verb, one handler — physics stays legible
    def _verb_travel(self, agent, action):
        """Board the coach. Valid only while one is standing in the plaza.

        Nothing tells a villager this exists except the verb list, and the
        verb list only carries it while the coach is really there. No hint,
        no pending message, no encouragement. If nobody ever boards, that is
        the answer. (v3.4)
        """
        from sim import road
        c = road.cfg()
        if not c["enabled"]:
            return False, "there is no road out of here"
        dest = c["destination"]
        bus = getattr(getattr(self, "engine", None), "bus", None)
        standing = bool(bus and getattr(bus, "visiting", 0) and not
                        getattr(bus, "day_done_departed", False))
        if not (dest and standing):
            return False, ("no coach is standing in the plaza — there is "
                           "nowhere to go from here right now")
        plaza = self._coach_stop()
        if agent.location != plaza:
            return False, (f"the coach leaves from {plaza} and they are at "
                           f"{agent.location}")
        # what travels: cash and memory. Not the house, not the job, not
        # the credit history, not the debts. (claude/WORLD2.md §3L)
        note = road.write_traveller(self, agent, dest)
        if not note:
            return False, "the coach could not take them"
        fare = round(float(agent.money), 2)
        self.outside_flow = round(self.outside_flow - fare, 2)
        job = agent.workplace()
        self.emit("world", None,
                  f"{agent.name} climbed onto the coach with everything they "
                  f"had in their pockets and did not look back. The board on "
                  f"its side reads {dest}.", plaza)
        if job:
            self.emit("world", None,
                      f"{job} has nobody behind the counter. "
                      f"{agent.job.upper()} IS AN UNHELD POSITION.", job)
        for other in self.agents.values():
            if other.name == agent.name:
                continue
            other.pending.append({
                "text": f"{agent.name} got on the coach and left {getattr(config, 'TOWN_NAME', 'town')}.",
                "interrupt": True, "sim_time": self.clock.hhmm,
            })
        self.departed = getattr(self, "departed", [])
        self.departed.append({"name": agent.name, "day": self.clock.day,
                              "to": dest, "money": fare})
        del self.agents[agent.name]
        return True, f"left for {dest} on the coach"

    def _coach_stop(self):
        for loc, meta in self.locations.items():
            if meta.get("plaza") or loc.lower().endswith("plaza"):
                return loc
        return next(iter(self.public_locations() or ["the plaza"]))

    _VERB_HANDLERS = {
        "move": _verb_move, "say": _verb_say, "text": _verb_text,
        "interact": _verb_interact,
        "eat": _verb_eat, "build": _verb_build, "propose": _verb_propose,
        "treat": _verb_treat, "drink": _verb_drink, "pay": _verb_pay,
        "borrow": _verb_borrow, "work": _verb_work, "rest": _verb_rest,
        "buy": _verb_buy, "travel": _verb_travel,
    }

    @staticmethod
    def _verb_key(action):
        """The dispatch key for an action, ALWAYS hashable. (v3.9.4)

        A MALFORMED VERB USED TO KILL THE TOWN. `_VERB_HANDLERS.get(act)`
        raises TypeError on an unhashable key, and nothing between here and
        `Engine.step()` catches it — so a villager emitting
        `{"action": {"build": "the pool"}}`, which is an ordinary way for a
        model to malform that JSON, took the whole town down with it.

        The design already says "anything unrecognized idles." A dict is
        unrecognized. It should idle. This makes the sentence true for every
        input rather than for the inputs we happened to imagine.

        Ships dark by definition: no valid action reaches this branch, and
        the golden hash does not move."""
        act = (action or {}).get("action", "idle")
        if isinstance(act, str):
            return act
        return f"<{type(act).__name__}>"

    def execute(self, agent, action):
        """Validate and apply a brain's chosen action. Returns (ok, summary).
        Every verb gets its own handler; anything unrecognized idles."""
        act = self._verb_key(action)
        agent.last_action = action
        handler = self._VERB_HANDLERS.get(act, World._verb_idle)
        return handler(self, agent, action)

    def _next_command_id(self):
        self._command_seq += 1
        return f"{self.world_id}:command:{self._command_seq}"

    def apply_command(self, command):
        """Apply a typed action command at the world's physics boundary."""
        if not isinstance(command, Command):
            raise TypeError("command must be a Command")
        if command.command_id is None:
            command.command_id = self._next_command_id()
        if command.kind != "action":
            return CommandResult(False,
                                 f"unknown command kind {command.kind!r}",
                                 command.command_id)
        if command.actor not in self.agents:
            return CommandResult(False,
                                 f"unknown actor {command.actor!r}",
                                 command.command_id)
        return self.apply_effect(
            command,
            lambda: self.execute(self.agents[command.actor], command.payload),
        )

    def apply_effect(self, command, effect):
        """Run a validated effect and associate emitted events with it."""
        if not isinstance(command, Command):
            raise TypeError("command must be a Command")
        if command.command_id is None:
            command.command_id = self._next_command_id()
        before = self._event_seq
        previous = self._active_command
        self._active_command = command
        try:
            outcome = effect()
            if (isinstance(outcome, tuple) and len(outcome) == 2 and
                    isinstance(outcome[0], bool)):
                accepted, summary = outcome
            else:
                accepted, summary = True, outcome
        finally:
            self._active_command = previous
        event_ids = [
            event["event_id"] for event in self.events
            if before < event.get("seq", 0) <= self._event_seq
            and event.get("command_id") == command.command_id
        ]
        return CommandResult(bool(accepted), summary, command.command_id,
                             event_ids)

    # ---------------------------------------------------------- resolvers
    def _resolve_location(self, agent, name):
        if not name:
            return None
        name = str(name).strip()
        if name.lower() in ("home", "my house", "house"):
            return agent.home
        for loc in self.locations:
            if loc.lower() == name.lower():
                return loc
        # lenient contains-match: models abbreviate ("diner", "the store")
        for loc in self.locations:
            if name.lower() in loc.lower() or loc.lower() in name.lower():
                return loc
        return None

    def _resolve_agent(self, name):
        if not name:
            return None
        name = str(name).strip()
        for n in self.agents:
            if n.lower() == name.lower():
                return n
        for n in self.agents:
            if name.lower() in n.lower():
                return n
        return None
