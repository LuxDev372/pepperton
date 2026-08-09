"""Villager minds.

Three species:
  LLMBrain      — a real model, per-agent model + provider (the point).
                  Speaks Ollama (local or cloud) or any OpenAI-compatible
                  gateway; which one is a config detail, not a code one.
                  `OllamaBrain` is kept as an alias for older configs.
  MockBrain     — scripted, deterministic, GPU-free; keeps the whole sim
                  testable end-to-end and doubles as the fallback when a
                  model host is unreachable.
  ExternalBrain — the possession seat. A queue an outside mind (Brad at a
                  console, or a Claude session) can drive; falls back to
                  its understudy brain when the seat is empty. The town
                  never knows which citizens are possessed.
"""

import os
import random

import requests

import config
from sim import prompts


# ------------------------------------------------------------- providers
def providers():
    """The full provider table: every OLLAMA_HOSTS key folded in as a plain
    Ollama provider, then config.PROVIDERS layered on top. This is what
    keeps a bare {"default": "http://..."} config working unchanged."""
    table = {k: {"api": "ollama", "url": v}
             for k, v in getattr(config, "OLLAMA_HOSTS", {}).items()}
    table.update(getattr(config, "PROVIDERS", {}))
    return table


def resolve_provider(key):
    """Provider spec for a host/provider key, falling back to 'default'."""
    table = providers()
    return table.get(key) or table.get("default") or {
        "api": "ollama", "url": "http://127.0.0.1:11434"}


class BrainBase:
    def decide(self, agent, world, perceptions, memories):
        raise NotImplementedError

    def reflect(self, agent, day, day_memories):
        raise NotImplementedError


# ------------------------------------------------------------------- LLM
class LLMBrain(BrainBase):
    """One villager, one model, one provider. The wire format is chosen by
    the provider spec; everything above this line is provider-agnostic."""

    def __init__(self, model, host_key="default"):
        self.model = model
        self.host_key = host_key
        self.provider = resolve_provider(host_key)
        self.url = self.provider.get("url", "http://127.0.0.1:11434").rstrip("/")
        self.understudy = None   # set by engine: MockBrain fallback

    # -- helpers ------------------------------------------------------
    @property
    def timeout(self):
        return self.provider.get("timeout", config.OLLAMA_TIMEOUT)

    def _options(self):
        opts = dict(config.OLLAMA_OPTIONS)
        opts.update(self.provider.get("options", {}))
        return opts

    def _thinks_too_much(self):
        tag = self.model.lower()
        return any(s in tag for s in getattr(config, "THINK_OFF", ("qwen3",)))

    def _api_key(self):
        """Read the provider's key from the environment. Never logged, never
        written to config — a missing key is a normal, survivable state."""
        env = self.provider.get("api_key_env")
        if not env:
            return None
        key = os.environ.get(env, "").strip()
        if not key:
            raise requests.RequestException(
                f"{env} is not set — no key for provider {self.host_key!r}")
        return key

    # -- the wire -----------------------------------------------------
    def _chat(self, system, user):
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": user}]
        if self.provider.get("api") == "openai":
            return self._chat_openai(messages)
        return self._chat_ollama(messages)

    def _chat_ollama(self, messages):
        payload = {
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": self._options(),
        }
        # Some families deliberate at length in hidden thinking mode, which
        # makes those villagers geologically slow. Turn it off where we can.
        if self._thinks_too_much():
            payload["think"] = False
        r = requests.post(f"{self.url}/api/chat", json=payload,
                          timeout=self.timeout)
        r.raise_for_status()
        body = self._body(r)
        return str(body.get("message", {}).get("content") or "")

    def _chat_openai(self, messages):
        opts = self._options()
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": opts.get("temperature", 0.8),
        }
        if self.provider.get("json_mode", True):
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        headers.update(self.provider.get("headers", {}))
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = requests.post(f"{self.url}/chat/completions", json=payload,
                          headers=headers, timeout=self.timeout)
        r.raise_for_status()
        choices = self._body(r).get("choices") or [{}]
        return str(choices[0].get("message", {}).get("content") or "")

    @staticmethod
    def _body(r):
        """Decode a response, treating a 200-with-error-payload (and any
        non-JSON body) as a transport failure so the understudy takes over
        instead of the tick dying on a KeyError."""
        try:
            body = r.json()
        except ValueError:
            raise requests.RequestException(
                f"non-JSON reply ({r.text[:120]!r})")
        if not isinstance(body, dict):
            raise requests.RequestException(f"unexpected reply shape: {type(body).__name__}")
        if body.get("error"):
            raise requests.RequestException(str(body["error"])[:200])
        return body

    def decide(self, agent, world, perceptions, memories):
        system = prompts.system_prompt(agent, world)
        user = prompts.decision_prompt(agent, world, perceptions, memories)
        agent.last_prompt = system + "\n\n---\n\n" + user
        try:
            raw = self._chat(system, user)
        except requests.RequestException as e:
            agent.last_reply = f"({self.host_key}/{self.model} error: {e})"
            if self.understudy:
                action, _, reason = self.understudy.decide(agent, world, perceptions, memories)
                return action, agent.last_reply, f"host unreachable; understudy acted: {reason}"
            return {"action": "idle", "note": "stares into the middle distance"}, agent.last_reply, "host unreachable"
        agent.last_reply = raw
        action = prompts.parse_json_reply(raw)
        if not isinstance(action, dict) or "action" not in action:
            return {"action": "idle", "note": "mutters something unintelligible"}, raw, "unparseable reply"
        # a reply that quotes our own schema is not a decision (v3.8.3).
        # The ACTION IS UNTOUCHED — the world does exactly what it did
        # before, and the villager is not corrected, censored or nudged.
        # Only the provenance changes, so a day containing it can no longer
        # certify as 100% thought.
        quoted = prompts.echoed_template(action)
        if quoted:
            return action, raw, f"echoed the template {quoted}"
        return action, raw, "model decision"

    def reflect(self, agent, day, day_memories):
        fallback = {"reflection": f"Another day. {agent.goal.capitalize()} — "
                                  f"still working on it.",
                    "warmer": None, "colder": None, "goal_resolved": False}
        try:
            raw = self._chat(
                f"You are {agent.name}. Answer only with JSON.",
                prompts.reflection_prompt(agent, day, day_memories),
            )
            parsed = prompts.parse_json_reply(raw)
            if parsed and parsed.get("reflection"):
                return {
                    "reflection": str(parsed["reflection"]),
                    "warmer": parsed.get("warmer") or None,
                    "colder": parsed.get("colder") or None,
                    "goal_resolved": str(parsed.get("goal_resolved")).lower()
                                     in ("true", "1", "yes"),
                }
        except requests.RequestException:
            pass
        return fallback


# Older configs and callers said "Ollama"; the class outgrew the name.
OllamaBrain = LLMBrain


# ------------------------------------------------------------------ Mock
_MOCK_RADIO_TAKES = [
    "Did you hear that? {opine}",
    "The radio says so, but I'll believe it when I see it.",
    "That's the world for you. Around here we've got real problems.",
    "Mark my words, that'll be on everyone's lips by supper.",
]

_MOCK_OPINES = [
    "I don't like the sound of it.",
    "About time, if you ask me.",
    "Nobody asked me, of course.",
    "In my day this wouldn't have made the news.",
]

_MOCK_SMALLTALK = [
    "Morning, {other}. Town's quiet today, isn't it?",
    "{other}, I've been meaning to ask you something — remind me later.",
    "You look like you've heard something, {other}. Out with it.",
    "Weather's turning. I can feel it in my knees, {other}.",
    "{other}, between you and me — {goal_frag}.",
]

_MOCK_REPLIES = [
    "Is that so.",
    "Hm. That's one way to put it.",
    "You don't say. Well — {opine}",
    "I heard different, but go on.",
]


class MockBrain(BrainBase):
    """Scripted small-town behavior with personality flavor. Deterministic
    given the world seed. Not smart, but alive enough to test everything."""

    def __init__(self, agent_name, seed):
        self.rng = random.Random(f"{seed}:{agent_name}")

    def _fill(self, template, agent, other=None):
        return template.format(
            other=(other or "neighbor").split()[0],
            opine=self.rng.choice(_MOCK_OPINES),
            goal_frag=agent.goal,
        )

    def decide(self, agent, world, perceptions, memories):
        agent.last_prompt = "(mock brain — no prompt)"
        clock = world.clock
        urgent = agent.urgent_needs()

        spoken_to = [p for p in perceptions if "said to you" in p["text"]]
        heard_radio = [p for p in perceptions if p["text"].startswith("The radio:")]

        # night: go home and sleep (the homeless take an inn bed if the
        # town has built one and they can cover it; else the park)
        if clock.is_night and not agent.asleep:
            bed = agent.home
            if bed is None:
                inns = [k for k, v in world.locations.items() if v.get("inn")]
                if inns and agent.money >= getattr(config, "INN_ROOM_COST", 5):
                    bed = inns[0]
            bed = bed or "the park"
            if agent.location != bed:
                return {"action": "move", "to": bed}, "", "mock: bedtime"
            return {"action": "rest"}, "", "mock: bedtime"

        # answer whoever addressed us
        if spoken_to:
            speaker = spoken_to[-1]["text"].split(" said to you")[0]
            reply = self._fill(self.rng.choice(_MOCK_REPLIES), agent, speaker)
            return {"action": "say", "text": reply, "to": speaker}, "", "mock: replying"

        # react to the radio
        if heard_radio and self.rng.random() < 0.8:
            take = self._fill(self.rng.choice(_MOCK_RADIO_TAKES), agent)
            return {"action": "say", "text": take}, "", "mock: radio take"

        # urgent needs
        if "fullness" in urgent:
            if world.locations.get(agent.location, {}).get("sells_food") and agent.money >= config.MEAL_COST:
                return {"action": "eat"}, "", "mock: hungry, eating out"
            if agent.home and agent.location == agent.home:
                return {"action": "eat"}, "", "mock: hungry, eating at home"
            dest = "Rosie's Diner" if agent.money >= config.MEAL_COST else (agent.home or "Rosie's Diner")
            return {"action": "move", "to": dest}, "", "mock: hungry, seeking food"
        if "energy" in urgent:
            bed = agent.home or "the park"
            if agent.location == bed:
                return {"action": "rest"}, "", "mock: exhausted"
            return {"action": "move", "to": bed}, "", "mock: exhausted, heading to rest"

        # the jobless take an open position (v2.7: showing up is the interview)
        if getattr(config, "HIRING_ENABLED", True) and \
                not agent.workplace() and hasattr(world, "open_positions"):
            openings = world.open_positions()
            if openings:
                job, wp = sorted(openings.items())[0]
                if agent.location == wp:
                    return {"action": "work"}, "", "mock: taking the job"
                if self.rng.random() < 0.4:
                    return ({"action": "move", "to": wp},
                            "", "mock: job hunting")

        # settle a debt when the creditor is standing right here (mock virtue)
        if getattr(config, "ECONOMY", False) and hasattr(world, "open_debts"):
            # the Holt Act concentrates the mind: bank-held paper gets paid
            # first, from anywhere, before the house goes
            bank = world.bank_name()
            if bank:
                held = sum(d["amount"] for d in world.open_debts(
                    debtor=agent.name, creditor=bank) if d.get("assigned_day"))
                if held > 0 and agent.money >= held and \
                        self.rng.random() < 0.6:
                    return ({"action": "pay", "to": "the bank",
                             "amount": held},
                            "", "mock: saving the house")
                # the rich buy a seized house when one is going cheap
                for house, loc in world.locations.items():
                    if loc.get("for_sale") and \
                            agent.money >= loc["for_sale"] + 15 and \
                            self.rng.random() < 0.3:
                        if agent.location == bank:
                            return ({"action": "buy", "item": house},
                                    "", "mock: buying the deed")
                        return ({"action": "move", "to": bank},
                                "", "mock: heading to the sale")
            for d in world.open_debts(debtor=agent.name):
                cred = world.agents.get(d["creditor"])
                if cred and cred.location == agent.location and \
                        not cred.asleep and agent.money >= d["amount"] > 0 and \
                        self.rng.random() < 0.5:
                    return ({"action": "pay", "to": d["creditor"],
                             "amount": d["amount"]},
                            "", "mock: settling a debt")
            # the comfortable buy themselves something, once in a while
            if hasattr(world, "goods_sold_here") and agent.money > 35 and \
                    self.rng.random() < 0.08:
                here = world.goods_sold_here(agent.location)
                afford = [k for k, v in here.items()
                          if agent.money >= v["cost"]
                          and (v.get("consumable")
                               or k not in agent.possessions)]
                if afford:
                    return ({"action": "buy", "item": afford[0]},
                            "", "mock: treating themselves")
            # the comfortable drop a five in the jar now and then
            box = getattr(config, "POOR_BOX", "the poor box")
            if box in world.tills and agent.money > 40 and \
                    world.locations.get(agent.location, {}).get("sells_food") and \
                    self.rng.random() < 0.08:
                return ({"action": "pay", "to": "the poor box", "amount": 5},
                        "", "mock: feeding the jar")
            # broke and at the bank: ask the teller
            bank = world.bank_name()
            if bank and agent.location == bank and \
                    agent.money < config.MEAL_COST:
                return ({"action": "borrow", "amount": 10},
                        "", "mock: asking for a loan")

        # work a shift in the morning if we have a job and aren't there yet
        wp = agent.workplace()
        if wp and getattr(config, "ECONOMY", False) and hasattr(world, "_wage_till_key"):
            tk = world._wage_till_key(agent)
            if world.tills.get(tk, 0.0) <= 0 and \
                    world.wage_debt_of(tk) >= config.WAGE_DEBT_CAP:
                wp = None   # no payroll there — do something else with the day
        if wp and 8 * 60 <= clock.minutes < 12 * 60 and (agent.activity or {}).get("type") != "work":
            if agent.location != wp:
                return {"action": "move", "to": wp}, "", "mock: off to work"
            return {"action": "work"}, "", "mock: starting shift"

        # pitch in on a local project sometimes
        for p in world.projects:
            if not p["complete"] and p["site"] == agent.location and self.rng.random() < 0.35:
                return {"action": "build", "project": p["name"]}, "", "mock: pitching in"

        # otherwise: social wandering
        roll = self.rng.random()
        others = world.occupants(agent.location, exclude=agent.name)
        if others and roll < 0.5:
            other = self.rng.choice(others)
            line = self._fill(self.rng.choice(_MOCK_SMALLTALK), agent, other.name)
            return {"action": "say", "text": line, "to": other.name}, "", "mock: smalltalk"
        if roll < 0.75:
            dest = self.rng.choice(world.public_locations())
            return {"action": "move", "to": dest}, "", "mock: wandering"
        return {"action": "idle", "note": f"{agent.quirk} — you can tell"}, "", "mock: idling"

    def reflect(self, agent, day, day_memories):
        n_talks = sum(1 for m in day_memories if m["kind"] == "speech")
        contacts = [n for n in agent.relationships if n]
        warmer = self.rng.choice(contacts) if contacts and self.rng.random() < 0.3 else None
        return {
            "reflection": (f"Day {day} done. Talked {n_talks} times, and I'm no "
                           f"closer on this: {agent.goal}. Tomorrow I do something "
                           f"about it."),
            "warmer": warmer, "colder": None,
            "goal_resolved": self.rng.random() < 0.12,
        }


# -------------------------------------------------------------- External
class ExternalBrain(BrainBase):
    """The possession seat. An outside mind (Brad, or a Claude session)
    posts actions into the queue via the server API; when the seat is
    empty the understudy (mock or ollama) carries the character."""

    def __init__(self, understudy):
        self.understudy = understudy
        self.queued_action = None
        self.possessed = False
        self.latest_observation = {}

    def decide(self, agent, world, perceptions, memories):
        self.latest_observation = {
            "sim_time": world.clock.label,
            "location": agent.location,
            "needs": dict(agent.needs),
            "money": agent.money,
            "perceptions": [p["text"] for p in perceptions],
            "memories": [m["text"] for m in memories],
        }
        if self.possessed and self.queued_action:
            action, self.queued_action = self.queued_action, None
            return action, "(external)", "possessed: external action"
        return self.understudy.decide(agent, world, perceptions, memories)

    def reflect(self, agent, day, day_memories):
        return self.understudy.reflect(agent, day, day_memories)


def build_brain(agent, seed):
    """Every villager gets an ExternalBrain wrapper (possession-ready)
    around their real mind."""
    mock = MockBrain(agent.name, seed)
    if config.MOCK_MODE:
        core = mock
    else:
        core = LLMBrain(agent.model, agent.host)
        core.understudy = mock
    return ExternalBrain(core)
