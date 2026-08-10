"""Villagers and random cast generation."""

import random

import config


class Agent:
    def __init__(self, name, job, traits, quirk, goal, model, host, home):
        self.name = name
        self.job = job
        self.traits = traits
        self.quirk = quirk
        self.goal = goal
        self.model = model
        self.host = host        # key into config.PROVIDERS / OLLAMA_HOSTS
        self.home = home
        self.workplace_at = None    # v3.11: set when a post is claimed
        self.phone_day = 0          # v3.11: the day this allowance belongs to
        self.phone_left = 0         # v3.11: group posts left today
        self.location = home
        self.money = 0
        self.pantry = 3     # home servings per day; restocked overnight
        self.possessions = []   # goods bought with earned money (v2.5)
        self.last_say = ""  # repetition guard
        self.recent_own_says = []   # token sets of recent lines (Paraphrase Act)
        self.last_text = ""         # repetition guard for SMS
        self.is_stranger = False    # strangers aren't in anyone's contacts yet
        self.drink_ticks = []       # ticks of recent drinks (Rusty Tap)
        self.talk_streak = 0        # consecutive talk-only decisions
        self.soapbox = 0            # consecutive speeches to an empty room
        self.needs = {k: float(v["start"]) for k, v in config.NEEDS.items()}
        self.asleep = False
        # activity: {"type": "work"|"rest"|"nap"|"idle", "until_tick": int|None, "note": str}
        self.activity = None
        self.last_decision_tick = -999
        self.pending = []           # perceptions queued since last decision
        self.urgent_flag = False    # a need crossed urgent since last decision
        self.relationships = {}     # name -> familiarity score
        # inspector breadcrumbs
        self.last_prompt = ""
        self.last_reply = ""
        self.last_source = None   # 'model' | 'understudy' | 'possessed'
        self.last_reason = ""
        self.last_action = None

    # ------------------------------------------------------------------
    def persona_line(self):
        t = ", ".join(self.traits)
        return (f"{self.name}, the town {self.job}. Traits: {t}. "
                f"Quirk: {self.quirk}. Current preoccupation: {self.goal}.")

    def workplace(self):
        # v3.11: a villager may hold a post this town INVENTED by finishing
        # something, which is not in config.WORKPLACES and never can be —
        # that dict is written before Day 1. workplace_at is set when a post
        # is claimed and is authoritative; it falls back to the town charter
        # for everyone cast into a job before they had a thought.
        return getattr(self, "workplace_at", None) or config.WORKPLACES.get(self.job)

    def urgent_needs(self):
        out = []
        for k, v in config.NEEDS.items():
            if self.needs[k] <= v["urgent_below"]:
                out.append(k)
        return out

    def to_public(self):
        bubble = None
        if self.last_action and self.last_action.get("action") == "say":
            bubble = self.last_action.get("text", "")
        return {
            "name": self.name,
            "job": self.job,
            "model": self.model,
            "location": self.location,
            "asleep": self.asleep,
            "money": round(self.money, 1),
            "needs": {k: round(v, 1) for k, v in self.needs.items()},
            "activity": (self.activity or {}).get("type"),
            "possessions": list(self.possessions),
            # WHAT WE TOLD THEM DRIVES THEM (v3.9.5).
            #
            # `agent.goal` is rendered into the SYSTEM prompt every tick —
            # "What's driving you lately: {goal}." — and it is the only
            # durable statement of what a villager wants. A villager cannot
            # set it: the two writers in the codebase are casting and
            # `rng.choice(config.GOALS)` after a nightly reflection resolves
            # one. It was never readable from outside the process, so every
            # claim about a villager's motivation was an inference from
            # behaviour back onto a list of seven strings — which is exactly
            # the reasoning that killed four claims on 9 August.
            #
            # Pure observation. Read-only. Changes nothing.
            # (claude/WHATS-DRIVING-YOU.md)
            "goal": self.goal,
        }


def generate_cast(seed=None):
    rng = random.Random(seed)
    n = config.CAST_SIZE
    archetypes = rng.sample(config.ARCHETYPES, min(n, len(config.ARCHETYPES)))
    firsts = rng.sample(config.FIRST_NAMES, n)
    lasts = rng.sample(config.LAST_NAMES, n)
    quirks = rng.sample(config.QUIRKS, n)
    goals = rng.sample(config.GOALS, n)
    cast = []
    for i in range(n):
        arch = archetypes[i % len(archetypes)]
        name = f"{firsts[i]} {lasts[i]}"
        model, host = config.MODEL_POOL[i % len(config.MODEL_POOL)]
        home = f"{firsts[i]}'s house"
        a = Agent(
            name=name, job=arch["job"], traits=list(arch["traits"]),
            quirk=quirks[i], goal=goals[i], model=model, host=host, home=home,
        )
        a.money = rng.uniform(*config.START_MONEY)
        cast.append(a)
    return cast
