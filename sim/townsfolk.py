"""The townsfolk — scripted residents who make a town a town.

Five villagers alone in a village is not a village. It is five people in
an empty diner describing their hunger to each other, which is exactly
what Pepperton became: six broke neighbours generating sympathetic
replies about being broke, forever, because the only thing in anyone's
perception window was somebody else's complaint.

Vera Tibbs diagnosed it herself, holding fifty cents: "The Rusty Tap
needs CUSTOMERS, not friends trying to outdo each other with
generosity."

So the town gets people in it. They cost nothing — no model, no GPU,
seeded and deterministic — and they do four legible things:

  * they are PRESENT, so a room is not empty
  * they TRY DOORS, and are seen finding them open or shut
  * they SPEAK TO villagers, which is a stranger wanting something
    from you rather than a friend describing his hunger
  * they OFFER PAID WORK — a person standing in front of you with cash
    in hand, which is the one prompt shape this town has never had

THE RULE THIS MODULE IS BUILT ON, and it is not negotiable:

    AN NPC NEVER GIVES A VILLAGER ANYTHING THEY DID NOT EARN.

No gifts. No free meals. No donations to the jar. No rescues. Money
crosses from a townsperson to a villager over a counter or in exchange
for labour, and by no other path. A world that feeds the idle produces
the idle; we have seventy days of evidence. They transact. They do not
donate.

Townsfolk carry money in from outside the closed loop, so every dollar
is audited through world.outside_flow exactly like the bus.
"""

import config


def cfg():
    """Every knob getattr-defaulted; ships DARK (the v2.4.1 law)."""
    out = {
        "enabled": False,        # armed deliberately, never by surprise
        "count": 6,              # how many walk the streets
        "spend": [3, 7],         # what a townsperson parts with per purchase
        "move_chance": 0.35,     # odds of wandering somewhere new per tick
        "shop_chance": 0.18,     # odds of trying a door when at a business
        "shut_quiet_ticks": 24,  # a locked door is news, not wallpaper
        "speak_chance": 0.12,    # odds of addressing a villager present
        "oddjob_chance": 0.05,   # odds of posting paid work
        "oddjob_pay": [4, 8],    # what an odd job pays the villager who takes it
        "oddjob_expires": 8,     # ticks an offer stands before it walks away
    }
    out.update(getattr(config, "TOWNSFOLK", {}))
    return out


# Ordinary people. Deliberately unremarkable — they are scenery with
# errands, not characters competing with the cast for attention.
_NAMES = [
    ("Mabel Crane", "picking up something for her sister"),
    ("Denny Ross", "on his rounds"),
    ("Colleen Vasquez", "killing an hour before the bus"),
    ("Big Pete", "looking for someone who owes him"),
    ("Junie Ward", "walking the dog she does not have"),
    ("Hollis Trapp", "in no particular hurry"),
    ("Arnie Sokol", "here about a thing"),
    ("Della Pitts", "running errands and complaining about it"),
]

_GREETINGS = [
    "Morning. You open?",
    "Anybody working today?",
    "You serving, or should I come back?",
    "Is this place open or not?",
    "I've got money and nowhere to spend it. You open?",
]

_ODD_JOBS = [
    "a hand shifting some crates",
    "somebody to carry a load out to the road",
    "an hour of help clearing out a shed",
    "a pair of hands for twenty minutes",
    "help loading a cart, won't take long",
]


class Townsfolk:
    """Scripted residents. Deterministic, seeded, zero GPU."""

    def __init__(self, world, rng):
        self.world = world
        self.rng = rng
        self.people = []       # [{name, why, location}]
        self.offers = {}       # location -> {npc, pay, task, expires_tick}
        self.brought_in = 0.0  # audited outside money
        self._shut_said = {}   # location -> tick, so a locked door is news
                               # rather than wallpaper
        self._seeded = False

    # ------------------------------------------------------------- setup
    def ensure(self):
        c = cfg()
        if self._seeded or not c["enabled"]:
            return
        self._seeded = True
        world = self.world
        public = world.public_locations() or ["the plaza"]
        # Rosie exists at last. Every town has a diner named for her and
        # nobody has ever met her; she has been the richest entity in the
        # multiverse and a hole in the data model. She stands behind her
        # own counter now.
        diner = next((k for k, v in world.locations.items()
                      if v.get("sells_food") and "Rosie" in k), None)
        if diner:
            self.people.append({"name": "Rosie", "why": "runs the place",
                                "location": diner, "fixed": True})
        pool = list(_NAMES)
        for _ in range(max(0, c["count"] - len(self.people))):
            if not pool:
                break
            name, why = pool.pop(self.rng.randrange(len(pool)))
            self.people.append({"name": name, "why": why,
                                "location": self.rng.choice(public),
                                "fixed": False})

    # ----------------------------------------------------------- helpers
    def _open_here(self, location):
        """Is somebody actually working in this room right now?"""
        for villager in self.world.agents.values():
            if villager.asleep or villager.location != location:
                continue
            if (villager.activity or {}).get("type") != "work":
                continue
            if villager.workplace() == location:
                return villager
        return None

    def _villagers_here(self, location):
        return [a for a in self.world.agents.values()
                if a.location == location and not a.asleep]

    def snapshot(self):
        """For the observatory map — rendered dimmer than a real mind, so
        scenery is never mistaken for somebody thinking."""
        return [{"name": p["name"], "location": p["location"],
                 "why": p["why"], "npc": True} for p in self.people]

    # -------------------------------------------------------------- tick
    def step(self):
        c = cfg()
        if not c["enabled"] or not getattr(config, "ECONOMY", False):
            return
        self.ensure()
        self._expire_offers()
        for person in self.people:
            if self.world.clock.is_night:
                continue          # townsfolk go home too
            self._act(person, c)

    def _act(self, person, c):
        world = self.world
        here = person["location"]
        business = here in world.tills and not world.locations.get(
            here, {}).get("bank")

        # try the door
        if business and self.rng.random() < c["shop_chance"]:
            worker = self._open_here(here)
            if worker:
                amount = round(self.rng.uniform(c["spend"][0],
                                                c["spend"][1]), 2)
                world.tills[here] = round(world.tills.get(here, 0.0)
                                          + amount, 2)
                self.brought_in = round(self.brought_in + amount, 2)
                world.outside_flow = round(world.outside_flow + amount, 2)
                world.emit("action", person["name"],
                           f"spent ${amount:.0f} at {here} — "
                           f"{worker.name.split()[0]} served them",
                           here)
                world.settle_business_debts()
            else:
                # SEEN to be turned away. This is the lesson the bus can
                # only deliver as a receipt at the end of the day — but a
                # lesson repeated every tick is wallpaper, and it would
                # crowd the villagers' own conversations out of their
                # context windows. So a locked door is reported at most
                # once every shut_quiet_ticks, and the person walks off
                # rather than standing there rattling it.
                last = self._shut_said.get(here)
                if last is None or \
                        world.tick_no - last >= c["shut_quiet_ticks"]:
                    self._shut_said[here] = world.tick_no
                    world.emit("action", person["name"],
                               f"tried the door at {here}, found it shut, "
                               f"and walked off with their money still in "
                               f"their pocket", here)
            # tried it, open or shut — now go somewhere else, like a person
            if not person.get("fixed"):
                elsewhere = [p for p in (world.public_locations() or [here])
                             if p != here]
                if elsewhere:
                    person["location"] = self.rng.choice(elsewhere)
            return

        # a stranger who wants something from you
        locals_here = self._villagers_here(here)
        if locals_here and self.rng.random() < c["speak_chance"]:
            target = self.rng.choice(locals_here)
            world.emit("say", person["name"],
                       self.rng.choice(_GREETINGS), here, target=target.name)
            return

        # PAID WORK, offered in person, with the money in hand
        if locals_here and here not in self.offers and \
                self.rng.random() < c["oddjob_chance"]:
            pay = round(self.rng.uniform(c["oddjob_pay"][0],
                                         c["oddjob_pay"][1]), 2)
            task = self.rng.choice(_ODD_JOBS)
            self.offers[here] = {
                "npc": person["name"], "pay": pay, "task": task,
                "expires": world.tick_no + c["oddjob_expires"],
            }
            world.emit("say", person["name"],
                       f"I need {task} — ${pay:.0f} to whoever wants it. "
                       f"I'm here for a bit.", here)
            for villager in locals_here:
                villager.pending.append({
                    "text": (f"{person['name']} is standing right here "
                             f"offering ${pay:.0f} cash for {task}. Use the "
                             f"work action here to take it."),
                    "interrupt": villager.money < config.MEAL_COST,
                    "sim_time": world.clock.hhmm,
                })
            return

        # wander
        if self.rng.random() < c["move_chance"] and not person.get("fixed"):
            dest = self.rng.choice(world.public_locations() or [here])
            if dest != here:
                person["location"] = dest

    # ------------------------------------------------------------ offers
    def offer_at(self, location):
        return self.offers.get(location)

    def take_offer(self, agent, location):
        """A villager claims an odd job by working where it was offered.
        Returns (ok, note). The money is EARNED — that is the only way it
        ever crosses from a townsperson to a villager."""
        offer = self.offers.get(location)
        if not offer:
            return False, None
        del self.offers[location]
        world = self.world
        pay = offer["pay"]
        tax = round(pay * getattr(config, "INCOME_TAX", 0.15), 2)
        agent.money += pay - tax
        if tax:
            world.tills[config.TOWN_FUND] = round(
                world.tills.get(config.TOWN_FUND, 0.0) + tax, 2)
        self.brought_in = round(self.brought_in + pay, 2)
        world.outside_flow = round(world.outside_flow + pay, 2)
        world.earned_today[agent.name] = \
            world.earned_today.get(agent.name, 0.0) + pay - tax
        world.emit("action", agent.name,
                   f"took {offer['npc']}'s odd job — {offer['task']} — and "
                   f"was paid ${pay:.0f} on the spot", location)
        return True, (f"took the odd job from {offer['npc']} and earned "
                      f"${pay - tax:.2f} (after the town's cut). Cash in "
                      f"hand, for work done")

    def _expire_offers(self):
        gone = [loc for loc, o in self.offers.items()
                if self.world.tick_no >= o["expires"]]
        for loc in gone:
            offer = self.offers.pop(loc)
            self.world.emit("action", offer["npc"],
                            f"gave up waiting for {offer['task']} and took "
                            f"their ${offer['pay']:.0f} elsewhere", loc)
