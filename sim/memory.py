"""Memory streams — the Stanford-style backbone.

Every agent accumulates timestamped memories (observations, speech,
actions, reflections). Retrieval scores candidates on recency,
importance, and keyword relevance to the current situation.
"""

import math
import re
import sqlite3
import threading

import config

_WORD = re.compile(r"[a-z']+")


def _tokens(text):
    return set(_WORD.findall(text.lower()))


class MemoryStore:
    def __init__(self, db_path=None, world_id="legacy"):
        self.path = db_path or config.DB_PATH
        self.world_id = world_id
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                tick INTEGER NOT NULL,
                day INTEGER NOT NULL,
                sim_time TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                importance REAL NOT NULL
            )"""
        )
        # migration: world_id column separates incarnations of a town so a
        # --fresh world can never inherit a dead cast's memories
        cols = [r[1] for r in self._conn.execute("PRAGMA table_info(memories)")]
        if "world_id" not in cols:
            self._conn.execute(
                "ALTER TABLE memories ADD COLUMN world_id TEXT DEFAULT 'legacy'")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent, tick)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS legacy_world_claims ("
            "source TEXT PRIMARY KEY, world_id TEXT NOT NULL)"
        )
        self._conn.commit()

    def add(self, agent, tick, day, sim_time, kind, text, importance):
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (agent, tick, day, sim_time, kind, text, "
                "importance, world_id) VALUES (?,?,?,?,?,?,?,?)",
                (agent, tick, day, sim_time, kind, text, float(importance),
                 self.world_id),
            )
            self._conn.commit()

    def migrate_legacy_world(self, world_id):
        """Claim unscoped pre-TownStore memories for one restored town.

        Legacy rows cannot be separated after the fact if several old towns
        shared one database. Claiming them once is safer than leaving every
        newly restored town attached to the shared ``legacy`` scope.
        """
        with self._lock:
            cur = self._conn.execute(
                "UPDATE memories SET world_id=? WHERE world_id='legacy'",
                (world_id,),
            )
            self._conn.commit()
            return cur.rowcount

    def legacy_memory_count(self):
        with self._lock:
            (count,) = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE world_id='legacy'"
            ).fetchone()
        return count

    def legacy_world_claim(self):
        with self._lock:
            row = self._conn.execute(
                "SELECT world_id FROM legacy_world_claims "
                "WHERE source='pre-townstore'"
            ).fetchone()
        return row[0] if row else None

    def claim_legacy_world(self, world_id):
        """Record the one town allowed to adopt ambiguous legacy rows."""
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO legacy_world_claims(source, world_id) "
                "VALUES ('pre-townstore', ?)", (world_id,))
            row = self._conn.execute(
                "SELECT world_id FROM legacy_world_claims "
                "WHERE source='pre-townstore'"
            ).fetchone()
            self._conn.commit()
        return row[0] if row else None

    def high_watermark(self):
        """Return the latest durable memory row owned by this town."""
        with self._lock:
            (memory_id,) = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM memories WHERE world_id=?",
                (self.world_id,),
            ).fetchone()
        return memory_id

    def delete_after(self, tick, memory_id=None):
        """Reconcile after a crash-restore: memories from the abandoned
        future timeline are removed so nobody remembers what never happened.

        The row watermark handles writes later in the checkpoint's tick.
        """
        with self._lock:
            future_clauses = ["tick>?"]
            params = [self.world_id, int(tick)]
            if memory_id is not None:
                future_clauses.append("id>?")
                params.append(int(memory_id))
            cur = self._conn.execute(
                "DELETE FROM memories WHERE world_id=? AND (" +
                " OR ".join(future_clauses) + ")",
                params,
            )
            self._conn.commit()
        return cur.rowcount

    def recent(self, agent, n=10):
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, sim_time, kind, text, importance, tick FROM memories"
                " WHERE agent=? AND world_id=? ORDER BY tick DESC, id DESC LIMIT ?",
                (agent, self.world_id, n),
            ).fetchall()
        return [
            {"day": r[0], "sim_time": r[1], "kind": r[2], "text": r[3],
             "importance": r[4], "tick": r[5]}
            for r in reversed(rows)
        ]

    def day_memories(self, agent, day):
        with self._lock:
            rows = self._conn.execute(
                "SELECT sim_time, kind, text, importance FROM memories"
                " WHERE agent=? AND day=? AND world_id=? ORDER BY tick, id",
                (agent, day, self.world_id),
            ).fetchall()
        return [
            {"sim_time": r[0], "kind": r[1], "text": r[2],
             "importance": r[3]}
            for r in rows
        ]

    def retrieve(self, agent, query, now_tick, k=None):
        """Top-k memories by recency + importance + keyword relevance."""
        k = k or config.MEMORY_TOP_K
        query_tokens = _tokens(query)
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, sim_time, kind, text, importance, tick FROM memories"
                " WHERE agent=? AND world_id=? ORDER BY tick DESC LIMIT 400",
                (agent, self.world_id),
            ).fetchall()
        weights = config.MEMORY_WEIGHTS
        halflife = config.MEMORY_RECENCY_HALFLIFE_TICKS
        scored = []
        for day, sim_time, kind, text, importance, tick in rows:
            age = max(0, now_tick - tick)
            recency = math.pow(0.5, age / halflife)
            memory_tokens = _tokens(text)
            relevance = (len(query_tokens & memory_tokens)
                         / (len(query_tokens) + 1)) if query_tokens else 0.0
            score = (
                weights["recency"] * recency
                + weights["importance"] * (importance / 10.0)
                + weights["relevance"] * relevance
            )
            scored.append((score, {
                "day": day, "sim_time": sim_time, "kind": kind,
                "text": text, "importance": importance, "tick": tick,
            }))
        scored.sort(key=lambda s: s[0], reverse=True)
        top = [m for _, m in scored[:k]]
        top.sort(key=lambda m: m["tick"])
        return top

    def count(self, agent):
        with self._lock:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE agent=? AND world_id=?",
                (agent, self.world_id),
            ).fetchone()
        return n

    def close(self):
        with self._lock:
            self._conn.close()
