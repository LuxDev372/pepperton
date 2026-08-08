"""Focused TownStore proof; run from the project root with:
    python tests/store.py
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-store-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
from sim.engine import Engine
from sim.memory import MemoryStore
from sim.store import (LegacyProjectionConflictError, SCHEMA_VERSION,
                       TownStore, UnsupportedSchemaError)
from sim.world import World

config.MOCK_MODE = True
config.RADIO_ENABLED = False
config.CHAOS["enabled"] = False


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError(f"{label}: {detail}")
    print(f"PASS {label}")


# Checkpoint migration is additive, durable, and fails closed on unknown data.
legacy = {"seed": 1, "tick_no": 0}
migrated = TownStore.migrate_checkpoint(legacy)
second = TownStore.migrate_checkpoint(legacy)
check("legacy checkpoints gain a schema and isolated identity",
      migrated["schema_version"] == SCHEMA_VERSION and
      migrated["world_id"] != "legacy" and
      migrated["world_id"] == second["world_id"] and
      migrated["_legacy_identity_pending"] and
      "schema_version" not in legacy)
other_root = TownStore.migrate_checkpoint(
    legacy, identity_path=os.path.join(SCRATCH, "other", "state.json"))
check("legacy identity is stable per persistence root",
      migrated["world_id"] != other_root["world_id"])

try:
    TownStore.migrate_checkpoint({"schema_version": SCHEMA_VERSION + 1})
except UnsupportedSchemaError:
    future_rejected = True
else:
    future_rejected = False
check("future checkpoint schemas fail closed", future_rejected)

state_path = os.path.join(SCRATCH, "state.json")
checkpoint_store = TownStore(
    world_id="checkpoint-town", state_path=state_path,
    db_path=os.path.join(SCRATCH, "checkpoint.db"),
    transcript_jsonl=os.path.join(SCRATCH, "checkpoint.jsonl"),
    transcript_log=os.path.join(SCRATCH, "checkpoint.log"))
checkpoint_store.save_checkpoint({"seed": 3, "world_id": "checkpoint-town"})
loaded = checkpoint_store.load_checkpoint()
check("checkpoint writes are versioned and readable",
      loaded["seed"] == 3 and loaded["schema_version"] == SCHEMA_VERSION)
original_fsync_parent = TownStore._fsync_parent
TownStore._fsync_parent = staticmethod(
    lambda path: (_ for _ in ()).throw(OSError("durability barrier failed")))
try:
    checkpoint_store.save_checkpoint(
        {"seed": 4, "world_id": "checkpoint-town"})
except OSError:
    directory_fsync_rejected = True
else:
    directory_fsync_rejected = False
finally:
    TownStore._fsync_parent = staticmethod(original_fsync_parent)
check("checkpoint directory durability failures propagate",
      directory_fsync_rejected)

future_path = os.path.join(SCRATCH, "future.json")
with open(future_path, "w", encoding="utf-8") as handle:
    json.dump({"schema_version": SCHEMA_VERSION + 1}, handle)
try:
    TownStore.load_checkpoint_file(future_path)
except UnsupportedSchemaError:
    future_file_rejected = True
else:
    future_file_rejected = False
check("startup does not replace newer checkpoints", future_file_rejected)
corrupt_path = os.path.join(SCRATCH, "corrupt.json")
with open(corrupt_path, "w", encoding="utf-8") as handle:
    handle.write("{truncated")
try:
    TownStore.load_checkpoint_file(corrupt_path)
except ValueError:
    corrupt_rejected = True
else:
    corrupt_rejected = False
check("corrupt checkpoints fail closed", corrupt_rejected)
unreadable_path = os.path.join(SCRATCH, "checkpoint-directory")
os.makedirs(unreadable_path)
try:
    TownStore.load_checkpoint_file(unreadable_path)
except OSError:
    unreadable_rejected = True
else:
    unreadable_rejected = False
check("checkpoint read errors fail closed", unreadable_rejected)
checkpoint_store.close()


# Memory and event recovery are scoped to one durable town identity.
memory_db = os.path.join(SCRATCH, "memory.db")
town_a = MemoryStore(db_path=memory_db, world_id="town-a")
town_b = MemoryStore(db_path=memory_db, world_id="town-b")
town_a.add("Ada", 1, 1, "08:00", "event", "old history", 1)
memory_watermark = town_a.high_watermark()
town_a.add("Ada", 1, 1, "08:15", "event", "abandoned future", 1)
town_b.add("Ada", 99, 1, "08:00", "event", "other town", 1)
town_a.delete_after(1, memory_id=memory_watermark)
check("memory rewind preserves prior history",
      [m["text"] for m in town_a.recent("Ada", 20)] == ["old history"])
check("memory rewind cannot delete another town",
      [m["text"] for m in town_b.recent("Ada", 20)] == ["other town"])
town_a.close()
town_b.close()

event_store = TownStore(
    world_id="event-town", state_path=os.path.join(SCRATCH, "events-state.json"),
    db_path=os.path.join(SCRATCH, "events.db"),
    transcript_jsonl=os.path.join(SCRATCH, "events.jsonl"),
    transcript_log=os.path.join(SCRATCH, "events.log"))
for seq, text in ((1, "keep me"), (2, "drop me")):
    event_store.append_event({
        "wid": "event-town", "seq": seq, "tick": 1, "day": 1,
        "sim_time": "08:00", "type": "event", "agent": "Ada",
        "location": "the plaza", "text": text,
    })
dropped = event_store.rewind_event_tail(1, 1)
with open(event_store.transcript_log, encoding="utf-8") as handle:
    rebuilt_log = handle.read()
check("event rewind drops the same-tick abandoned tail",
      dropped == 1 and
      [e["text"] for e in event_store.read_events()] == ["keep me"])
check("event rewind rebuilds the derived transcript",
      "keep me" in rebuilt_log and "drop me" not in rebuilt_log)
event_store.close()

missing_store = TownStore(
    world_id="missing-town", state_path=os.path.join(SCRATCH, "missing-state.json"),
    db_path=os.path.join(SCRATCH, "missing.db"),
    transcript_jsonl=os.path.join(SCRATCH, "missing.jsonl"),
    transcript_log=os.path.join(SCRATCH, "missing.log"))
check("a missing event stream has a consistent empty rewind",
      missing_store.rewind_event_tail(0, 0) == 0)
try:
    missing_store.rewind_event_tail(0, 1)
except FileNotFoundError:
    missing_expected_rejected = True
else:
    missing_expected_rejected = False
check("a missing expected event stream fails closed",
      missing_expected_rejected)
missing_store.transcript_jsonl = unreadable_path
try:
    missing_store.read_events()
except OSError:
    event_read_rejected = True
else:
    event_read_rejected = False
check("event stream read errors fail closed", event_read_rejected)
missing_store.close()

concurrent_store = TownStore(
    world_id="concurrent-town",
    state_path=os.path.join(SCRATCH, "concurrent-state.json"),
    db_path=os.path.join(SCRATCH, "concurrent.db"),
    transcript_jsonl=os.path.join(SCRATCH, "concurrent.jsonl"),
    transcript_log=os.path.join(SCRATCH, "concurrent.log"))
concurrent_world = World([], world_id="concurrent-town", store=concurrent_store)
workers = [threading.Thread(
    target=concurrent_world.emit,
    args=("event", None, f"event {i}", "the plaza"),
    kwargs={"deliver": False}) for i in range(20)]
for worker in workers:
    worker.start()
for worker in workers:
    worker.join()
persisted_sequences = [e["seq"] for e in concurrent_store.read_events()]
check("concurrent emits preserve allocated event order",
      persisted_sequences == list(range(1, 21)))
original_append = concurrent_store.append_event_with_log
concurrent_store.append_event_with_log = (
    lambda event, line: (_ for _ in ()).throw(OSError("append failed")))
try:
    concurrent_world.emit(
        "event", None, "must not become a ghost", "the plaza", deliver=False)
except OSError:
    append_rejected = True
else:
    append_rejected = False
finally:
    concurrent_store.append_event_with_log = original_append
check("failed event appends never reuse a possibly durable sequence",
      append_rejected and concurrent_world._event_seq == 21 and
      len(concurrent_world.events) == 20)
concurrent_world.close()


# Ambiguous pre-TownStore projections may be claimed once, never shared.
legacy_db = os.path.join(SCRATCH, "legacy.db")
legacy_memory = MemoryStore(db_path=legacy_db, world_id="legacy")
legacy_memory.add("Ada", 1, 1, "08:00", "event", "old memory", 1)
legacy_paths = {
    "state_path": os.path.join(SCRATCH, "legacy-state.json"),
    "db_path": legacy_db,
    "transcript_jsonl": os.path.join(SCRATCH, "legacy.jsonl"),
    "transcript_log": os.path.join(SCRATCH, "legacy.log"),
}
claimed = TownStore(world_id="claimed-town", **legacy_paths)
claimed.append_event({
    "seq": 1, "tick": 0, "day": 1, "sim_time": "08:00",
    "type": "event", "agent": "Ada", "location": "the plaza",
    "text": "old event",
})
claimed.migrate_legacy_projections()
check("legacy projections are rebound to one migrated town",
      claimed.memory.recent("Ada", 10)[0]["text"] == "old memory" and
      claimed.read_events("claimed-town")[0]["wid"] == "claimed-town")
second_claim = TownStore(world_id="second-town", **legacy_paths)
try:
    second_claim.migrate_legacy_projections()
except LegacyProjectionConflictError:
    conflict_rejected = True
else:
    conflict_rejected = False
check("a second legacy projection claim fails closed", conflict_rejected)
claimed.close()
second_claim.close()
legacy_memory.close()


# Engine integration uses the store for checkpointing and exact-tail recovery.
engine = Engine(seed=202)
engine.world.tick_no = 1
engine.save_state()
checkpoint_seq = engine.world._event_seq
abandoned = engine.world.emit(
    "event", None, "ABANDONED EVENT", "the plaza", deliver=False)
agent = next(iter(engine.world.agents.values()))
engine.memory.add(
    agent.name, 1, 1, "08:15", "event", "ABANDONED MEMORY", 5)
restored = Engine(state=Engine.load_state())
check("engine checkpoints are owned by TownStore",
      Engine.load_state()["schema_version"] == SCHEMA_VERSION)
check("restore removes events after the checkpoint sequence",
      restored.world._event_seq == checkpoint_seq + 1 and
      not any(e["text"] == "ABANDONED EVENT" for e in restored.world.events))
check("restore removes memories from the abandoned event tail",
      not any(m["text"] == "ABANDONED MEMORY"
              for m in restored.memory.recent(agent.name, 50)))

World.close_all()
check("world lifecycle closes durable stores",
      engine.world._closed and restored.world._closed)

background = Engine(seed=303)
config.PACING[config.PACING_MODE]["real_seconds_per_tick"] = 60
background.start_background()
background.stop()
check("engine shutdown joins its worker before closing the store",
      not background._thread.is_alive() and background.world._closed)

failed_background = Engine(seed=304)


def fail_persistence():
    raise OSError("simulated transcript failure")


failed_background.step = fail_persistence
failed_background.start_background()
failed_background._thread.join(timeout=2)
check("background persistence failures stop the town",
      not failed_background.running and
      not failed_background._thread.is_alive() and
      failed_background.world._closed)
failed_background.stop()

migration_state = Engine.load_state()
migration_state["world_id"] = "legacy"
migration_state.pop("schema_version", None)
original_migrate_projections = TownStore.migrate_legacy_projections
original_store_close = TownStore.close
closed_failed_stores = []


def fail_migration(store):
    raise RuntimeError("simulated migration failure")


def track_store_close(store):
    closed_failed_stores.append(store)
    original_store_close(store)


TownStore.migrate_legacy_projections = fail_migration
TownStore.close = track_store_close
try:
    Engine(state=migration_state)
except RuntimeError:
    migration_failed = True
else:
    migration_failed = False
finally:
    TownStore.migrate_legacy_projections = original_migrate_projections
    TownStore.close = original_store_close
check("failed bootstrap migration closes its SQLite store",
      migration_failed and len(closed_failed_stores) == 1)


# ---- v3.6.2: orphaned temp files. (Found by review.) ----
import ast as _ast
import glob as _glob
from sim.store import TMP_PREFIXES

_dir = os.path.dirname(getattr(config, "STATE_PATH", "data/world_state.json")) or "."
os.makedirs(_dir, exist_ok=True)
_orphans = [os.path.join(_dir, p + "a7f3k2" + ".tmp") for p in TMP_PREFIXES]
for _p in _orphans:
    with open(_p, "w") as _f:
        _f.write("half a checkpoint, killed mid-write")
_bystander = os.path.join(_dir, "world_state.json")
_had_bystander = os.path.exists(_bystander)

_store = TownStore(world_id="sweeptest")
check("a kill mid-atomic-write leaves nothing behind",
      not any(os.path.exists(p) for p in _orphans))
check("...and the sweep does not eat the live checkpoint",
      os.path.exists(_bystander) == _had_bystander)
_store.close()

# The sweep matches by prefix, and mkstemp names are random — so a new
# atomic-write call site whose prefix nobody registered leaves orphans that
# NOTHING will ever match. Read the parse tree and make that impossible.
_src = open(os.path.join(ROOT, "sim", "store.py"), encoding="utf-8").read()
_used = set()
for _node in _ast.walk(_ast.parse(_src)):
    if (isinstance(_node, _ast.Call)
            and isinstance(_node.func, _ast.Attribute)
            and _node.func.attr == "mkstemp"):
        for _kw in _node.keywords:
            if _kw.arg == "prefix" and isinstance(_kw.value, _ast.Constant):
                _used.add(_kw.value.value)
check("every atomic-write prefix in store.py is registered for sweeping",
      _used and _used <= set(TMP_PREFIXES),
      f"unregistered: {sorted(_used - set(TMP_PREFIXES))}")


# ---- v3.6.2: a dead town must not read as "running". (Found by review.) ----
# The ledger exists so a run cannot lie about itself afterward. Until now
# only stop() closed it — and stop() is reached solely via the server's
# clean-shutdown handler, which calls save_state() FIRST. A persistent disk
# fault therefore raised again on the way out, the exception was swallowed,
# and experiments.json said "running" for a town that was dead. Forever.
import sim.experiment as _expmod

_closed_with = []
_orig_close = _expmod.ExperimentLedger.close


def _spy_close(self, engine, reason="stopped"):
    _closed_with.append(reason)
    return _orig_close(self, engine, reason)


_expmod.ExperimentLedger.close = _spy_close
try:
    _e = Engine(seed=11)
    _e.running = True

    # the fatal-persistence path, taken directly
    _e._close_books("died: persistence failure")
    check("a town killed by a disk fault closes its own books",
          _closed_with == ["died: persistence failure"],
          str(_closed_with))

    # and the books are shut exactly once, however many paths hit them
    _e._close_books("stopped")
    _e.stop()
    check("...and the books are never closed twice",
          _closed_with == ["died: persistence failure"], str(_closed_with))

    # closing the books must never mask the failure that is closing them
    _expmod.ExperimentLedger.close = lambda s, e, reason="stopped": (
        _ for _ in ()).throw(OSError("ledger disk is gone too"))
    _e2 = Engine(seed=12)
    _e2._close_books("died: persistence failure")
    check("a ledger that cannot be written does not raise over the crash",
          True)
    _e2.world.close()
finally:
    _expmod.ExperimentLedger.close = _orig_close

print("TownStore proof complete")
