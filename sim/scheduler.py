"""Bounded concurrent policy calls over immutable observations."""

from concurrent.futures import Future, wait
from dataclasses import dataclass
from queue import Empty, Queue
import threading
import time

from sim.policy import Decision, Reflection


class _DaemonExecutor:
    """Small executor whose slow provider calls cannot block shutdown."""

    def __init__(self, max_workers, thread_name_prefix):
        self._queue = Queue()
        self._lock = threading.Lock()
        self._closed = False
        self._threads = []
        for index in range(max_workers):
            worker = threading.Thread(
                target=self._worker, name=f"{thread_name_prefix}-{index}", daemon=True)
            worker.start()
            self._threads.append(worker)

    def submit(self, fn, value):
        future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("scheduler is shut down")
            self._queue.put((future, fn, value))
        return future

    def _worker(self):
        while True:
            item = self._queue.get()
            if item is None:
                return
            future, fn, value = item
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn(value))
            except BaseException as exc:
                future.set_exception(exc)

    def shutdown(self, wait=False, cancel_futures=True):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if cancel_futures:
                while True:
                    try:
                        future, _, _ = self._queue.get_nowait()
                    except Empty:
                        break
                    future.cancel()
            for _ in self._threads:
                self._queue.put(None)
        if wait:
            for worker in self._threads:
                worker.join()


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
        self._executor = _DaemonExecutor(
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

    def _collect(self, jobs, expected_type, fallback, cancel_event=None):
        futures = [future for _, future, _, _ in jobs if future is not None]
        done = set()
        if futures:
            deadline = time.monotonic() + self.timeout
            remaining = set(futures)
            # A provider request already running in another thread cannot be
            # forcibly stopped safely. Polling lets the engine stop waiting
            # for it, though, and prevents its result from changing the town
            # after an operator has asked to pause.
            while remaining and not (cancel_event and cancel_event.is_set()):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                complete, remaining = wait(remaining, timeout=min(left, 0.05))
                done.update(complete)
        outcomes = {}
        for key, future, task, status in jobs:
            if future is None:
                detail = ("policy call already in flight" if status == "busy"
                          else "scheduler is shut down")
                outcomes[key] = PolicyOutcome(fallback(status, detail), status, detail)
                continue
            if future not in done:
                cancelled = bool(cancel_event and cancel_event.is_set())
                status = "paused" if cancelled else "timeout"
                detail = "pause requested" if cancelled else f"after {self.timeout:.3f}s"
                outcomes[key] = PolicyOutcome(fallback(status, detail), status, detail)
                if future.cancel():
                    with self._lock:
                        if self._inflight.get(key) is future:
                            del self._inflight[key]
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

    def decide(self, tasks, cancel_event=None):
        jobs = []
        for task in tasks:
            key = task.agent_name
            future, status = self._submit(key, self._decide, task)
            jobs.append((key, future, task, status))
        def fallback(status, detail):
            return Decision({"action": "idle", "note": f"policy {status}: {detail}"},
                            reason=f"policy {status}; fallback idle")
        outcomes = self._collect(jobs, Decision, fallback, cancel_event)
        return [outcomes[task.agent_name] for task in tasks]

    def reflect(self, tasks, cancel_event=None):
        jobs = []
        for task in tasks:
            key = task.agent_name
            future, status = self._submit(key, self._reflect, task)
            jobs.append((key, future, task, status))
        def fallback(status, detail):
            return Reflection(f"Policy {status}; the day remains unfinished: {detail}")
        outcomes = self._collect(jobs, Reflection, fallback, cancel_event)
        return [outcomes[task.agent_name] for task in tasks]

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=False, cancel_futures=True)
