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
                creditor.money += payment
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
