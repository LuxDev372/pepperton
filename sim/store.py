"""Durable town state and append-only projections.

TownStore is the persistence boundary for the simulation.  It deliberately
keeps the first migration small: the existing JSON checkpoint, SQLite memory
database, JSONL event stream, and human-readable transcript retain their file
formats, while one object owns their paths, schema version, and recovery
operations.
"""

import json
import os
import tempfile
import threading
import uuid

import config
from sim.memory import MemoryStore


SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = "data/world_state.json"


class UnsupportedSchemaError(ValueError):
    """Raised when a checkpoint is newer than this executable understands."""


class LegacyProjectionConflictError(RuntimeError):
    """Raised when two old towns claim one ambiguous persistence root."""


class TownStore:
    """Own checkpoint, event, transcript, and memory persistence for a town."""

    schema_version = SCHEMA_VERSION

    def __init__(self, world_id="legacy", state_path=None, db_path=None,
                 transcript_jsonl=None, transcript_log=None):
        self.world_id = world_id
        self.state_path = state_path or getattr(
            config, "STATE_PATH", DEFAULT_STATE_PATH)
        self.db_path = db_path or config.DB_PATH
        self.transcript_jsonl = transcript_jsonl or config.TRANSCRIPT_JSONL
        self.transcript_log = transcript_log or config.TRANSCRIPT_LOG
        self._lock = threading.RLock()
        self._ensure_parent(self.state_path)
        self._ensure_parent(self.db_path)
        self._ensure_parent(self.transcript_jsonl)
        self._ensure_parent(self.transcript_log)
        self.memory = MemoryStore(db_path=self.db_path, world_id=world_id)

    @staticmethod
    def _ensure_parent(path):
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @staticmethod
    def _fsync_parent(path):
        """Persist a completed atomic replacement where the OS supports it."""
        if os.name == "nt":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(os.path.dirname(path) or ".", flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    # ----------------------------------------------------------- checkpoint
    @classmethod
    def migrate_checkpoint(cls, state, identity_path=None):
        """Upgrade a legacy checkpoint to the current envelope in memory.

        Pre-TownStore files had no schema marker.  Their contents are already
        compatible with schema 1, so migration is intentionally additive.
        """
        if not isinstance(state, dict):
            raise ValueError("checkpoint must contain a JSON object")
        version = state.get("schema_version", 0)
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint schema_version is not an integer") from exc
        if version > cls.schema_version:
            raise UnsupportedSchemaError(
                f"checkpoint schema {version} is newer than supported "
                f"schema {cls.schema_version}")
        migrated = dict(state)
        migrated["schema_version"] = cls.schema_version
        # Pre-TownStore checkpoints have no durable identity.  Never revive
        # those as the shared literal ``legacy`` scope: that would make their
        # event, memory, and goal projections collide with every other old
        # town using the same persistence root. Deriving the ID from the
        # persistence root also makes two concurrent starters choose the same
        # identity instead of racing unrelated random IDs into the checkpoint.
        if migrated.get("world_id") in (None, "", "legacy"):
            identity_path = identity_path or getattr(
                config, "STATE_PATH", DEFAULT_STATE_PATH)
            identity_key = os.path.abspath(identity_path)
            migrated["world_id"] = uuid.uuid5(
                uuid.NAMESPACE_URL, f"pepperton:{identity_key}").hex[:12]
            migrated["_legacy_identity_pending"] = True
        return migrated

    @classmethod
    def load_checkpoint_file(cls, path=None):
        path = path or getattr(config, "STATE_PATH", DEFAULT_STATE_PATH)
        try:
            with open(path, encoding="utf-8") as handle:
                state = json.load(handle)
        except FileNotFoundError:
            return None
        # Corrupt, unreadable, or newer checkpoints must never be interpreted
        # as permission to start a fresh town and overwrite recoverable state.
        return cls.migrate_checkpoint(state, identity_path=path)

    def load_checkpoint(self):
        return self.load_checkpoint_file(self.state_path)

    def save_checkpoint(self, state):
        """Atomically write a versioned checkpoint beside the live file."""
        envelope = self.migrate_checkpoint(
            state, identity_path=self.state_path)
        with self._lock:
            self._ensure_parent(self.state_path)
            fd, tmp_path = tempfile.mkstemp(
                prefix=".world_state.", suffix=".tmp",
                dir=os.path.dirname(self.state_path) or ".")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(envelope, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self.state_path)
                self._fsync_parent(self.state_path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

    # --------------------------------------------------------------- events
    def append_event(self, event):
        with self._lock:
            with open(self.transcript_jsonl, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def append_log(self, line):
        with self._lock:
            with open(self.transcript_log, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()

    def append_event_with_log(self, event, line):
        """Append an authoritative event and its derived log line in order."""
        with self._lock:
            self.append_event(event)
            try:
                self.append_log(line)
            except OSError:
                # JSONL is authoritative; recovery rebuilds this projection.
                return False
            return True

    def migrate_legacy_projections(self):
        """Claim pre-TownStore projections for this checkpoint's new ID.

        The checkpoint carries a pending marker until this operation
        completes, so a crash between the checkpoint write and projection
        migration can safely repeat the idempotent claim on restart.
        """
        with self._lock:
            event_stream_missing = False
            try:
                with open(self.transcript_jsonl, encoding="utf-8") as handle:
                    lines = handle.readlines()
            except FileNotFoundError:
                lines = []
                event_stream_missing = True

            rewritten = []
            changed = False
            legacy_events = 0
            events = []
            for line in lines:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    rewritten.append(line)
                    continue
                if not isinstance(event, dict):
                    rewritten.append(line)
                    continue
                if event.get("wid", "legacy") == "legacy":
                    legacy_events += 1
                    event["wid"] = self.world_id
                    changed = True
                rewritten.append(json.dumps(event, ensure_ascii=False) + "\n")
                events.append(event)
            existing_claim = self.memory.legacy_world_claim()
            if existing_claim and existing_claim != self.world_id:
                raise LegacyProjectionConflictError(
                    f"legacy projections already belong to {existing_claim}; "
                    f"restore this town with an isolated persistence root or "
                    f"an explicit migration mapping")
            if not existing_claim and (legacy_events or
                                       self.memory.legacy_memory_count()):
                recorded_claim = self.memory.claim_legacy_world(self.world_id)
                if recorded_claim != self.world_id:
                    raise LegacyProjectionConflictError(
                        f"legacy projections already belong to {recorded_claim}; "
                        f"restore this town with an isolated persistence root or "
                        f"an explicit migration mapping")
            self.memory.migrate_legacy_world(self.world_id)
            if event_stream_missing:
                return
            if not changed:
                # A crash after replacing JSONL but before rebuilding the
                # derived log is also recoverable on the retry.
                self._rewrite_log(events)
                return

            fd, tmp_path = tempfile.mkstemp(
                prefix=".transcript-migrate.", suffix=".tmp",
                dir=os.path.dirname(self.transcript_jsonl) or ".")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.writelines(rewritten)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self.transcript_jsonl)
                self._fsync_parent(self.transcript_jsonl)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            self._rewrite_log(events)

    def read_events(self, world_id=None, max_tick=None):
        """Read valid event envelopes, optionally for one world and timeline."""
        selected = []
        wanted = self.world_id if world_id is None else world_id
        try:
            with open(self.transcript_jsonl, encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            return selected
        for line in lines:
            try:
                event = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get("wid", "legacy") != wanted:
                continue
            if max_tick is not None and event.get("tick", 0) > max_tick:
                continue
            selected.append(event)
        return selected

    def rewind_events(self, restored_tick, restored_event_seq=None,
                      world_id=None):
        """Remove this world's abandoned post-checkpoint event tail."""
        return self.rewind_event_tail(
            restored_tick, restored_event_seq, world_id=world_id)

    @staticmethod
    def _log_line(event):
        day = event.get("day", 1)
        sim_time = event.get("sim_time", "00:00")
        location = event.get("location", "")
        etype = str(event.get("type", "event"))
        agent = event.get("agent")
        target = event.get("target")
        who = f"{agent} -> {target}" if target else (agent or "WORLD")
        return (f"[Day {day}, {sim_time}] [{location}] {etype.upper():8s} "
                f"{who}: {event.get('text', '')}\n")

    def _rewrite_log(self, events):
        fd, tmp_path = tempfile.mkstemp(
            prefix=".transcript-log.", suffix=".tmp",
            dir=os.path.dirname(self.transcript_log) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                for event in events:
                    handle.write(self._log_line(event))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.transcript_log)
            self._fsync_parent(self.transcript_log)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def rewind_event_tail(self, restored_tick, restored_event_seq=None,
                          world_id=None):
        """Rewind events and the derived text log as one projection update.

        Returns the number of dropped events.
        The JSONL stream remains authoritative; the human log is rebuilt from
        its surviving event envelopes.
        """
        wanted = self.world_id if world_id is None else world_id
        with self._lock:
            try:
                with open(self.transcript_jsonl, encoding="utf-8") as handle:
                    lines = handle.readlines()
            except FileNotFoundError:
                if restored_event_seq:
                    raise
                return 0

            kept = []
            surviving_events = []
            dropped = 0
            for line in lines:
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    kept.append(line)
                    continue
                if not isinstance(event, dict):
                    kept.append(line)
                    continue
                if event.get("wid", "legacy") != wanted:
                    kept.append(line)
                    surviving_events.append(event)
                    continue
                abandoned = event.get("tick", 0) > restored_tick
                if restored_event_seq is not None:
                    abandoned = abandoned or event.get("seq", 0) > restored_event_seq
                if abandoned:
                    dropped += 1
                else:
                    kept.append(line)
                    surviving_events.append(event)

            if not dropped:
                # The event stream is authoritative and the human transcript
                # is derived.  Rebuild even when the checkpoint did not drop
                # an event: a crash between append_event and append_log, or a
                # deleted log file, otherwise leaves the projection stale
                # forever because recovery appears to be a no-op.
                self._rewrite_log(surviving_events)
                return 0
            fd, tmp_path = tempfile.mkstemp(
                prefix=".transcript.", suffix=".tmp",
                dir=os.path.dirname(self.transcript_jsonl) or ".")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.writelines(kept)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_path, self.transcript_jsonl)
                self._fsync_parent(self.transcript_jsonl)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            self._rewrite_log(surviving_events)
            return dropped

    def close(self):
        """Release the SQLite connection owned by this store."""
        close = getattr(self.memory, "close", None)
        if close:
            close()
