"""Bounded concurrent policy calls over immutable observations."""

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import threading

from sim.policy import Decision, Reflection


@dataclass(frozen=True)
class DecisionTask:
    agent_name: str
    brain: object
    observation: object
    tick_no: int
    previous_decision_tick: int
    seat_state: object
    mutation_seq: int


@dataclass(frozen=True)
class ReflectionTask:
    agent_name: str
    brain: object
    observation: object
    day: int
    mutation_seq: int


@dataclass(frozen=True)
class PolicyOutcome:
    value: object
    status: str = "ok"
    detail: str = ""


class PolicyScheduler:
    """Run policy concurrently; the Engine alone applies its proposals."""

    def __init__(self, timeout, max_workers):
        self.timeout = max(0.0, float(timeout))
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="pepperton-policy")
        self._lock = threading.Lock()
        self._inflight = {}
        self._closed = False

    @staticmethod
    def _decide(task):
        result = task.brain.decide(
            task.observation.actor, task.observation.world,
            task.observation.perceptions, task.observation.memories)
        if isinstance(result, Decision):
            return result
        action, raw, reason = result
        return Decision(action, raw=raw, reason=reason)

    @staticmethod
    def _reflect(task):
        raw = task.brain.reflect(
            task.observation.actor, task.day, task.observation.memories)
        if isinstance(raw, str):
            raw = {"reflection": raw}
        return Reflection(
            reflection=str(raw.get("reflection", "")),
            warmer=raw.get("warmer") or None,
            colder=raw.get("colder") or None,
            goal_resolved=bool(raw.get("goal_resolved")))

    def _submit(self, key, fn, task):
        with self._lock:
            if self._closed:
                return None, "closed"
            current = self._inflight.get(key)
            if current is not None and not current.done():
                return None, "busy"
            future = self._executor.submit(fn, task)
            self._inflight[key] = future
            return future, "submitted"

    def _collect(self, jobs, expected_type, fallback):
        futures = [future for _, future, _, _ in jobs if future is not None]
        done, _ = wait(futures, timeout=self.timeout) if futures else (set(), set())
        outcomes = {}
        for key, future, task, status in jobs:
            if future is None:
                detail = ("policy call already in flight" if status == "busy"
                          else "scheduler is shut down")
                outcomes[key] = PolicyOutcome(fallback(status, detail), status, detail)
                continue
            if future not in done:
                detail = f"after {self.timeout:.3f}s"
                outcomes[key] = PolicyOutcome(fallback("timeout", detail), "timeout", detail)
                continue
            try:
                value = future.result()
                if not isinstance(value, expected_type):
                    raise TypeError(f"policy returned {type(value).__name__}")
            except Exception as exc:
                detail = str(exc)[:160] or type(exc).__name__
                outcomes[key] = PolicyOutcome(fallback("failed", detail), "failed", detail)
            else:
                outcomes[key] = PolicyOutcome(value)
            with self._lock:
                if self._inflight.get(key) is future:
                    del self._inflight[key]
        return outcomes

    def decide(self, tasks):
        jobs = []
        for task in tasks:
            key = ("decide", task.agent_name)
            future, status = self._submit(key, self._decide, task)
            jobs.append((key, future, task, status))
        def fallback(status, detail):
            return Decision({"action": "idle", "note": f"policy {status}: {detail}"},
                            reason=f"policy {status}; fallback idle")
        outcomes = self._collect(jobs, Decision, fallback)
        return [outcomes[("decide", task.agent_name)] for task in tasks]

    def reflect(self, tasks):
        jobs = []
        for task in tasks:
            key = ("reflect", task.agent_name)
            future, status = self._submit(key, self._reflect, task)
            jobs.append((key, future, task, status))
        def fallback(status, detail):
            return Reflection(f"Policy {status}; the day remains unfinished: {detail}")
        outcomes = self._collect(jobs, Reflection, fallback)
        return [outcomes[("reflect", task.agent_name)] for task in tasks]

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
