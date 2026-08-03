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
    def __init__(self, db_path=None):
        self.path = db_path or config.DB_PATH
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
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_mem_agent ON memories(agent, tick)"
        )
        self._conn.commit()

    def add(self, agent, tick, day, sim_time, kind, text, importance):
        with self._lock:
            self._conn.execute(
                "INSERT INTO memories (agent, tick, day, sim_time, kind, text, importance)"
                " VALUES (?,?,?,?,?,?,?)",
                (agent, tick, day, sim_time, kind, text, float(importance)),
            )
            self._conn.commit()

    def recent(self, agent, n=10):
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, sim_time, kind, text, importance, tick FROM memories"
                " WHERE agent=? ORDER BY tick DESC, id DESC LIMIT ?",
                (agent, n),
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
                " WHERE agent=? AND day=? ORDER BY tick, id",
                (agent, day),
            ).fetchall()
        return [
            {"sim_time": r[0], "kind": r[1], "text": r[2], "importance": r[3]}
            for r in rows
        ]

    def retrieve(self, agent, query, now_tick, k=None):
        """Top-k memories by recency + importance + keyword relevance."""
        k = k or config.MEMORY_TOP_K
        qtok = _tokens(query)
        with self._lock:
            rows = self._conn.execute(
                "SELECT day, sim_time, kind, text, importance, tick FROM memories"
                " WHERE agent=? ORDER BY tick DESC LIMIT 400",
                (agent,),
            ).fetchall()
        w = config.MEMORY_WEIGHTS
        hl = config.MEMORY_RECENCY_HALFLIFE_TICKS
        scored = []
        for day, sim_time, kind, text, imp, tick in rows:
            age = max(0, now_tick - tick)
            recency = math.pow(0.5, age / hl)
            mtok = _tokens(text)
            relevance = (len(qtok & mtok) / (len(qtok) + 1)) if qtok else 0.0
            score = (
                w["recency"] * recency
                + w["importance"] * (imp / 10.0)
                + w["relevance"] * relevance
            )
            scored.append((score, {
                "day": day, "sim_time": sim_time, "kind": kind,
                "text": text, "importance": imp, "tick": tick,
            }))
        scored.sort(key=lambda s: s[0], reverse=True)
        top = [m for _, m in scored[:k]]
        top.sort(key=lambda m: m["tick"])
        return top

    def count(self, agent):
        with self._lock:
            (n,) = self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE agent=?", (agent,)
            ).fetchone()
        return n
