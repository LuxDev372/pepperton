"""Focused proof for Director and API control command migration."""

import atexit
import os
import shutil
import sys
import tempfile


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-command-controls-")
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


engine = Engine(seed=401)
result = engine.director.trigger_command("duck_omen", source="manual")
event = next(event for event in engine.world.events
             if event["event_id"] == result.event_ids[0])
check("Director commands return an accepted causal result", result.accepted)
check("Director events retain manual command provenance",
      event["source"] == "manual" and
      event["command_id"] == result.command_id and
      event["topic"] == "duck_omen" and
      event["thread_id"] == "thread:duck_omen")

unknown = engine.director.trigger_command("not-an-event", source="manual")
check("unknown Director commands are rejected", not unknown.accepted)

queued = Command(kind="director.event", source="manual",
                 payload={"event": "dead_air"})
engine.submit(queued, "typed test event")
engine._drain_commands()
check("queued typed commands retain their result",
      engine._command_results[-1].accepted and
      engine._command_results[-1].event_ids)

agent = next(iter(engine.world.agents.values()))
recast = engine.dispatch(Command(
    kind="recast", source="manual",
    payload={"agent": agent.name, "model": agent.model, "host": agent.host},
))
check("recasts use the same command boundary",
      recast.accepted and recast.event_ids and
      engine.world.events[-1]["command_id"] == recast.command_id)

World.close_all()
print("Command controls proof complete")
