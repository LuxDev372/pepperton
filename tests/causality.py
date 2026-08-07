"""Focused causal-envelope proof; run from the project root."""

import atexit
import os
import shutil
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-causality-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)

import config
from sim.causality import Command
from sim.engine import Engine
from sim.world import World


config.MOCK_MODE = True
config.RADIO_ENABLED = False
config.CHAOS["enabled"] = False


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


engine = Engine(seed=101)
actor = next(agent for agent in engine.world.agents.values() if not agent.asleep)
result = engine.world.apply_command(Command(
    kind="action",
    source="villager",
    actor=actor.name,
    payload={"action": "idle", "note": "a causal test action"},
))
event = next(event for event in engine.world.events
             if event["event_id"] == result.event_ids[0])
check("action commands are accepted", result.accepted)
check("action commands receive stable identities",
      result.command_id == f"{engine.world.world_id}:command:1" and
      event["event_id"] == f"{engine.world.world_id}:event:{event['seq']}")
check("action events retain provenance",
      event["source"] == "villager" and
      event["command_id"] == result.command_id and
      event["topic"] == "idle")

unknown_actor = engine.world.apply_command(Command(
    kind="action", source="manual", actor="Nobody", payload={}))
unknown_kind = engine.world.apply_command(Command(
    kind="director.event", source="manual", payload={}))
check("world rejects unknown command actors", not unknown_actor.accepted)
check("world rejects commands it does not own", not unknown_kind.accepted)

direct = engine.world.emit("world", None, "a recorded fact", "the plaza",
                           deliver=False)
check("non-command events retain a durable envelope",
      direct["event_id"] and direct["source"] == "world" and
      direct["command_id"] is None)

engine.save_state()
saved_command_seq = engine.world._command_seq
restored = Engine(state=Engine.load_state())
continued = restored.world.apply_command(Command(
    kind="action", source="villager", actor=actor.name,
    payload={"action": "idle", "note": "after restore"},
))
check("checkpoint restore preserves the command sequence",
      continued.command_id ==
      f"{restored.world.world_id}:command:{saved_command_seq + 1}")

World.close_all()
print("Causality proof complete")
