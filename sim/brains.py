"""Villager minds.

Three species:
  OllamaBrain   — a real local LLM, per-agent model + host (the point).
  MockBrain     — scripted, deterministic, GPU-free; keeps the whole sim
                  testable end-to-end and doubles as the fallback when a
                  model host is unreachable.
  ExternalBrain — the possession seat. A queue an outside mind (Brad at a
                  console, or a Claude session) can drive; falls back to
                  its understudy brain when the seat is empty. The town
                  never knows which citizens are possessed.
"""

import random

import requests

import config
from sim import prompts


class BrainBase:
    def decide(self, agent, world, perceptions, memories):
        raise NotImplementedError

    def reflect(self, agent, day, day_memories):
        raise NotImplementedError


# ---------------------------------------------------------------- Ollama
class OllamaBrain(BrainBase):
    def __init__(self, model, host_key="default"):
        self.model = model
        self.url = config.OLLAMA_HOSTS.get(host_key, config.OLLAMA_HOSTS["default"])
        self.understudy = None   # set by engine: MockBrain fallback

    def _chat(self, system, user):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": "json",
            "stream": False,
            "keep_alive": config.OLLAMA_KEEP_ALIVE,
            "options": config.OLLAMA_OPTIONS,
        }
        # qwen3-family models deliberate at length in hidden thinking mode,
        # which makes those villagers geologically slow. Turn it off.
        if "qwen3" in self.model.lower():
            payload["think"] = False
        r = requests.post(
            f"{self.url}/api/chat", json=payload, timeout=config.OLLAMA_TIMEOUT,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    def decide(self, agent, world, perceptions, memories):
        system = prompts.system_prompt(agent, world)
        user = prompts.decision_prompt(agent, world, perceptions, memories)
        agent.last_prompt = system + "\n\n---\n\n" + user
        try:
            raw = self._chat(system, user)
        except requests.RequestException as e:
            agent.last_reply = f"(ollama error: {e})"
            if self.understudy:
                action, _, reason = self.understudy.decide(agent, world, perceptions, memories)
                return action, agent.last_reply, f"host unreachable; understudy acted: {reason}"
            return {"action": "idle", "note": "stares into the middle distance"}, agent.last_reply, "host unreachable"
        agent.last_reply = raw
        action = prompts.parse_json_reply(raw)
        if not isinstance(action, dict) or "action" not in action:
            return {"action": "idle", "note": "mutters something unintelligible"}, raw, "unparseable reply"
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

        # settle a debt when the creditor is standing right here (mock virtue)
        if getattr(config, "ECONOMY", False) and hasattr(world, "open_debts"):
            for d in world.open_debts(debtor=agent.name):
                cred = world.agents.get(d["creditor"])
                if cred and cred.location == agent.location and \
                        not cred.asleep and agent.money >= d["amount"] > 0 and \
                        self.rng.random() < 0.5:
                    return ({"action": "pay", "to": d["creditor"],
                             "amount": d["amount"]},
                            "", "mock: settling a debt")
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
        core = OllamaBrain(agent.model, agent.host)
        core.understudy = mock
    return ExternalBrain(core)
