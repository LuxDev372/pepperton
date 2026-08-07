"""Serializable contracts for commands and their causal results."""

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Command:
    """A proposed state change from a named source."""

    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    actor: Optional[str] = None
    cause_event_id: Optional[str] = None
    thread_id: Optional[str] = None
    salience: str = "normal"
    command_id: Optional[str] = None
    topic: Optional[str] = None


@dataclass(frozen=True)
class DomainEvent:
    """The durable event envelope shared by transcript and perceptions."""

    wid: str
    seq: int
    event_id: str
    tick: int
    day: int
    sim_time: str
    type: str
    agent: Optional[str]
    location: str
    target: Optional[str]
    text: str
    source: str
    cause_event_id: Optional[str]
    command_id: Optional[str]
    salience: str
    thread_id: Optional[str]
    topic: Optional[str]

    def as_dict(self):
        return asdict(self)


@dataclass
class CommandResult:
    """The authoritative outcome of applying a command."""

    accepted: bool
    summary: str
    command_id: str
    event_ids: List[str] = field(default_factory=list)
