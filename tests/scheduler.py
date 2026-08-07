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
from sim.scheduler import DecisionTask, PolicyScheduler, ReflectionTask

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


class DelayedExternalBrain(ExternalBrain):
    def __init__(self, understudy):
        super().__init__(understudy)
        self.started = threading.Event()
        self.release = threading.Event()

    def decide(self, *args):
        self.started.set()
        self.release.wait(1)
        return super().decide(*args)


class ReflectionGateBrain:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def decide(self, *args):
        return {"action": "idle"}, "", "idle"

    def reflect(self, *args):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            self.release.wait(1)
        return {"reflection": "retry reflection"}


class CrossPhaseBrain:
    def __init__(self):
        self.decide_started = threading.Event()
        self.release = threading.Event()
        self.reflect_started = threading.Event()

    def decide(self, *args):
        self.decide_started.set()
        self.release.wait(1)
        return {"action": "idle"}, "", "idle"

    def reflect(self, *args):
        self.reflect_started.set()
        return {"reflection": "must not overlap"}


class QueueProbeBrain:
    def __init__(self):
        self.started = threading.Event()

    def decide(self, *args):
        self.started.set()
        return {"action": "idle"}, "", "idle"

    def reflect(self, *args):
        return {"reflection": "queued reflection"}


def timeout_scenario():
    engine = Engine(seed=812)
    focus = isolate_focus(engine)
    slow = SlowBrain()
    engine.brains[focus.name] = slow
    worker = threading.Thread(target=engine.step)
    worker.start()
    check("slow policy starts", slow.started.wait(1))
    check("policy workers are daemonized", all(
        thread.daemon for thread in threading.enumerate()
        if thread.name.startswith("pepperton-policy-")))
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


def queued_possession_scenario():
    engine = Engine(seed=816)
    focus = isolate_focus(engine)
    seat = DelayedExternalBrain(engine.brains[focus.name].understudy)
    engine.brains[focus.name] = seat
    worker = threading.Thread(target=engine.step)
    worker.start()
    check("queued possession reaches seat boundary", seat.started.wait(1))
    seat.queue_action({"action": "say", "text": "queued during scheduling"})
    seat.release.set()
    worker.join(1)
    check("queued possession action is not lost", any(
        event.get("text") == "queued during scheduling" and
        event.get("source") == "possession" for event in engine.world.events))
    engine.stop()


def reflection_retry_scenario():
    engine = Engine(seed=817)
    focus = isolate_focus(engine)
    gated = ReflectionGateBrain()
    engine.brains[focus.name] = gated
    with engine.lock:
        tasks = engine._prepare_reflections(force=True)
    results = []
    worker = threading.Thread(target=lambda: results.append(engine.scheduler.reflect(tasks)))
    worker.start()
    check("reflection policy starts", gated.started.wait(1))
    engine.dispatch(Command(kind="director.event", source="manual",
                            payload={"event": "duck_omen"}))
    gated.release.set()
    worker.join(1)
    with engine.lock:
        engine._apply_reflections(tasks, results[0])
        retry_tasks = engine._prepare_reflections()
    check("stale reflections remain scheduled for retry", bool(retry_tasks))
    engine.save_state()
    restored = Engine(state=Engine.load_state())
    check("reflection retry survives checkpoint recovery",
          restored._reflection_retry_day == retry_tasks[0].day)
    restored.stop()
    retry_outcomes = engine.scheduler.reflect(retry_tasks)
    with engine.lock:
        engine._apply_reflections(retry_tasks, retry_outcomes)
    check("retried reflection is applied", any(
        event.get("type") == "reflect" and event.get("text") == "retry reflection"
        for event in engine.world.events))
    check("successful retry advances reflected day",
          engine._reflected_day == retry_tasks[0].day)
    engine.stop()


def scheduler_queue_scenarios():
    from types import SimpleNamespace

    observation = SimpleNamespace(actor=None, world=None, perceptions=(), memories=())
    scheduler = PolicyScheduler(timeout=0.01, max_workers=1)
    blocker = CrossPhaseBrain()
    decision = DecisionTask("Ada", blocker, observation, 1, 0, None, 0)
    reflection = ReflectionTask("Ada", blocker, observation, 1, 0)
    scheduler.decide([decision])
    check("cross-phase guard sees running decision", blocker.decide_started.is_set())
    outcome = scheduler.reflect([reflection])[0]
    check("reflection is busy behind timed-out decision", outcome.status == "busy")
    check("decision and reflection never overlap", not blocker.reflect_started.is_set())
    blocker.release.set()
    scheduler.close()

    scheduler = PolicyScheduler(timeout=0.01, max_workers=1)
    blocker, queued = CrossPhaseBrain(), QueueProbeBrain()
    tasks = [DecisionTask("blocker", blocker, observation, 1, 0, None, 0),
             DecisionTask("queued", queued, observation, 1, 0, None, 0)]
    outcomes = scheduler.decide(tasks)
    check("queued policy receives timeout", outcomes[1].status == "timeout")
    check("queued policy is cancelled before start", not queued.started.is_set())
    blocker.release.set()
    scheduler.close()


timeout_scenario()
stale_result_scenario()
possession_scenario()
queued_possession_scenario()
reflection_retry_scenario()
scheduler_queue_scenarios()
print("Scheduler proof complete")
shutil.rmtree(SCRATCH, ignore_errors=True)
