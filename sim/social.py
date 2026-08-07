"""Serializable records for bounded, two-party social interactions."""

from dataclasses import dataclass, field


SPEECH_ACTS = {
    "request", "offer", "inform", "ask", "accept", "refuse", "counter",
    "defer", "leave",
}
RESPONSE_STATUS = {
    "accept": "accepted", "refuse": "refused", "counter": "countered",
    "defer": "deferred", "leave": "closed",
}
MAX_INTERACTION_ROUNDS = 3


@dataclass
class Interaction:
    """One bounded interaction; policy prose never becomes domain state."""

    id: str
    initiator: str
    target: str
    location: str
    topic: str
    speech_act: str
    status: str = "open"
    response_act: str | None = None
    thread_id: str | None = None
    source_event_id: str | None = None
    last_event_id: str | None = None
    proposal: dict = field(default_factory=dict)
    commitment_id: str | None = None
    next_responder: str | None = None
    round: int = 0
    created_tick: int = 0
    updated_tick: int = 0

    def __post_init__(self):
        if self.status == "open" and self.next_responder is None:
            self.next_responder = self.target

    @classmethod
    def from_dict(cls, value):
        value = dict(value or {})
        return cls(
            id=value["id"], initiator=value["initiator"],
            target=value["target"], location=value["location"],
            topic=value.get("topic", "general"),
            speech_act=value.get("speech_act", "request"),
            status=value.get("status", "open"),
            response_act=value.get("response_act"),
            thread_id=value.get("thread_id"),
            source_event_id=value.get("source_event_id"),
            last_event_id=value.get("last_event_id"),
            proposal=dict(value.get("proposal") or {}),
            commitment_id=value.get("commitment_id"),
            next_responder=value.get("next_responder") or (
                value.get("target") if value.get("status", "open") == "open"
                else None),
            round=int(value.get("round", 0) or 0),
            created_tick=int(value.get("created_tick", 0) or 0),
            updated_tick=int(value.get("updated_tick", 0) or 0))

    def to_dict(self):
        return {
            "id": self.id, "initiator": self.initiator, "target": self.target,
            "location": self.location, "topic": self.topic,
            "speech_act": self.speech_act, "status": self.status,
            "response_act": self.response_act, "thread_id": self.thread_id,
            "source_event_id": self.source_event_id,
            "last_event_id": self.last_event_id, "proposal": dict(self.proposal),
            "commitment_id": self.commitment_id,
            "next_responder": self.next_responder, "round": self.round,
            "created_tick": self.created_tick, "updated_tick": self.updated_tick,
        }

    def to_public(self):
        """Safe projection: proposals remain private to participants."""
        return {
            "id": self.id, "initiator": self.initiator, "target": self.target,
            "location": self.location, "topic": self.topic,
            "speech_act": self.speech_act, "status": self.status,
            "response_act": self.response_act, "commitment_id": self.commitment_id,
            "next_responder": self.next_responder, "round": self.round,
            "created_tick": self.created_tick, "updated_tick": self.updated_tick,
        }


@dataclass
class Commitment:
    """An obligation that will be resolved by authoritative world facts."""

    id: str
    owner: str
    counterparty: str
    condition: str
    deadline_day: int | None = None
    status: str = "open"
    source_interaction_id: str | None = None
    source_event_id: str | None = None
    proof: dict = field(default_factory=dict)
    fulfillment_event_ids: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, value):
        value = dict(value or {})
        return cls(
            id=value["id"], owner=value["owner"],
            counterparty=value["counterparty"],
            condition=value.get("condition", ""),
            deadline_day=value.get("deadline_day"), status=value.get("status", "open"),
            source_interaction_id=value.get("source_interaction_id"),
            source_event_id=value.get("source_event_id"),
            proof=dict(value.get("proof") or {}),
            fulfillment_event_ids=list(value.get("fulfillment_event_ids") or []))

    def to_dict(self):
        return {
            "id": self.id, "owner": self.owner, "counterparty": self.counterparty,
            "condition": self.condition, "deadline_day": self.deadline_day,
            "status": self.status, "source_interaction_id": self.source_interaction_id,
            "source_event_id": self.source_event_id, "proof": dict(self.proof),
            "fulfillment_event_ids": list(self.fulfillment_event_ids),
        }

    def to_public(self):
        return {
            "id": self.id, "owner": self.owner, "counterparty": self.counterparty,
            "deadline_day": self.deadline_day, "status": self.status,
            "source_interaction_id": self.source_interaction_id,
            "fulfillment_count": len(self.fulfillment_event_ids),
        }
