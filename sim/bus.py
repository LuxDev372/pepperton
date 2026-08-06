"""The tour bus — the town's first customers from outside itself.

Pepperton's economy was a closed loop: every dollar sat in a pocket, a
till, or the fund, and the only way money reached a wallet was wages,
and the only way a till refilled was a neighbor spending money he
didn't have. Seven broke people cannot be each other's economy. Vera
Tibbs of Pompeii diagnosed it out loud, holding fifty cents: "The Rusty
Tap needs CUSTOMERS, not friends trying to outdo each other with
generosity."

So the bus route reopens. On a rhythm, a coach pulls into the plaza and
sightseers get off with money from somewhere else entirely — the one
faucet in a world that never had one. They spend an afternoon and
leave.

They will only spend at a business whose doors are OPEN, and a business
is open when a villager is standing in it working. That is the entire
mechanism, and it is not explained to anyone. The bus arrives, the
money goes where the doors are open, and the town watches the receipts.

No sermon ships with this module. The consequences are the argument.
"""

import config


def cfg():
    """Every knob getattr-defaulted — a pre-bus config must still boot
    (the v2.4.1 law)."""
    out = {
        "enabled": True,
        "every_days": 2,          # a rhythm, not a windfall
        "arrive": "11:00",        # midday: the doors have had time to open
        "depart": "16:00",        # and time to stay shut
        "visitors": [4, 8],       # how many step off
        "spend": [4, 9],          # what each will part with, per purchase
        "buys_per_visitor": 2,    # a meal, a souvenir
    }
    out.update(getattr(config, "BUS", {}))
    return out


class BusRoute:
    """Schedules arrivals, moves the money, and reports the receipts."""

    def __init__(self, world, rng):
        self.world = world
        self.rng = rng
        self.day_done = 0        # last day a bus was dispatched
        self.visiting = 0        # visitors currently in town
        self.spent_today = {}    # till -> money taken this visit
        # every dollar this route has ever carried into the world. The
        # loop is no longer closed, so it must at least be AUDITED:
        # town_money_now == town_money_before + brought_in, always.
        self.brought_in = 0.0

    # ------------------------------------------------------------ helpers
    def _open_businesses(self):
        """A business is open when someone is inside it, working. Not
        rostered — WORKING, right now, this tick."""
        world = self.world
        open_now = {}
        for villager in world.agents.values():
            if villager.asleep:
                continue
            act = villager.activity or {}
            if act.get("type") != "work":
                continue
            shop = villager.workplace()
            if not shop or shop != villager.location:
                continue
            if shop not in world.tills:
                continue
            if world.locations.get(shop, {}).get("bank"):
                continue          # nobody takes a bus tour to a bank
            open_now.setdefault(shop, []).append(villager.name)
        return open_now

    # ------------------------------------------------------------- arrival
    def step(self):
        c = cfg()
        if not c["enabled"] or not getattr(config, "ECONOMY", False):
            return
        clock = self.world.clock
        if clock.at(c["arrive"]):
            self._arrive(c)
        elif self.visiting and clock.at(c["depart"]):
            self._depart()
        elif self.visiting:
            self._shop(c)

    def _arrive(self, c):
        day = self.world.clock.day
        if self.day_done >= day:
            return
        if c["every_days"] > 1 and (day % c["every_days"]) != 0:
            return
        self.day_done = day
        self.visiting = self.rng.randint(c["visitors"][0], c["visitors"][1])
        self.spent_today = {}
        world = self.world
        world.emit("world", None,
                   f"A coach pulls into the plaza and {self.visiting} "
                   f"sightseers step down, blinking at the light — cameras "
                   f"out, wallets fat, an afternoon to spend before the bus "
                   f"leaves again.", "the plaza")
        for villager in world.agents.values():
            villager.pending.append({
                "text": (f"A tour bus just stopped in the plaza. "
                         f"{self.visiting} strangers with money are wandering "
                         f"the town looking for somewhere to spend it. The "
                         f"bus leaves at {c['depart']}."),
                "interrupt": True, "sim_time": world.clock.hhmm,
            })

    # -------------------------------------------------------------- trade
    def _shop(self, c):
        """Each tick of the visit, a few visitors spend — but only where
        someone is working. Money enters the world here and nowhere else."""
        world = self.world
        open_now = self._open_businesses()
        if not open_now:
            return
        shops = sorted(open_now)
        for _ in range(max(1, self.visiting // 3)):
            shop = self.rng.choice(shops)
            amount = round(self.rng.uniform(c["spend"][0], c["spend"][1]), 2)
            world.tills[shop] = round(world.tills.get(shop, 0.0) + amount, 2)
            self.spent_today[shop] = round(
                self.spent_today.get(shop, 0.0) + amount, 2)
            self.brought_in = round(self.brought_in + amount, 2)
            world.outside_flow = round(world.outside_flow + amount, 2)
        # the town SEES the register ring — that is the whole lesson
        for shop, took in sorted(self.spent_today.items()):
            if took >= 10 and not self.spent_today.get(f"~announced~{shop}"):
                self.spent_today[f"~announced~{shop}"] = 1
                staff = ", ".join(open_now.get(shop, []))
                world.emit("world", None,
                           f"The visitors have found {shop} open — the till "
                           f"has taken ${took:.0f} from strangers this "
                           f"afternoon" + (f" ({staff} is working)"
                                           if staff else "") + ".", shop)
        # a till with cash pays its back wages immediately; that is how a
        # bartender who worked for IOUs gets paid by a busload of tourists
        world.settle_business_debts()

    # ----------------------------------------------------------- departure
    def _depart(self):
        world = self.world
        takings = {k: v for k, v in self.spent_today.items()
                   if not str(k).startswith("~announced~")}
        total = round(sum(takings.values()), 2)
        self.visiting = 0
        self.spent_today = {}
        if total <= 0:
            world.emit("world", None,
                       "The coach pulls out of the plaza. The visitors "
                       "walked the whole town and found every door shut — "
                       "no diner, no shop, no bar open to take their money. "
                       "They spent nothing here and the bus took their "
                       "wallets with it.", "the plaza")
            for villager in world.agents.values():
                villager.pending.append({
                    "text": ("The tour bus left. Nobody in this town sold "
                             "them anything — every business was closed. "
                             "That money got back on the bus."),
                    "interrupt": True, "sim_time": world.clock.hhmm,
                })
                self._remember(villager.name,
                               "A busload of visitors came to town with money "
                               "and left with all of it, because every door "
                               "was shut. Nobody earned a cent from them.", 8)
            return
        where = "; ".join(f"{shop} ${amt:.0f}"
                          for shop, amt in sorted(takings.items()))
        world.emit("world", None,
                   f"The coach pulls out of the plaza. The visitors left "
                   f"${total:.0f} in this town today — {where}. Every "
                   f"dollar of it went into a business that was OPEN.",
                   "the plaza")
        for villager in world.agents.values():
            villager.pending.append({
                "text": (f"The tour bus left. The visitors spent ${total:.0f} "
                         f"in town today: {where}."),
                "interrupt": False, "sim_time": world.clock.hhmm,
            })
            self._remember(villager.name,
                           f"A busload of strangers spent ${total:.0f} in "
                           f"town today — all of it at the places that were "
                           f"open ({where}).", 7)

    def _remember(self, who, text, importance):
        engine = getattr(self.world, "engine", None)
        if engine is not None:
            engine.memory.add(who, self.world.tick_no, self.world.clock.day,
                              self.world.clock.hhmm, "event", text, importance)
