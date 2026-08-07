"""Proofs for the scheduler's lock, timeout, and stale-result boundary."""

import atexit
import os
import shutil
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-scheduler-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
from sim.brains import ExternalBrain
from sim.causality import Command
from sim.engine import Engine

config.MOCK_MODE = True
config.RADIO_ENABLED = False
config.CHAOS["enabled"] = False
config.ECONOMY = False
config.POLICY_DECISION_TIMEOUT = 0.03
config.POLICY_MAX_WORKERS = 4


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"PASS {label}")


def isolate_focus(engine):
    focus = next(iter(engine.world.agents.values()))
    for agent in engine.world.agents.values():
        if agent is not focus:
            agent.asleep = True
            agent.needs["energy"] = 10
    focus.location = focus.home
    return focus


class SlowBrain:
    def __init__(self, delay=0.2):
        self.delay = delay
        self.started = threading.Event()
        self.finished = threading.Event()

    def decide(self, agent, world, perceptions, memories):
        self.started.set()
        time.sleep(self.delay)
        self.finished.set()
        return {"action": "say", "text": "late policy"}, "", "slow policy"

    def reflect(self, agent, day, day_memories):
        return {"reflection": "slow reflection"}


class GatedBrain:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()

    def decide(self, agent, world, perceptions, memories):
        self.started.set()
        self.release.wait(1)
        return {"action": "say", "text": "stale policy"}, "", "gated policy"

    def reflect(self, agent, day, day_memories):
        return {"reflection": "gated reflection"}


def timeout_scenario():
    engine = Engine(seed=812)
    focus = isolate_focus(engine)
    slow = SlowBrain()
    engine.brains[focus.name] = slow
    worker = threading.Thread(target=engine.step)
    worker.start()
    check("slow policy starts", slow.started.wait(1))
    started = time.monotonic()
    engine.snapshot()
    elapsed = time.monotonic() - started
    worker.join(1)
    check("tick completes despite slow policy", not worker.is_alive())
    check("snapshot remains responsive during policy call", elapsed < 0.05,
          f"snapshot took {elapsed:.3f}s")
    check("timeout is represented in event stream", any(
        event.get("type") == "policy" and event.get("agent") == focus.name and
        "timeout" in event.get("text", "") for event in engine.world.events))
    focus.activity = None
    engine.step()
    check("in-flight policy is not duplicated", any(
        event.get("type") == "policy" and event.get("agent") == focus.name and
        "busy" in event.get("text", "") for event in engine.world.events))
    slow.finished.wait(1)
    check("late policy result cannot land", not any(
        event.get("text") == "late policy" for event in engine.world.events))
    engine.stop()


def stale_result_scenario():
    engine = Engine(seed=813)
    focus = isolate_focus(engine)
    gated = GatedBrain()
    engine.brains[focus.name] = gated
    worker = threading.Thread(target=engine.step)
    worker.start()
    check("gated policy starts", gated.started.wait(1))
    engine.dispatch(Command(kind="director.event", source="manual",
                            payload={"event": "duck_omen"}))
    gated.release.set()
    worker.join(1)
    check("stale-result tick completes", not worker.is_alive())
    check("direct command discards stale decision", not any(
        event.get("text") == "stale policy" for event in engine.world.events))
    check("discard is visible", any(
        event.get("type") == "policy" and event.get("agent") == focus.name and
        "stale" in event.get("text", "") for event in engine.world.events))
    engine.stop()


def possession_scenario():
    engine = Engine(seed=814)
    focus = isolate_focus(engine)
    gated = GatedBrain()
    seat = ExternalBrain(gated)
    engine.brains[focus.name] = seat
    worker = threading.Thread(target=engine.step)
    worker.start()
    check("seat policy starts", gated.started.wait(1))
    seat.set_possessed(True)
    gated.release.set()
    worker.join(1)
    check("seat change discards stale decision", not any(
        event.get("text") == "stale policy" for event in engine.world.events))
    engine.stop()


timeout_scenario()
stale_result_scenario()
possession_scenario()
print("Scheduler proof complete")
shutil.rmtree(SCRATCH, ignore_errors=True)
