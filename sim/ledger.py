"""The town ledger — every dollar, debt, and promise in one place.

Money in Pepperton is a closed loop: each dollar is in a villager's
pocket, a business's till, or the town fund. This class owns all of it —
tills, wages, rent, the debt ledger, the promise registry, and the
First Bank's credit decisions. The World delegates here; physics stays
in world.py, economics lives in this file.

(Split out of World after an external review correctly observed that
one class was doing five jobs. The reviewer was right.)
"""

import config


class Ledger:
    # Gratitude idioms — "I owe you one", "I owe you a beer" — are warmth,
    # not contracts. The ledger only wants MONEY promises. (Statute passed
    # after Sam Fletcher was booked twice in one day for saying thank you.)
    PROMISE_IDIOMS = ("owe you one", "owe you a ", "owe you big")
    MONEY_WORDS = ("$", "pay you back", "pay you", "money", "dollar",
                   "buck", "cash", "settle up")

    def __init__(self, world):
        self.world = world
        # Every dollar is somewhere: a pocket, a till, or the fund.
        self.tills = {}
        if getattr(config, "ECONOMY", False):
            for name, loc in world.locations.items():
                if loc.get("bank"):
                    self.tills[name] = float(config.BANK_SEED)
                elif loc.get("sells_food") or loc.get("bar"):
                    self.tills[name] = float(config.TILL_SEED)
            self.tills[config.TOWN_FUND] = float(config.TOWN_FUND_SEED)
            # the poor box starts EMPTY — charity is earned, not seeded
            if getattr(config, "POOR_BOX_ENABLED", True):
                self.tills[getattr(config, "POOR_BOX", "the poor box")] = 0.0
        self.debts = []      # {"id","debtor","creditor","amount","reason","day","due_day","status"}
        self.promises = []   # {"id","maker","to","text","day","due_day","status"}
        self.seq = 0             # shared id counter for debts and promises
        self.rent_day_done = 0   # last day rent was collected
        self.swept_day = 0       # last day the morning sweep ran

    # ------------------------------------------------------------- deposits
    def deposit(self, location, amount):
        if getattr(config, "ECONOMY", False) and location in self.tills:
            self.tills[location] += float(amount)

    # ---------------------------------------------------------------- debts
    def add_debt(self, debtor, creditor, amount, reason, due_day=None,
                 merge=False):
        if merge:
            for debt in self.debts:
                if debt["status"] == "open" and debt["debtor"] == debtor and \
                        debt["creditor"] == creditor and \
                        debt["reason"] == reason:
                    debt["amount"] = round(debt["amount"] + float(amount), 2)
                    return debt
        self.seq += 1
        debt = {"id": self.seq, "debtor": debtor, "creditor": creditor,
                "amount": round(float(amount), 2), "reason": reason,
                "day": self.world.clock.day, "due_day": due_day,
                "status": "open"}
        self.debts.append(debt)
        return debt

    def open_debts(self, debtor=None, creditor=None):
        return [debt for debt in self.debts if debt["status"] == "open"
                and (debtor is None or debt["debtor"] == debtor)
                and (creditor is None or debt["creditor"] == creditor)]

    def apply_payment_to_debts(self, payer, payee, amount):
        """Reduce payer->payee open debts, oldest first. Returns leftover."""
        for debt in self.debts:
            if amount <= 0:
                break
            if debt["status"] == "open" and debt["debtor"] == payer and \
                    debt["creditor"] == payee:
                hit = min(amount, debt["amount"])
                debt["amount"] = round(debt["amount"] - hit, 2)
                amount = round(amount - hit, 2)
                if debt["amount"] <= 0.01:
                    debt["amount"] = 0.0
                    debt["status"] = "paid"
        return amount

    # ------------------------------------------------------------- promises
    def is_money_promise(self, norm):
        if not any(p in norm for p in config.PROMISE_PATTERNS):
            return False
        if any(idiom in norm for idiom in self.PROMISE_IDIOMS) and \
                not any(m in norm for m in self.MONEY_WORDS):
            return False
        return True

    def detect_promise(self, agent, target, norm, text):
        """Speech that creates obligation: the world writes payment promises
        down, and the deadline passing in silence becomes public character."""
        if not getattr(config, "ECONOMY", False) or not target:
            return
        if not self.is_money_promise(norm):
            return
        for promise in self.promises:
            if promise["status"] == "open" and \
                    promise["maker"] == agent.name and \
                    promise["to"] == target:
                return   # one open promise per pair; talk is cheap, once
        self.seq += 1
        due = self.world.clock.day + config.PROMISE_GRACE_DAYS
        self.promises.append({
            "id": self.seq, "maker": agent.name, "to": target,
            "text": text[:140], "day": self.world.clock.day, "due_day": due,
            "status": "open",
        })
        agent.pending.append({
            "text": (f"You just promised {target} money, out loud. The town "
                     f"ledger has it now — pay them something by day {due} "
                     f"(the pay action) or be known as a promise-breaker."),
            "interrupt": False, "sim_time": self.world.clock.hhmm,
        })

    def settle_promises_on_payment(self, payer, payee_name):
        """Any payment before the deadline keeps an open promise."""
        world = self.world
        for promise in self.promises:
            if promise["status"] == "open" and \
                    promise["maker"] == payer.name and \
                    promise["to"] == payee_name:
                promise["status"] = "kept"
                payer.relationships[payee_name] = \
                    payer.relationships.get(payee_name, 0) + 2
                payee = world.agents.get(payee_name)
                if payee:
                    payee.relationships[payer.name] = \
                        payee.relationships.get(payer.name, 0) + 2
                    payee.pending.append({
                        "text": (f"{payer.name} said they'd pay you, and they "
                                 f"actually did. A kept promise is rare currency."),
                        "interrupt": False, "sim_time": world.clock.hhmm,
                    })
                world.emit("action", payer.name,
                           f"kept their word to {payee_name}", payer.location,
                           deliver=False)

    # ---------------------------------------------------------------- wages
    def wage_till_key(self, agent):
        """Which pot pays this villager. The bank's teller is paid from the
        town fund — the bank's till is LOAN CAPITAL, not payroll (learned
        the hard way: a bank that pays wages from its vault forecloses on
        itself by day six)."""
        workplace = agent.workplace()
        if not workplace:
            return config.TOWN_FUND
        if self.world.locations.get(workplace, {}).get("bank"):
            return config.TOWN_FUND
        return workplace if workplace in self.tills else config.TOWN_FUND

    def wage_debt_of(self, till_key):
        return sum(debt["amount"] for debt in self.debts
                   if debt["status"] == "open" and debt["debtor"] == till_key)

    def pay_wage(self, agent, wage):
        """A shift-tick wage, paid FROM the employer's till (public jobs
        draw on the town fund). Shortfalls become back-wage debt the
        business settles automatically when cash comes in."""
        if not getattr(config, "ECONOMY", False):
            agent.money += wage
            return
        till_key = self.wage_till_key(agent)
        avail = self.tills.get(till_key, 0.0)
        paid = min(float(wage), max(0.0, avail))
        if paid > 0:
            self.tills[till_key] = round(avail - paid, 2)
            # withholding: the town takes its cut at the till, straight to
            # the fund that pays the public workers ("to encourage")
            tax = round(paid * getattr(config, "INCOME_TAX", 0.15), 2)
            agent.money += paid - tax
            self.world.earned_today[agent.name] = \
                self.world.earned_today.get(agent.name, 0.0) + paid - tax
            if tax > 0:
                self.tills[config.TOWN_FUND] = round(
                    self.tills.get(config.TOWN_FUND, 0.0) + tax, 2)
        short = round(float(wage) - paid, 2)
        if short > 0:
            debt = self.add_debt(till_key, agent.name, short,
                                 f"back wages at {till_key}", merge=True)
            if debt["amount"] <= short + 0.01:   # first shortfall this stretch
                agent.pending.append({
                    "text": (f"The till at {till_key} couldn't cover your full "
                             f"wage — the difference is on the books as back "
                             f"pay. It'll come when business does."),
                    "interrupt": False, "sim_time": self.world.clock.hhmm,
                })

    def settle_business_debts(self):
        """Tills with cash pay their back-wage debts automatically."""
        if not getattr(config, "ECONOMY", False):
            return
        world = self.world
        for debt in self.debts:
            if debt["status"] != "open" or debt["debtor"] not in self.tills:
                continue
            till = self.tills.get(debt["debtor"], 0.0)
            if till <= 0:
                continue
            payment = round(min(till, debt["amount"]), 2)
            self.tills[debt["debtor"]] = round(till - payment, 2)
            debt["amount"] = round(debt["amount"] - payment, 2)
            creditor = world.agents.get(debt["creditor"])
            if creditor:
                # late wages don't dodge the withholding: income is income,
                # whether the till paid on time or caught up later
                tax = 0.0
                if debt["reason"].startswith("back wages"):
                    tax = round(payment
                                * getattr(config, "INCOME_TAX", 0.15), 2)
                    if tax:
                        self.tills[config.TOWN_FUND] = round(
                            self.tills.get(config.TOWN_FUND, 0.0) + tax, 2)
                creditor.money += payment - tax
                self.world.earned_today[creditor.name] = \
                    self.world.earned_today.get(creditor.name, 0.0) \
                    + payment - tax
            if debt["amount"] <= 0.01:
                debt["amount"] = 0.0
                debt["status"] = "paid"
                if creditor:
                    creditor.pending.append({
                        "text": f"{debt['debtor']} settled your back wages in full.",
                        "interrupt": False, "sim_time": world.clock.hhmm,
                    })

    # --------------------------------------------------------------- credit
    def credit_report(self, agent):
        """(tier, limit, shifts) — the notice board IS the credit score."""
        world = self.world
        shifts = sum(project["contributors"].get(agent.name, 0)
                     for project in world.projects)
        mine = self.open_debts(debtor=agent.name)
        overdue = [debt for debt in mine if debt["due_day"] is not None
                   and world.clock.day > debt["due_day"]]
        bank = world.bank_name()
        if overdue:
            return "bad", 0, shifts
        if bank and self.open_debts(debtor=agent.name, creditor=bank):
            return "extended", 0, shifts
        if shifts >= config.CREDIT_GOOD_SHIFTS:
            return "solid", config.LOAN_MAX_GOOD, shifts
        return "thin", config.LOAN_MAX_SHAKY, shifts

    # -------------------------------------- the Holt Act (v2.7): foreclosure
    @staticmethod
    def foreclosure_cfg():
        cfg = {"enabled": True, "bank_reserve": 60, "grace_days": 3,
               "min_debt": 12, "house_price": 40}
        cfg.update(getattr(config, "FORECLOSURE", {}))
        return cfg

    def debt_market(self):
        """The bank buys the town fund's receivables at face value, oldest
        first, keeping a reserve of loan capital. The fund gets cash NOW
        (it pays the public payroll); the bank gets paper with teeth."""
        cfg = self.foreclosure_cfg()
        world = self.world
        bank = world.bank_name()
        if not cfg["enabled"] or not bank or bank not in self.tills:
            return
        day = world.clock.day
        bought = []
        for debt in self.debts:
            if debt["status"] != "open" or \
                    debt["creditor"] != config.TOWN_FUND or \
                    debt["debtor"] not in world.agents:
                continue   # only villagers' paper; business IOUs stay put
            if debt["due_day"] is None or day <= debt["due_day"]:
                continue   # the bank buys ARREARS — fresh debt keeps its
                           # grace with the fund; blow the deadline and
                           # your paper goes to market
            price = debt["amount"]
            if self.tills[bank] - price < cfg["bank_reserve"]:
                continue   # the vault keeps its reserve, always
            self.tills[bank] = round(self.tills[bank] - price, 2)
            self.tills[config.TOWN_FUND] = round(
                self.tills.get(config.TOWN_FUND, 0.0) + price, 2)
            debt["creditor"] = bank
            debt["assigned_day"] = day
            debt["due_day"] = day + cfg["grace_days"]
            debt.pop("noticed", None)   # the bank posts its own notice
            bought.append(debt)
        if not bought:
            return
        total = sum(d["amount"] for d in bought)
        debtors = {}
        for debt in bought:
            debtors.setdefault(debt["debtor"], 0.0)
            debtors[debt["debtor"]] = round(
                debtors[debt["debtor"]] + debt["amount"], 2)
        world.emit("world", None,
                   f"NOTICE from {bank}: the bank has PURCHASED "
                   f"${total:.0f} of debt owed to the town fund "
                   f"({', '.join(sorted(debtors))}). The paper now belongs "
                   f"to the bank — and the bank collects.", bank)
        for name, owed in sorted(debtors.items()):
            debtor = world.agents.get(name)
            total_held = sum(d["amount"] for d in self.open_debts(
                debtor=name, creditor=bank) if d.get("assigned_day"))
            if debtor:
                debtor.pending.append({
                    "text": (f"{bank} has bought your debt from the town "
                             f"fund — ${owed:.0f} today, ${total_held:.0f} "
                             f"held in all. The bank gives you until day "
                             f"{day + cfg['grace_days']} to pay (the pay "
                             f"action, to 'the bank'). After that, the "
                             f"bank takes the HOUSE."),
                    "interrupt": True, "sim_time": world.clock.hhmm,
                })

    def foreclosure_sweep(self):
        """Bought paper past its grace window: the levy. The bank locks the
        door, lists the house, and the villager sleeps where they can."""
        cfg = self.foreclosure_cfg()
        world = self.world
        bank = world.bank_name()
        if not cfg["enabled"] or not bank:
            return
        day = world.clock.day
        for villager in list(world.agents.values()):
            if not villager.home:
                continue
            held = [d for d in self.open_debts(debtor=villager.name,
                                               creditor=bank)
                    if d.get("assigned_day")]
            overdue = [d for d in held if d["due_day"] is not None
                       and day > d["due_day"]]
            total = sum(d["amount"] for d in held)
            if not overdue or total < cfg["min_debt"]:
                continue
            house = villager.home
            loc = world.locations.get(house)
            if loc is None:
                continue
            loc.pop("home_of", None)
            loc["for_sale"] = cfg["house_price"]
            loc["seized_from"] = villager.name
            loc["desc"] = (f"{villager.name}'s old house — bank-owned, "
                           f"FOR SALE at ${cfg['house_price']} (deed at "
                           f"the teller window)")
            villager.home = None
            world.emit("world", None,
                       f"FORECLOSURE at {house}: {bank} has levied the "
                       f"home of {villager.name} over ${total:.0f} in "
                       f"unpaid debt. The door is locked. The house is "
                       f"for sale — ${cfg['house_price']} at the teller "
                       f"window. The ledger forgets nothing.", house)
            villager.pending.append({
                "text": (f"The bank took your house. Your key does not "
                         f"turn. Everything you owe (${total:.0f}) still "
                         f"stands — pay the bank in full before someone "
                         f"buys {house} and it's yours again. Until then: "
                         f"the park bench, or an inn bed if you can pay."),
                "interrupt": True, "sim_time": world.clock.hhmm,
            })
            for witness in world.agents.values():
                self.world_memory(witness.name,
                                  f"The bank foreclosed on {villager.name} — "
                                  f"took the house over ${total:.0f} in debt. "
                                  f"The ledger has teeth now. Everyone saw.",
                                  9 if witness.name == villager.name else 8)

    def world_memory(self, who, text, importance):
        """Engine-owned memory store, reached through the world's engine
        backref when present (mock tests run world-only)."""
        engine = getattr(self.world, "engine", None)
        if engine is not None:
            engine.memory.add(who, self.world.tick_no, self.world.clock.day,
                              self.world.clock.hhmm, "event", text, importance)

    def check_redemption(self, agent):
        """Paying the bank in full before the sale buys the door back."""
        cfg = self.foreclosure_cfg()
        world = self.world
        bank = world.bank_name()
        if not cfg["enabled"] or not bank:
            return
        held = [d for d in self.open_debts(debtor=agent.name, creditor=bank)
                if d.get("assigned_day")]
        if held:
            return
        for house, loc in world.locations.items():
            if loc.get("for_sale") and loc.get("seized_from") == agent.name:
                loc.pop("for_sale", None)
                loc.pop("seized_from", None)
                loc["home_of"] = agent.name
                loc["desc"] = f"{agent.name}'s house"
                if agent.home is None:
                    agent.home = house
                world.emit("action", agent.name,
                           f"paid the bank in full and bought their own "
                           f"door back — {house} is a home again",
                           agent.location)
                agent.pending.append({
                    "text": (f"Paid in full. The bank handed back the key "
                             f"to {house}. Remember how this felt."),
                    "interrupt": False, "sim_time": world.clock.hhmm,
                })
                return

    def sell_seized_house(self, buyer, house):
        """A villager buys a bank-owned house at the listed price. The
        price goes against the evictee's debt; any surplus is honestly
        returned to them. Returns (ok, note)."""
        cfg = self.foreclosure_cfg()
        world = self.world
        bank = world.bank_name()
        loc = world.locations.get(house)
        if not loc or not loc.get("for_sale"):
            return False, f"{house} is not for sale"
        price = loc["for_sale"]
        if buyer.money < price:
            return False, (f"the deed to {house} costs ${price} and they "
                           f"have ${buyer.money:.0f}")
        evictee_name = loc.get("seized_from")
        buyer.money -= price
        remaining = price
        if bank and evictee_name:
            for debt in self.debts:
                if remaining <= 0:
                    break
                if debt["status"] == "open" and \
                        debt["debtor"] == evictee_name and \
                        debt["creditor"] == bank and debt.get("assigned_day"):
                    hit = min(remaining, debt["amount"])
                    debt["amount"] = round(debt["amount"] - hit, 2)
                    remaining = round(remaining - hit, 2)
                    if debt["amount"] <= 0.01:
                        debt["amount"] = 0.0
                        debt["status"] = "paid"
        applied = round(price - remaining, 2)
        if bank and bank in self.tills and applied:
            self.tills[bank] = round(self.tills[bank] + applied, 2)
        loc.pop("for_sale", None)
        loc.pop("seized_from", None)
        loc["owner"] = buyer.name
        evictee = world.agents.get(evictee_name) if evictee_name else None
        surplus = remaining
        if surplus > 0:
            if evictee:
                evictee.money += surplus
            elif bank and bank in self.tills:
                self.tills[bank] = round(self.tills[bank] + surplus, 2)
        if buyer.home is None:
            buyer.home = house
            loc["home_of"] = buyer.name
            loc["desc"] = f"{buyer.name}'s house (bought at the bank sale)"
            note = f"bought {house} at the bank's sale (-${price}) and moved in"
        else:
            loc["desc"] = (f"a house owned by {buyer.name} "
                           f"(bought at the bank sale)")
            note = (f"bought {house} at the bank's sale (-${price}) — "
                    f"a second deed; a landlord is born")
        if evictee:
            settle_bit = (f"${applied:.0f} of the price went against your "
                          f"debt" + (f"; ${surplus:.0f} came back to you — "
                                     f"honest books" if surplus > 0 else ""))
            evictee.pending.append({
                "text": (f"{buyer.name} bought your old house at the bank's "
                         f"sale. {settle_bit}."),
                "interrupt": True, "sim_time": world.clock.hhmm,
            })
        world.emit("action", buyer.name, note, buyer.location)
        return True, note

    # -------------------------------------------------------- morning sweep
    def morning_sweep(self):
        """The 08:00 sweep: rent falls due, promise deadlines are judged,
        the bank posts arrears. Idempotent per day."""
        if not getattr(config, "ECONOMY", False):
            return
        world = self.world
        day = world.clock.day
        if self.swept_day >= day:
            return
        self.swept_day = day
        # rent day: every RENT_EVERY_DAYS, skipping day 1
        if day > 1 and (day - 1) % config.RENT_EVERY_DAYS == 0 and \
                self.rent_day_done < day:
            self.rent_day_done = day
            for tenant in world.agents.values():
                if not tenant.home:
                    continue
                # a bought deed ends rent: owning your home is the way out
                if world.locations.get(tenant.home, {}).get("owner") == \
                        tenant.name:
                    continue
                if tenant.money >= config.RENT_COST:
                    tenant.money -= config.RENT_COST
                    self.tills[config.TOWN_FUND] = round(
                        self.tills.get(config.TOWN_FUND, 0.0)
                        + config.RENT_COST, 2)
                    tenant.pending.append({
                        "text": (f"Rent day — ${config.RENT_COST} on "
                                 f"{tenant.home}, paid to the town fund."),
                        "interrupt": False, "sim_time": world.clock.hhmm,
                    })
                else:
                    self.add_debt(tenant.name, config.TOWN_FUND,
                                  config.RENT_COST,
                                  f"unpaid rent on {tenant.home}",
                                  due_day=day + config.RENT_GRACE_DAYS)
                    tenant.pending.append({
                        "text": (f"Rent day — ${config.RENT_COST} due on "
                                 f"{tenant.home} and you can't cover it. "
                                 f"You're in the town ledger now; working a "
                                 f"shift pays, and the pay action settles up."),
                        "interrupt": True, "sim_time": world.clock.hhmm,
                    })
        # promise deadlines: silence past the due day is a public verdict
        for promise in self.promises:
            if promise["status"] == "open" and day > promise["due_day"]:
                # grandfather clause: anything recorded under an older, looser
                # statute that wouldn't qualify today lapses without verdict
                if not self.is_money_promise(promise["text"].lower()):
                    promise["status"] = "lapsed"
                    continue
                promise["status"] = "broken"
                maker = world.agents.get(promise["maker"])
                promisee = world.agents.get(promise["to"])
                if promisee:
                    promisee.relationships[promise["maker"]] = \
                        promisee.relationships.get(promise["maker"], 0) - 3
                    promisee.pending.append({
                        "text": (f'{promise["maker"]} promised you money — '
                                 f'"{promise["text"]}" — and the deadline '
                                 f"passed in silence. That tells you who "
                                 f"they are."),
                        "interrupt": True, "sim_time": world.clock.hhmm,
                    })
                if maker:
                    maker.pending.append({
                        "text": (f"You never made good on what you told "
                                 f'{promise["to"]} — "{promise["text"]}". '
                                 f"Broken promises have a way of coming up "
                                 f"in this town."),
                        "interrupt": False, "sim_time": world.clock.hhmm,
                    })
                world.emit("world", None,
                           f'a promise quietly expires: {promise["maker"]} '
                           f'told {promise["to"]} "{promise["text"]}" and '
                           f"never followed through",
                           maker.location if maker else "the plaza",
                           deliver=False)
        # the bank posts arrears — once per debt, town-wide
        bank = world.bank_name()
        if bank:
            for debt in self.debts:
                if debt["status"] == "open" and debt["creditor"] == bank and \
                        debt["due_day"] is not None and \
                        day > debt["due_day"] and not debt.get("noticed"):
                    debt["noticed"] = True
                    world.emit("world", None,
                               f"NOTICE from {bank}: {debt['debtor']} is in "
                               f"arrears — ${debt['amount']:.0f} past due. "
                               f"The ledger forgets nothing.", bank)
                    for villager in world.agents.values():
                        villager.pending.append({
                            "text": (f"Posted at {bank} for all to see: "
                                     f"{debt['debtor']} is in arrears — "
                                     f"${debt['amount']:.0f} past due."),
                            "interrupt": villager.name == debt["debtor"],
                            "sim_time": world.clock.hhmm,
                        })
        # the Holt Act: the bank buys the fund's paper, then collects on it
        self.debt_market()
        self.foreclosure_sweep()
