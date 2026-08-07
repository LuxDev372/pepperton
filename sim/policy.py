"""Immutable inputs and proposals for policy work.

The scheduler may hand these snapshots to slow model calls without exposing
the live simulation objects.  World mutation stays in :mod:`sim.engine`.
"""

from dataclasses import dataclass
from typing import Mapping, Optional

import config


class FrozenDict(dict):
    """JSON-shaped mapping that rejects mutation after a snapshot is made."""

    def _immutable(self, *args, **kwargs):
        raise TypeError("policy snapshots are immutable")

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _immutable
    __ior__ = _immutable


def freeze(value):
    if isinstance(value, FrozenDict):
        return value
    if isinstance(value, Mapping):
        return FrozenDict({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted(freeze(item) for item in value))
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ClockObservation:
    day: int
    minutes: int
    step: int

    @property
    def hhmm(self):
        return f"{self.minutes // 60:02d}:{self.minutes % 60:02d}"

    @property
    def label(self):
        return f"Day {self.day}, {self.hhmm}"

    @property
    def is_night(self):
        return self.minutes >= 22 * 60 + 30 or self.minutes < 6 * 60 + 30


@dataclass(frozen=True)
class AgentObservation:
    name: str
    job: str
    traits: tuple
    quirk: str
    goal: str
    model: str
    host: str
    home: Optional[str]
    location: str
    money: float
    pantry: int
    possessions: tuple
    drink_ticks: tuple
    talk_streak: int
    soapbox: int
    needs: FrozenDict
    asleep: bool
    activity: Optional[FrozenDict]
    relationships: FrozenDict
    is_stranger: bool

    def workplace(self):
        return config.WORKPLACES.get(self.job)

    def urgent_needs(self):
        return [name for name, spec in config.NEEDS.items()
                if self.needs.get(name, 0) <= spec["urgent_below"]]


@dataclass(frozen=True)
class WorldObservation:
    world_id: str
    tick_no: int
    clock: ClockObservation
    agents: FrozenDict
    locations: FrozenDict
    projects: tuple
    tills: FrozenDict
    debts: tuple
    promises: tuple
    goods: FrozenDict
    credit_reports: FrozenDict
    wage_debts: FrozenDict
    wage_till_keys: FrozenDict

    def occupants(self, location, exclude=None):
        return tuple(agent for agent in self.agents.values()
                     if agent.location == location and agent.name != exclude
                     and not agent.asleep)

    def public_locations(self):
        return [name for name, spec in self.locations.items()
                if "home_of" not in spec]

    def bank_name(self):
        return next((name for name, spec in self.locations.items()
                     if spec.get("bank")), None)

    def goods_catalog(self):
        return self.goods

    def goods_sold_here(self, location):
        return FrozenDict({name: spec for name, spec in self.goods.items()
                           if spec.get("sold_at") == location})

    def open_debts(self, debtor=None, creditor=None):
        return tuple(debt for debt in self.debts if debt.get("status") == "open"
                     and (debtor is None or debt.get("debtor") == debtor)
                     and (creditor is None or debt.get("creditor") == creditor))

    def open_positions(self):
        held = {agent.job for agent in self.agents.values()}
        return {job: workplace for job, workplace in config.WORKPLACES.items()
                if workplace and workplace in self.locations and job not in held}

    def credit_report(self, agent):
        return self.credit_reports.get(getattr(agent, "name", agent), (0, 0, 0))

    def wage_debt_of(self, till_key):
        return self.wage_debts.get(till_key, 0)

    def _wage_till_key(self, agent):
        return self.wage_till_keys.get(getattr(agent, "name", agent))


@dataclass(frozen=True)
class Observation:
    actor: AgentObservation
    world: WorldObservation
    perceptions: tuple
    memories: tuple


@dataclass(frozen=True)
class Decision:
    action: Optional[FrozenDict]
    raw: str = ""
    reason: str = ""
    prompt: str = ""
    reply: str = ""
    source: str = "villager"

    def __post_init__(self):
        if self.action is not None and not isinstance(self.action, FrozenDict):
            object.__setattr__(self, "action", freeze(self.action))


@dataclass(frozen=True)
class Reflection:
    reflection: str
    warmer: Optional[str] = None
    colder: Optional[str] = None
    goal_resolved: bool = False


def _agent_view(agent):
    return AgentObservation(
        name=agent.name, job=agent.job, traits=tuple(agent.traits),
        quirk=agent.quirk, goal=agent.goal, model=agent.model, host=agent.host,
        home=agent.home, location=agent.location, money=agent.money,
        pantry=agent.pantry, possessions=tuple(agent.possessions),
        drink_ticks=tuple(agent.drink_ticks), talk_streak=agent.talk_streak,
        soapbox=agent.soapbox, needs=freeze(agent.needs), asleep=agent.asleep,
        activity=freeze(agent.activity) if agent.activity else None,
        relationships=freeze(agent.relationships), is_stranger=agent.is_stranger)


def make_observation(agent, world, perceptions, memories):
    credit_reports = {}
    wage_debts = {}
    wage_till_keys = {}
    for name, other in world.agents.items():
        if hasattr(world, "credit_report"):
            credit_reports[name] = world.credit_report(other)
        if hasattr(world, "_wage_till_key"):
            key = world._wage_till_key(other)
            wage_till_keys[name] = key
            wage_debts[key] = world.wage_debt_of(key)
    world_view = WorldObservation(
        world_id=world.world_id, tick_no=world.tick_no,
        clock=ClockObservation(world.clock.day, world.clock.minutes, world.clock.step),
        agents=freeze({name: _agent_view(other) for name, other in world.agents.items()}),
        locations=freeze(world.locations), projects=tuple(freeze(p) for p in world.projects),
        tills=freeze(world.tills), debts=tuple(freeze(d) for d in world.debts),
        promises=tuple(freeze(p) for p in world.promises),
        goods=freeze(world.goods_catalog()), credit_reports=freeze(credit_reports),
        wage_debts=freeze(wage_debts), wage_till_keys=freeze(wage_till_keys))
    memory_views = tuple(freeze({
        "day": item.get("day", 0), "sim_time": item.get("sim_time", ""),
        "kind": item.get("kind", ""), "text": item.get("text", ""),
        "importance": item.get("importance", 0),
    }) for item in memories)
    return Observation(_agent_view(agent), world_view,
                       tuple(freeze(item) for item in perceptions), memory_views)
