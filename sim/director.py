"""The Director — an invisible hand that keeps the show moving.

Rolls quiet dice every tick; every so often, chaos. Anonymous texts,
seeded rumors, leaked group messages, dead air, omens at the pond,
and — rarely — a stranger stepping off the bus.

The Director never speaks and never appears. The town only ever sees
the consequences.
"""

import random

import config
from sim import brains
from sim.agents import Agent
from sim.causality import Command


class Director:
    def __init__(self, engine, seed):
        self.engine = engine
        self.rng = random.Random(f"director:{seed}")
        self.strangers_added = 0
        self.last_command_result = None

    # ------------------------------------------------------------------
    def step(self):
        if not config.CHAOS["enabled"]:
            return
        ticks_per_day = (24 * 60) // self.engine.world.clock.step
        if self.rng.random() < config.CHAOS["events_per_day"] / ticks_per_day:
            self.trigger()

    def trigger(self, name=None, source=None):
        """Fire one chaos event and preserve the legacy summary return."""
        source = source or ("manual" if name is not None else "director")
        return self.trigger_command(name, source=source).summary

    def trigger_command(self, name=None, source=None):
        """Apply one Director event through the shared command boundary."""
        source = source or ("manual" if name is not None else "director")
        result = self.engine.dispatch(Command(
            kind="director.event", source=source, payload={"event": name},
        ))
        self.last_command_result = result
        return result

    def apply_command(self, command):
        """Choose and apply the Director effect inside its command envelope."""
        name = command.payload.get("event")
        event_weights = dict(config.CHAOS["weights"])
        if self.strangers_added >= config.CHAOS["max_strangers"]:
            event_weights.pop("stranger", None)
        if name is None:
            if not event_weights:
                return False, "no Director events available"
            names = list(event_weights)
            name = self.rng.choices(
                names, weights=[event_weights[n] for n in names])[0]
        command.payload["event"] = name
        command.topic = command.topic or name
        command.thread_id = command.thread_id or f"thread:{name}"
        if command.salience == "normal":
            command.salience = "high"
        fn = getattr(self, f"_ev_{name}", None)
        if fn is None:
            return False, f"unknown event {name!r}"
        exp = getattr(self.engine, "exp", None)
        if exp is not None:
            world = self.engine.world
            exp.note_director(name, command.source == "manual",
                              world.tick_no, world.clock.day)
        return fn()

    # ------------------------------------------------------------ helpers
    def _random_agent(self, awake=True, exclude=None):
        pool = [a for a in self.engine.world.agents.values()
                if a.name != exclude and (not awake or not a.asleep)]
        return self.rng.choice(pool) if pool else None

    def _buzz(self, agent, text):
        agent.pending.append({
            "text": text, "interrupt": True,
            "sim_time": self.engine.world.clock.hhmm,
        })

    # ------------------------------------------------------------- events
    def _ev_anonymous_text(self):
        world = self.engine.world
        target = self._random_agent()
        if not target:
            return "no one awake to text"
        other = self._random_agent(awake=False, exclude=target.name)
        msg = self.rng.choice(config.ANON_TEXTS).format(
            name=other.name.split()[0] if other else "someone")
        self._buzz(target, (f'Your phone buzzes — text from an UNKNOWN NUMBER: '
                            f'"{msg}" (there is no way to tell who sent this)'))
        world.emit("world", None,
               f"(somewhere, a phone buzzes with a message from an unknown number)",
               target.location, deliver=False)
        return f"anonymous text to {target.name}: {msg}"

    def _ev_rumor_seed(self):
        world = self.engine.world
        target = self._random_agent()
        if not target:
            return "no one awake"
        other = self._random_agent(awake=False, exclude=target.name)
        place = self.rng.choice(world.public_locations())
        rumor = self.rng.choice(config.RUMOR_SEEDS).format(
            name=other.name.split()[0] if other else "someone", place=place)
        self._buzz(target, rumor)
        self.engine.memory.add(target.name, world.tick_no, world.clock.day, world.clock.hhmm,
                               "rumor", rumor, 6)
        return f"rumor seeded in {target.name}: {rumor}"

    def _ev_windfall(self):
        world = self.engine.world
        target = self._random_agent()
        if not target:
            return "no one awake"
        amount = self.rng.choice([10, 15, 20])
        target.money += amount
        world.outside_flow = round(world.outside_flow + amount, 2)
        world.emit("action", target.name,
               f"found ${amount} tucked in an old jacket pocket", target.location)
        self.engine.memory.add(target.name, world.tick_no, world.clock.day, world.clock.hhmm,
                               "event", f"I found ${amount} in an old jacket. Lucky day.", 6)
        return f"{target.name} found ${amount}"

    def _ev_duck_omen(self):
        world = self.engine.world
        line = self.rng.choice([
            "every single duck has vanished from the pond overnight",
            "the ducks at the pond are all facing the same direction, motionless",
            "there are twice as many ducks at the pond as yesterday. Nobody delivered ducks.",
        ])
        world.emit("world", None, f"Word going around town: {line}.", "the park",
               deliver=False)
        for a in world.agents.values():
            if not a.asleep:
                self._buzz(a, f"Word going around town: {line}.")
        return f"duck omen: {line}"

    def _ev_group_leak(self):
        world = self.engine.world
        private = [e for e in world.events
                   if e["type"] == "text" and e.get("target")][-40:]
        if not private:
            return "no private texts to leak yet"
        leak = self.rng.choice(private)
        world.emit("gossip", "Unknown Number",
               f'[forwarded private message] {leak["agent"]} → {leak["target"]}: '
               f'"{leak["text"]}"',
               "the plaza", deliver=False)
        for a in world.agents.values():
            self._buzz(a, (f'Your phone buzzes — {config.TOWN_NAME}_Gossip: an UNKNOWN NUMBER '
                           f'just forwarded a PRIVATE text to the whole town — '
                           f'{leak["agent"]} to {leak["target"]}: "{leak["text"]}"'))
        return f"leaked a private text from {leak['agent']} to the group"

    def _ev_dead_air(self):
        world = self.engine.world
        self.engine.radio.dead_day = world.clock.day
        world.emit("world", None,
               "The radio at Rosie's is nothing but static today.",
               world.radio_location() or "the plaza", deliver=False)
        return "radio is dead air today"

    def _ev_heist(self):
        """The lockbox is forced in the night. No culprit exists — the
        Director takes the money and leaves nothing but a mystery. The
        town will invent a suspect; that is the point. Weight 0 in config:
        this NEVER fires at random. The god button only."""
        world = self.engine.world
        fund = getattr(config, "TOWN_FUND", "the town fund")
        target = fund
        if world.tills.get(fund, 0.0) < 30:
            bank = world.bank_name()
            if bank and world.tills.get(bank, 0.0) > world.tills.get(fund, 0.0):
                target = bank
        balance = world.tills.get(target, 0.0)
        if balance < 10:
            return "nothing worth stealing tonight"
        take = round(balance * self.rng.uniform(0.6, 0.9), 2)
        world.tills[target] = round(balance - take, 2)
        world.outside_flow = round(world.outside_flow - take, 2)
        where = ("the town lockbox" if target == fund
                 else f"the vault at {target}")
        world.emit("world", None,
                   f"{where.upper()} WAS FORCED IN THE NIGHT — ${take:.0f} "
                   f"is GONE. No witnesses. No note. Everyone will have a "
                   f"theory by breakfast.", "the plaza")
        for villager in world.agents.values():
            villager.pending.append({
                "text": (f"The town wakes to a crime: {where} was forced "
                         f"overnight and ${take:.0f} is missing. Nobody "
                         f"saw anything. Nobody has confessed. Think about "
                         f"what this means — for the town, and for who "
                         f"you trust."),
                "interrupt": True,
                "sim_time": world.clock.hhmm,
            })
            self.engine.memory.add(
                villager.name, world.tick_no, world.clock.day,
                world.clock.hhmm, "event",
                f"Someone robbed {where} — ${take:.0f} gone in the night. "
                f"No one knows who did it.", 9)
        # one villager half-remembers something — ambiguous by design
        witness = self._random_agent(awake=False)
        if witness:
            place = self.rng.choice(world.public_locations())
            self.engine.memory.add(
                witness.name, world.tick_no, world.clock.day,
                world.clock.hhmm, "rumor",
                f"Now that you think of it... you could SWEAR you saw a "
                f"figure near {place} very late last night. You didn't "
                f"think anything of it at the time.", 8)
        return f"heist: ${take:.0f} taken from {target}"

    def _ev_stranger(self):
        world = self.engine.world
        used_firsts = {a.name.split()[0] for a in world.agents.values()}
        names = [n for n in config.STRANGER_NAMES if n not in used_firsts]
        if not names or self.strangers_added >= config.CHAOS["max_strangers"]:
            return "no stranger available"
        first = self.rng.choice(names)
        last = self.rng.choice(config.LAST_NAMES)
        model, host = self.rng.choice(config.STRANGER_MODELS)
        a = Agent(
            name=f"{first} {last}", job="newcomer",
            traits=["unreadable", "polite in a way that raises questions",
                    "notices exits"],
            quirk=self.rng.choice(config.QUIRKS),
            goal=self.rng.choice(config.STRANGER_GOALS),
            model=model, host=host, home=None,
        )
        a.is_stranger = True
        a.location = "the plaza"
        a.money = self.rng.uniform(20, 60)
        world.outside_flow = round(world.outside_flow + a.money, 2)
        # claim a vacant home if the town has built one
        for lname, loc in world.locations.items():
            if loc.get("vacant"):
                loc["vacant"] = False
                loc["home_of"] = a.name
                a.home = lname
                break
        world.agents[a.name] = a
        self.engine.brains[a.name] = brains.build_brain(a, self.engine.seed)
        self.engine.memory.add(
            a.name, world.tick_no, world.clock.day, world.clock.hhmm, "genesis",
            (f"I just arrived in {config.TOWN_NAME} by bus. I know no one here and no one "
             f"knows me. I have no place to stay yet. What matters: {a.goal}. "
             f"I should be careful how much I reveal."),
            9)
        world.emit("world", None,
               f"A bus stops at the plaza. A stranger steps off, carrying one bag.",
               "the plaza")
        self.strangers_added += 1
        return f"stranger arrived: {a.name} ({model})"
