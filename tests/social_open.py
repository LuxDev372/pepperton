"""World-physics proof for opening social interactions."""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-social-open-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
from sim.causality import Command
from sim.engine import Engine

config.MOCK_MODE = True
config.RADIO_ENABLED = False

def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")

engine = Engine(seed=920)
left, right = list(engine.world.agents.values())[:2]
right.location = left.location
result = engine.dispatch(Command(
    kind="action", source="test", actor=left.name,
    payload={"action": "interact", "to": right.name, "act": "request",
             "topic": "library", "proposal": {"private": "details"}}))
check("co-located request opens", result.accepted)
scene = next(iter(engine.world.interactions.values()))
check("scene records participants and next responder",
      scene.initiator == left.name and scene.target == right.name and
      scene.next_responder == right.name)
check("scene emits an authoritative event", any(
      event.get("type") == "interaction" and event.get("topic") == "library"
      for event in engine.world.events))
right.location = "the park"
rejected = engine.dispatch(Command(
    kind="action", source="test", actor=left.name,
    payload={"action": "interact", "to": right.name, "topic": "library"}))
check("separated participant is rejected", not rejected.accepted)
right.location = left.location
accepted = engine.dispatch(Command(
    kind="action", source="test", actor=right.name,
    payload={"action": "interact", "act": "accept", "scene_id": scene.id}))
check("designated responder accepts", accepted.accepted and scene.status == "accepted")
wrong = engine.dispatch(Command(
    kind="action", source="test", actor=left.name,
    payload={"action": "interact", "act": "refuse", "scene_id": scene.id}))
check("closed scene rejects a second response", not wrong.accepted)
opened = engine.dispatch(Command(
    kind="action", source="test", actor=left.name,
    payload={"action": "interact", "to": right.name, "topic": "park"}))
second = list(engine.world.interactions.values())[-1]
deferred = engine.dispatch(Command(
    kind="action", source="test", actor=right.name,
    payload={"action": "interact", "act": "defer", "scene_id": second.id}))
check("designated responder defers", opened.accepted and deferred.accepted and second.status == "deferred")
third = engine.dispatch(Command(
    kind="action", source="test", actor=left.name,
    payload={"action": "interact", "to": right.name, "topic": "garden"}))
third_scene = list(engine.world.interactions.values())[-1]
countered = engine.dispatch(Command(
    kind="action", source="test", actor=right.name,
    payload={"action": "interact", "act": "counter", "scene_id": third_scene.id,
             "proposal": {"work": "tomorrow"}}))
check("counter hands the turn back", third.accepted and countered.accepted and
      third_scene.status == "open" and third_scene.next_responder == left.name)
fourth = engine.dispatch(Command(
    kind="action", source="test", actor=left.name,
    payload={"action": "interact", "to": right.name, "topic": "library"}))
fourth_scene = list(engine.world.interactions.values())[-1]
committed = engine.dispatch(Command(
    kind="action", source="test", actor=right.name,
    payload={"action": "interact", "act": "accept", "scene_id": fourth_scene.id,
             "commitment": {"condition": "bring plans", "deadline_day": 3}}))
check("acceptance creates a durable commitment", committed.accepted and
      fourth_scene.commitment_id in engine.world.commitments and
      engine.world.commitments[fourth_scene.commitment_id].condition == "bring plans")
engine.stop()
print("Social-open proof complete")
