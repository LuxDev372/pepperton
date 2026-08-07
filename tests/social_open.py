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
engine.stop()
print("Social-open proof complete")
