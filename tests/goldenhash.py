"""Behavioral fingerprint — the refactoring safety net.

Runs two seeded mock towns for 800 ticks each and hashes every event
the worlds emit plus the final economic state. Because Pepperton is
deterministic in mock mode (seeded RNGs everywhere, including
mid-stream restore), the hash is a complete fingerprint of behavior.

Usage:
    python tests/goldenhash.py            # print the fingerprint

The contract: a pure refactor (renames, restructuring, style) must
leave this hash IDENTICAL. If it moved, you changed behavior — maybe
on purpose (a feature), maybe not (a bug you just shipped). A feature
change is expected to move it; record the new value in the commit
message so the next refactorer has a baseline.

This file exists because an external reviewer said the code style
fell short and suggested a cleanup pass. They were right about the
style. The cleanup (v2.0.1) was verified with this exact harness:
same hash before and after, 13 if-branches replaced by a dispatch
table, zero behavior drift.
"""
import hashlib
import json
import os
import shutil
import sys

# Scratch directory, never the project root — this file rmtree's "data",
# and a live town keeps its whole life in there. Same protection lisim's
# PR #1 gave selftest.py, for the same reason.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.path.join(ROOT, ".selftest")
sys.path.insert(0, ROOT)
os.makedirs(SCRATCH, exist_ok=True)
os.chdir(SCRATCH)

import config

config.MOCK_MODE = True
config.RADIO_ENABLED = False   # no network: the fingerprint must be hermetic

from sim.engine import Engine  # noqa: E402  (config must be pinned first)
from sim.world import World    # noqa: E402

SEEDS = (7, 890811919)
TICKS = 800

fingerprint = hashlib.sha256()
for seed in SEEDS:
    World.close_all()   # Windows: an open transcript handle blocks the wipe
    shutil.rmtree("data", ignore_errors=True)
    os.makedirs("data")
    engine = Engine(seed=seed)
    engine.run_headless(TICKS)
    for line in open("data/transcript.jsonl", encoding="utf-8"):
        event = json.loads(line)
        event.pop("wid", None)   # world_id is a fresh uuid each boot
        fingerprint.update(json.dumps(event, sort_keys=True).encode())
    world = engine.world
    end_state = {
        "money": {a.name: round(a.money, 2) for a in world.agents.values()},
        "locations": {a.name: a.location for a in world.agents.values()},
        "tills": {k: round(v, 2) for k, v in sorted(world.tills.items())},
        "debts": [(d["debtor"], d["creditor"], d["amount"], d["status"])
                  for d in world.debts],
        "promises": [(p["maker"], p["to"], p["status"])
                     for p in world.promises],
    }
    fingerprint.update(json.dumps(end_state, sort_keys=True).encode())

World.close_all()
shutil.rmtree("data", ignore_errors=True)
print(f"GOLDEN {fingerprint.hexdigest()}")
print(f"       ({len(SEEDS)} towns x {TICKS} ticks, mock minds, radio off)")
