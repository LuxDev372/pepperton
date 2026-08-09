"""Prompt construction for villager minds."""

import json
import re

import config
from sim.ledger import Ledger

VERBS = """Respond with ONLY a JSON object choosing ONE action:
  {"action": "move", "to": "<location name>"}
  {"action": "say", "text": "<what you say out loud>", "to": "<person here, optional>"}
  {"action": "text", "to": "<anyone in town>", "text": "<message>"}   (phone: reaches them anywhere, PRIVATE — no one can overhear)
  {"action": "text", "to": "everyone", "text": "<message>"}   (posts to __GOSSIP__, the town group chat — the ENTIRE town sees it instantly)
  {"action": "eat"}                      (at the diner, the store, or your own home)
  {"action": "treat", "to": "<person here, or omit for everyone hungry>"}   (BUY someone a meal where food is sold — actual generosity, costs real money)
  {"action": "drink"}                    (only at the Rusty Tap — costs money, loosens you up)
  {"action": "build", "project": "<project from the notice board>"}   (real work, at the project's site — the whole town sees who actually builds)
  {"action": "propose", "project": "<short name for a NEW project>", "site": "<public place in town>", "work": <10-80 shifts>}   (post your own idea on the notice board — the town decides with their hammers)
  {"action": "work"}                     (only at your workplace)
__TRAVEL____ECON_VERBS__  {"action": "buy", "item": "<something sold where you are>"}   (own things — the store window shows what's for sale)
  {"action": "rest"}                     (at home to sleep, or nap in the park)
  {"action": "idle", "note": "<what you do, third person, e.g. 'browses the shelves'>"}
No prose outside the JSON. One action only.
Never repeat something you have already said — the town notices and it gets you nowhere. If talking isn't working (or no one is there to hear you), DO something instead: go where the people are, work, investigate, act on your goal.
NEVER announce that you will share/explain/reveal something later — say the actual thing NOW, with the actual details. Teasing an idea without stating it is the most annoying thing a person can do.
If someone is in the room with you, TALK to them — phones are for people who are elsewhere."""
VERBS = VERBS.replace("__GOSSIP__", f"{config.TOWN_NAME}_Gossip")
_ECON_VERBS = (
    '  {"action": "pay", "to": "<person here, or \'the bank\' / \'the town fund\'>", "amount": <dollars>}   (hand over REAL money — settles ledger debts, keeps promises)\n'
    '  {"action": "borrow", "amount": <dollars>}   (at the bank only — a loan against your reputation; the notice board is your credit score)\n'
)
VERBS = VERBS.replace("__ECON_VERBS__",
                      _ECON_VERBS if getattr(config, "ECONOMY", False) else "")


# ------------------------------------------------- did they answer, or quote?
#
# Day 157, 02:30 —   Hazel:  <message>
#
# That was the whole decision. `<message>` is OUR placeholder, lifted out of
# the schema two lines above and handed straight back. Sam did the same at
# 22:15 and signed it "- [Your Name]".
#
# It is valid JSON, so it parses, so `decision_source` called it "model" and
# it counted toward `live_pct: 100.0` on a day the ledger called admissible.
#
# PROVENANCE ANSWERED "WAS A MIND IN THE SEAT". IT NEVER ANSWERED "DID THE
# MIND SAY ANYTHING." (claude/WHY-THE-DOORS-STAY-SHUT.md)
#
# This is not a judgment about quality — we are forbidden that, and it is the
# difference between an instrument and an editor. It is string matching
# against our own template, and the tokens are SCRAPED FROM THE VERB LIST
# rather than typed here, so a placeholder added upstairs is detected the day
# it ships instead of the day somebody remembers this file exists.
_PLACEHOLDERS = None

# fields a villager fills with their own words; the only places a quoted
# placeholder can hide
_CONTENT_FIELDS = ("text", "note", "project", "to", "item", "site", "reason")


def template_placeholders():
    global _PLACEHOLDERS
    if _PLACEHOLDERS is None:
        found = set(re.findall(r"<[A-Za-z][^<>]{0,48}>", VERBS))
        found.add("[Your Name]")      # not ours; the models bring this one
        _PLACEHOLDERS = frozenset(found)
    return _PLACEHOLDERS


# The model's own scaffolding, not ours (v3.8.7).
#
# Day 164, 20:30 — Hazel Pike's entire contribution to the town group chat:
#
#     Hazel → Pepperton_Gossip: <|end|>
#
# v3.8.3 taught this detector OUR placeholders and stopped there, so a
# villager handing back phi4-mini's end-of-turn token parsed clean and
# counted toward live_pct. I found the form we wrote and forgot the form the
# model speaks — the same shape of miss, one layer out, twelve hours later.
#
# Matched by SHAPE rather than by a list of names: `<|...|>` is chat-template
# syntax and no villager has ever typed it. A new model with a new sentinel
# is caught the day it is cast, without anyone remembering this file.
# The bound was 40, which comfortably fits every sentinel we know of —
# Llama 3's <|reserved_special_token_NNN|> family runs about 25-30. But this
# project casts a new model family every few versions, and a constant tuned
# to today's known templates goes stale silently: the same failure shape as
# the placeholder detector before it learned to scrape VERBS. Widened, and
# made a knob, because everything else here is one (the v2.4.1 law).
# (Found by review, v3.8.8.)
_SENTINEL_RE = None


def _sentinel():
    global _SENTINEL_RE
    if _SENTINEL_RE is None:
        n = max(8, int(getattr(config, "SENTINEL_MAX_LEN", 80)))
        _SENTINEL_RE = re.compile(
            r"<\|[^|<>]{0,%d}\|>|</s>|\[/?INST\]" % n)
    return _SENTINEL_RE


# ---------------------------------------------------- OBSERVED, NOT COUNTED
#
# Day 166: Vera Tibbs posted "All the projects we've worked on and finished
# are listed here: [post a copy of the notice board text]" — the model
# writing the instruction instead of the content. A third costume.
#
# WE DELIBERATELY DO NOT TREAT THIS AS AN ECHO, and the reason is the line
# between an instrument and an editor.
#
# `<|end|>` and `<message>` are safe to catch because pipe-delimited chat
# syntax and our own placeholders have a near-zero collision rate with real
# dialogue — it is a judgment about SHAPE. Square brackets are ordinary
# English: asides, citations, and the [laughs]-style stage direction some
# models produce as genuine character voice. Separating "Vera is leaking an
# instruction" from "Vera writes in brackets" needs a judgment about
# MEANING, and we are not permitted that. A hand-typed verb list
# (post|copy|insert|…) would rot exactly the way hard-coded placeholder
# names would, only slower.
#
# So it is counted and shown to a human, and it NEVER touches
# decision_source, live_pct, or clean. Visibility without putting a
# false positive on the one number the provenance system exists to keep
# honest. (Design owed to review, v3.8.8.)
_ASIDE = re.compile(r"\[[^\[\]\n]{4,80}\]")


def bracketed_aside(action):
    """A bracketed stage direction, or None. OBSERVATIONAL ONLY — this must
    never reach decision_source or any certifying count."""
    if not isinstance(action, dict):
        return None
    for key in ("text", "note"):
        value = action.get(key)
        if isinstance(value, str):
            hit = _ASIDE.search(value)
            if hit:
                return hit.group(0)[:80]
    return None


def goal_fragments():
    """The distinctive tail of every goal string in config.GOALS.

    A villager is told "What's driving you lately: {goal}." in the SYSTEM
    prompt, every tick, forever. On Day 182 Ida Merriweather posted this to
    the Pompeii group chat:

        "I need to organize a town event nobody asked for."

    That is config.GOALS[0], verbatim, converted to first person. It parsed
    clean and counted toward live_pct, because this detector knew our
    placeholders and the models' sentinels and nothing about our goals.
    Third time in the same family, third layer out. (v3.9.5)

    Matched on the TAIL, not the whole string: the leading "wants to" /
    "is trying to" / "is quietly" is grammar a villager would naturally
    change, and the remainder is the part nobody says by accident. Short
    fragments are dropped so an ordinary sentence cannot trip it — "looking
    for a business partner" is plausible speech; "organize a town event
    nobody asked for" is a recitation.

    Scraped from config, not hardcoded, so a town that edits its own goal
    list is covered without anyone remembering this function exists.
    (claude/WHATS-DRIVING-YOU.md)"""
    global _GOAL_FRAGMENTS
    if _GOAL_FRAGMENTS is None:
        out = []
        for goal in (getattr(config, "GOALS", []) or []) + \
                    (getattr(config, "STRANGER_GOALS", []) or []):
            tail = re.sub(r"^(wants to|is trying to|is quietly|is determined "
                          r"to|is looking for|is clearly|claims to be|knew) ",
                          "", str(goal).strip(), flags=re.I).strip()
            if len(tail) < 24:
                continue
            out.append(tail.lower())
            # Ida converted GOALS[0] to first person before saying it, so
            # also carry the mechanical third->first person variant. This
            # is a substitution, not a tuning: no case in today's logs is
            # named here, and a goal list edited tomorrow gets the same
            # treatment. Paraphrase is NOT caught and is not meant to be —
            # a villager restating their preoccupation in their own words
            # is thinking, not reciting.
            first = tail.lower().replace(" their ", " my ").replace(
                " they ", " I ").replace("wants ", "want ")
            if first != tail.lower():
                out.append(first)
        _GOAL_FRAGMENTS = tuple(out)
    return _GOAL_FRAGMENTS


_GOAL_FRAGMENTS = None


def echoed_template(action):
    """Return what a reply quoted back at us instead of deciding, or None.

    Four kinds, all objective, none of them a judgment about content:
      * OUR placeholder   — "<message>", "[Your Name]"   (v3.8.3)
      * THEIR sentinel    — "<|end|>", "</s>", "[INST]"  (v3.8.7)
      * OUR goal line     — "organize a town event nobody asked for" (v3.9.5)
      * nothing at all    — "(empty)"

    A villager may say anything they like. What they may not do is hand back
    the form."""
    if not isinstance(action, dict):
        return None
    for key in _CONTENT_FIELDS:
        value = action.get(key)
        if not isinstance(value, str):
            continue
        for token in template_placeholders():
            if token in value:
                return token
        hit = _sentinel().search(value)
        if hit:
            return hit.group(0)
        low = value.lower()
        for fragment in goal_fragments():
            if fragment in low:
                return fragment
    if action.get("action") in ("say", "text") and \
            not str(action.get("text", "")).strip():
        return "(empty)"
    return None


def _verbs(world):
    """The action list, with `travel` present ONLY while a coach is actually
    standing in the plaza with a destination on its side (v3.4).

    This is the whole design. Nothing tells a villager the road exists — no
    hint, no pending message, no encouragement. The verb is in the list
    because it is genuinely possible right now, exactly the way `work` is,
    and it disappears again when the coach pulls out. If nobody ever uses
    it, that is the answer.
    """
    from sim import road
    c = road.cfg()
    line = ""
    if c["enabled"] and c["destination"]:
        bus = getattr(getattr(world, "engine", None), "bus", None)
        if bus is not None and getattr(bus, "visiting", 0):
            line = ('  {"action": "travel"}                   '
                    '(board the coach in the plaza)\n')
    return VERBS.replace("__TRAVEL__", line)


def _owns_line(agent):
    owned = getattr(agent, "possessions", [])
    if not owned:
        return ""
    return (f" You own: {', '.join(owned)} — comforts you worked for; "
            f"let them show.")


def _inn_bit(world):
    inns = [k for k, v in world.locations.items() if v.get("inn")]
    if not inns:
        return ""
    cost = getattr(config, "INN_ROOM_COST", 5)
    return f", or ${cost}/night for a bed at {inns[0]}"


def _econ_line():
    if not getattr(config, "ECONOMY", False):
        return ""
    return (f"\nMoney in {config.TOWN_NAME} is REAL and it circulates: "
            f"businesses pay wages out of their own tills, rent falls due "
            f"every {config.RENT_EVERY_DAYS} days, debts go in the town "
            f"ledger, and promising someone money OUT LOUD puts it in the "
            f"ledger too — keep your word (the pay action) or the whole town "
            f"learns what your word is worth. The town takes "
            f"{int(getattr(config, 'INCOME_TAX', 0.15) * 100)}% of every "
            f"wage for the town fund — the price of civilization."
            + ("  A poor box sits on the diner counter: drop money in "
               "(pay action, to 'the poor box') and it quietly buys "
               "meals for neighbors who can't pay."
               if getattr(config, "POOR_BOX_ENABLED", True) else "")
            + ("  And know the Holt Act: the bank BUYS unpaid debts from "
               "the town fund, and debt the bank holds can cost you your "
               "HOUSE — the bank forecloses, lists the house for sale, "
               "and you sleep rough until you pay off the bank or someone "
               "buys your old door out from under you."
               if Ledger.foreclosure_cfg()["enabled"] else ""))


def system_prompt(agent, world):
    locs = ", ".join(world.public_locations() + ([agent.home] if agent.home else []))
    if agent.is_stranger:
        roster = ("you don't know anyone here yet — you'll learn who people are "
                  "as you meet them (you can only text people whose names you know)")
    else:
        roster = "; ".join(
            f"{a.name} (the {a.job})" for a in world.agents.values()
            if a.name != agent.name and not a.is_stranger
        )
        if any(a.is_stranger for a in world.agents.values() if a.name != agent.name):
            roster += ("; …and a STRANGER who got off the bus recently — nobody "
                       "knows their name, and they are NOT in your phone contacts")
    return f"""You are {agent.name}, a resident of the small town of {config.TOWN_NAME}. You are the town {agent.job}.
Personality: {', '.join(agent.traits)}. Quirk: {agent.quirk}.
What's driving you lately: {agent.goal}.

You are a real person in a real town, not an assistant. Stay in character: have opinions, hold grudges, pursue what you want. Keep speech to one or two natural sentences.
{config.TOWN_VOICE}
Places in town: {locs}. {f'Your home is {agent.home}.' if agent.home else f'You have NO home here — your options are the park bench (free), paying ${config.ROOM_COST}/night for the room above the Rusty Tap{_inn_bit(world)}. A home of your own would take the town building one (housing — or an inn with beds for everyone — can be proposed on the notice board).'} Your workplace: {agent.workplace() or ('none — you are new in town' if agent.is_stranger else 'none — you are retired from all that')}.{_owns_line(agent)}
The people of {config.TOWN_NAME} (you know everyone; these exact names are your phone contacts): {roster}.
You earn money by working, meals cost money, and you must eat and sleep. If you say things that are false, your neighbors will notice reality disagrees with you.{_econ_line()}

{_verbs(world)}"""


def decision_prompt(agent, world, perceptions, memories):
    here = world.occupants(agent.location, exclude=agent.name)
    people = ", ".join(a.name + f" (the {a.job})" for a in here) or "no one else"
    needs_lines = []
    for k, v in agent.needs.items():
        if v <= config.NEEDS[k]["urgent_below"]:
            if k == "fullness":
                pantry_bit = (f" your pantry at home is FREE ({agent.pantry} "
                              f"servings left);" if agent.home and agent.pantry > 0
                              else "")
                tag = (f" (URGENT — you are genuinely hungry. Fix it NOW:"
                       f"{pantry_bit} working a shift earns meal money; or ask "
                       f"someone — people can buy you food)")
            else:
                tag = " (URGENT)"
        elif k == "fullness" and v >= 85:
            tag = " (completely full — do not eat)"
        else:
            tag = ""
        needs_lines.append(f"{k}: {v:.0f}/100{tag}")
    board_lines = []
    for p in world.projects:
        if p["complete"]:
            top = ", ".join(n.split()[0] for n in list(p["contributors"])[:3])
            board_lines.append(f"- ✔ {p['name']}: FINISHED and standing at {p['site']} "
                               f"(built by {top}). It EXISTS — never talk about "
                               f"starting or planning it.")
            continue
        if p.get("stalled"):
            # shelved, not demolished: it lost its permit and came off the
            # board, but it is still standing at the site and a hammer still
            # works on it. Nobody will ever fine or condemn it again.
            who = ", ".join(f"{n.split()[0]} {c}" for n, c in
                            sorted(p["contributors"].items(),
                                   key=lambda kv: -kv[1])) or "NOBODY YET"
            board_lines.append(
                f"- ⏸ {p['name']} at {p['site']}: SHELVED at {p['done']}/"
                f"{p['work']} — off the notice board, no permit, no deadline, "
                f"nobody is coming to tear it down. It is still standing there "
                f"half-built and the build action still works on it. "
                f"Shifts already in it: {who}.")
            continue
        if p["contributors"]:
            who = ", ".join(f"{n.split()[0]} {c}" for n, c in
                            sorted(p["contributors"].items(), key=lambda kv: -kv[1]))
            status = (f"UNDER CONSTRUCTION, {p['done']}/{p['work']} done — already "
                      f"started, no more planning needed, just build")
        else:
            who = "NOBODY YET"
            status = f"0/{p['work']} — not started"
        permit_note = ""
        if getattr(config, "PERMITS_ENABLED", True) and p.get("permit_due"):
            if p.get("fined") and getattr(config, "CONDEMN_ENABLED", True):
                permit_note = (f" [PERMIT EXPIRED — CONDEMNED day "
                               f"{p.get('condemn_day')} unless FINISHED; "
                               f"only hammers save it now]")
            elif p.get("fined"):
                permit_note = (f" [PERMIT EXPIRED — comes off the board day "
                               f"{p.get('condemn_day')}. It will still be "
                               f"standing and still buildable; it just stops "
                               f"being the town's business]")
            else:
                permit_note = f" [permit to day {p['permit_due']}]"
        board_lines.append(f"- {p['name']} at {p['site']}: {status} — "
                           f"contributors: {who}{permit_note}")
    board = "\n".join(board_lines) or (
        "- (EMPTY — everything proposed has been built! The town did that. "
        "Anyone can post a new project with the propose action — what does "
        "Pepperton still need?)")
    streak_note = ""
    if agent.talk_streak >= config.TALK_STREAK_NUDGE:
        streak_note = ("\nYou have done NOTHING but talk and post for a while now. "
                       "Words are piling up and your life is not moving. Everyone "
                       "can see the notice board — and your name isn't on it. Pick "
                       "a REAL action this time: build, work, go somewhere, do.")
    mem_lines = "\n".join(
        f"- (day {m['day']} {m['sim_time']}) {m['text']}" for m in memories
    ) or "- nothing comes to mind"
    perc_lines = "\n".join(f"- {p['text']}" for p in perceptions) or "- nothing new"
    activity = (agent.activity or {}).get("type") or "nothing in particular"
    rel_note = ""
    rels = sorted(((n, c) for n, c in agent.relationships.items() if n and c != 0),
                  key=lambda kv: -abs(kv[1]))[:4]
    if rels:
        def _tag(v):
            if v >= 5: return "warm"
            if v > 0: return "friendly"
            if v <= -5: return "bad blood"
            return "sore"
        rel_note = ("\nWhere you stand with people (your own judgment, built from history): "
                    + ", ".join(f"{n.split()[0]} {c:+d} ({_tag(c)})" for n, c in rels)
                    + ". Let it show — warmth to friends, edge to those who earned it.")
    drink_note = ""
    recent = [t for t in agent.drink_ticks
              if world.tick_no - t <= config.TIPSY_TICKS]
    if len(recent) >= config.DRUNK_AT:
        drink_note = ("\nYou are properly DRUNK: loud, overly honest, sentimental, "
                      "and your judgment is bad. You say the quiet parts out loud.")
    elif recent:
        drink_note = ("\nYou've had a drink and you're pleasantly loosened up: more "
                      "candid, more talkative, more likely to say what you actually "
                      "think — and less careful about what you keep to yourself.")
    night_note = ""
    if world.clock.is_night:
        night_note = ("\nIt is the MIDDLE OF THE NIGHT. Normal people are in bed. "
                      "Unless something is genuinely urgent, go home and rest — "
                      "whatever this is will still be here in the morning.")
    wp = agent.workplace()
    bank = world.bank_name() if hasattr(world, "bank_name") else None
    if agent.money < config.MEAL_COST:
        box = getattr(config, "POOR_BOX", "the poor box")
        box_bal = world.tills.get(box, 0.0) if hasattr(world, "tills") else 0.0
        box_bit = (f" The poor box at the diner holds ${box_bal:.0f} — a meal "
                   f"is there for whoever needs it (just eat at the diner)."
                   if box_bal >= config.MEAL_COST else "")
        bank_bit = ""
        if bank and getattr(config, "ECONOMY", False):
            tier, limit, _ = world.credit_report(agent)
            if limit > 0:
                bank_bit = (f" {bank} would lend you up to ${limit} against "
                            f"your record (borrow action, at the bank) — but "
                            f"loans come due, with interest.")
        money_tag = (f" (BROKE. Building pays NOTHING — it's volunteer work. "
                     f"{'Your job at ' + wp + ' PAYS real wages; go work a shift.' if wp else 'You need paid work or someone generous.'}{bank_bit}{box_bit})")
    elif agent.money < 3 * config.MEAL_COST:
        money_tag = (" (running low — remember building is unpaid; "
                     + (f"your job at {wp} is what pays" if wp else "paid work is what pays") + ")")
    else:
        money_tag = ""
        if agent.money >= 30 and hasattr(world, "goods_catalog"):
            unowned = [k for k, v in world.goods_catalog().items()
                       if not v.get("consumable")
                       and k not in getattr(agent, "possessions", [])]
            if unowned:
                money_tag = (" (comfortable — money can become a life: "
                             f"{unowned[0]} is ${world.goods_catalog()[unowned[0]]['cost']} "
                             f"at {world.goods_catalog()[unowned[0]]['sold_at']})")
    shop_note = ""
    if hasattr(world, "goods_sold_here"):
        here_goods = world.goods_sold_here(agent.location)
        if here_goods:
            shop_note = ("\nFor sale here: " + "; ".join(
                f"{k} (${v['cost']}) — {v['pitch']}"
                for k, v in here_goods.items()) +
                ". (the buy action, if you've earned it)")
    ledger_note = ""
    if getattr(config, "ECONOMY", False) and hasattr(world, "open_debts"):
        bits = []
        for d in world.open_debts(debtor=agent.name)[:3]:
            due = ""
            if d["due_day"] is not None:
                due = (", OVERDUE — settle it or your credit is dead"
                       if world.clock.day > d["due_day"]
                       else f", due day {d['due_day']}")
            bits.append(f"you OWE {d['creditor']} ${d['amount']:.0f} "
                        f"({d['reason']}{due})")
        for d in world.open_debts(creditor=agent.name)[:3]:
            if d["debtor"] in world.agents:
                bits.append(f"{d['debtor'].split()[0]} owes YOU "
                            f"${d['amount']:.0f} ({d['reason']}) — you may "
                            f"want to bring that up")
            else:
                bits.append(f"{d['debtor']} owes you ${d['amount']:.0f} in "
                            f"back wages (it pays out as their till refills)")
        for p in world.promises:
            if p["status"] == "open" and p["maker"] == agent.name:
                bits.append(f"you PROMISED {p['to'].split()[0]} money, on the "
                            f"record — pay them something by day {p['due_day']} "
                            f"or the town learns what your word is worth")
        # the Holt Act: bank-held paper is a countdown to losing the house
        bank = world.bank_name()
        if bank:
            held = [d for d in world.open_debts(debtor=agent.name,
                                                creditor=bank)
                    if d.get("assigned_day")]
            if held and agent.home:
                total = sum(d["amount"] for d in held)
                soonest = min(d["due_day"] for d in held
                              if d["due_day"] is not None)
                bits.append(f"the BANK holds ${total:.0f} of your debt — "
                            f"pay the bank by day {soonest} or it takes "
                            f"your HOUSE. This is not a bluff; ask anyone "
                            f"who has watched a foreclosure")
            for house, loc in world.locations.items():
                if loc.get("for_sale"):
                    if loc.get("seized_from") == agent.name:
                        bits.append(f"your old home {house} is listed at "
                                    f"${loc['for_sale']} — pay the bank "
                                    f"off IN FULL first and the key is "
                                    f"yours again, free")
                    elif agent.money >= loc["for_sale"]:
                        bits.append(f"{house} is bank-owned and FOR SALE "
                                    f"at ${loc['for_sale']} — you could "
                                    f"BUY it (the buy action, at the "
                                    f"bank): a deed means never paying "
                                    f"rent on it")
        if bits:
            ledger_note = "\nThe ledger (public record): " + "; ".join(bits) + "."
        if getattr(config, "HIRING_ENABLED", True) and \
                not agent.workplace() and hasattr(world, "open_positions"):
            openings = world.open_positions()
            if openings:
                sits = "; ".join(f"{j} at {w}"
                                 for j, w in sorted(openings.items()))
                ledger_note += (f"\nSITUATIONS VACANT (you have no job, and "
                                f"wages are the only honest money): {sits}. "
                                f"Go there and use the work action — "
                                f"showing up IS the interview.")
    return f"""It is {world.clock.label}. You are at {agent.location} ({world.locations[agent.location].get('desc', '')}).{drink_note}{night_note}
Here with you: {people}.{rel_note}
Your money: ${agent.money:.0f}{money_tag}. Your needs: {'; '.join(needs_lines)}.{ledger_note}{shop_note}
You were doing: {activity}.

The town notice board (public — everyone sees who builds and who doesn't. Building is VOLUNTEER work: it pays $0 and glory; jobs pay money. A person needs both):
{board}
{streak_note}
Things you remember that feel relevant:
{mem_lines}

Since you last stopped to think, you noticed:
{perc_lines}

What do you do right now? {_verbs(world)}"""


def reflection_prompt(agent, day, day_memories):
    lines = "\n".join(
        f"- {m['sim_time']} [{m['kind']}] {m['text']}" for m in day_memories[-60:]
    )
    return f"""You are {agent.name}, the {config.TOWN_NAME} {agent.job}. The day is over. Here is what happened to you on day {day}:
{lines}

In 2-3 sentences, in first person, reflect on the day: what mattered, how you feel about the people you dealt with, and what you intend to do about it tomorrow.
Respond with ONLY this JSON object (null is fine for any field you have no answer for):
{{"reflection": "<your 2-3 sentences>",
  "warmer": "<first name of ONE person who rose in your regard today, or null>",
  "colder": "<first name of ONE person who fell in your regard today, or null>",
  "goal_resolved": <true ONLY if your current preoccupation finally feels settled or finished, else false>}}"""


def parse_json_reply(raw):
    """Lenient JSON extraction — models decorate; we excavate."""
    if not raw:
        return None
    raw = raw.strip()
    # strip code fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    # find outermost braces
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
        start = raw.find("{", start + 1)
    return None
