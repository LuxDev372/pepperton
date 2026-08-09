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

# THE HARNESS DOES NOT READ YOUR CONFIG (v3.1.1).
#
# It used to. That made this hash a fingerprint of code PLUS the operator's
# settings, so two people running identical code on different towns got
# different answers and had no way to know that was expected. It cost Brad a
# scare on 2026-08-07: he ran v3.1.0 against his own hundred-day Pepperton
# config, got a hash nothing like the one in the release notes, and had every
# reason to think the release had broken his town.
#
# tests/goldenworld.py is a frozen world, installed as `config` before sim is
# imported. Now the hash answers only: given the same world, did the CODE
# change behaviour? Which is the question it was always advertised to answer.
import importlib.util

_spec = importlib.util.spec_from_file_location(
    "config", os.path.join(ROOT, "tests", "goldenworld.py"))
config = importlib.util.module_from_spec(_spec)
sys.modules["config"] = config      # before sim imports it
_spec.loader.exec_module(config)

config.MOCK_MODE = True
config.RADIO_ENABLED = False   # no network: the fingerprint must be hermetic

from sim.engine import Engine  # noqa: E402  (config must be pinned first)
from sim.world import World    # noqa: E402


# Say what you are, out loud, first (v3.1.2). Brad hit the same crash on the
# same line number twice in one morning — once because the suite had a real
# bug, and once because the fixed file had not actually landed on disk. From
# the output those are indistinguishable, and I misdiagnosed the second one
# as the first. A harness that does not identify itself cannot be trusted to
# report on anything else.
# A piped harness must not die of SIGPIPE (v3.1.3). `selftest.py | head -1`
# raised BrokenPipeError mid-run and looked exactly like a failure in the
# suite, one morning after two other things had looked exactly like failures
# in the suite. Python leaves SIGPIPE set to SIG_IGN; restoring the default
# makes `| head` behave the way anyone piping a test runner expects.
try:
    import signal
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (ImportError, AttributeError, ValueError):
    pass    # Windows has no SIGPIPE; nothing to defend against there

def _stamp(what):
    v = "unknown"
    for p in (os.path.join(ROOT, "VERSION"), "VERSION"):
        try:
            v = open(p).read().strip()
            break
        except OSError:
            pass
    print(f"{what} — Pepperton v{v}  ({os.path.abspath(__file__)})",
          flush=True)
    return v

_stamp("GOLDEN HASH")

SEEDS = (7, 890811919)
TICKS = 800

# Envelope, not physics. See the note at the strip site below — this list
# has now been wrong twice, both times about the same contributor's work.
#   wid / event_id / cause_event_id / command_id : per-boot identity
#   source / topic / thread_id / salience        : routing and annotation
ENVELOPE_FIELDS = ("wid", "event_id", "cause_event_id", "command_id",
                   "source", "topic", "thread_id", "salience")

fingerprint = hashlib.sha256()
for seed in SEEDS:
    World.close_all()   # Windows: an open transcript handle blocks the wipe
    shutil.rmtree("data", ignore_errors=True)
    os.makedirs("data")
    engine = Engine(seed=seed)
    engine.run_headless(TICKS)
    for line in open("data/transcript.jsonl", encoding="utf-8"):
        event = json.loads(line)
        # Per-boot identity is not physics. world_id is a fresh uuid every
        # time a town starts, and ekinnee's causal envelope derives three
        # more identifiers from it — so an event that is byte-identical in
        # every way that matters still hashes differently on every run.
        #
        # THIS HARNESS MADE HIS WORK LOOK NON-DETERMINISTIC. It was ours.
        # We stripped `wid` and stopped, then read the moving hash as a
        # fault in a contributor's thirteen-PR stack. An instrument that
        # cannot say what it is cannot be trusted to say anything else —
        # including what it thinks of somebody else's code. (LESSONS.md #1)
        #
        # AND THEN WE DID IT AGAIN. His social layer adds four more envelope
        # fields — salience, source, thread_id, topic — and this list did
        # not know their names, so the hash moved on day 1, event 1, before
        # a single villager had chosen anything differently. We read that as
        # "his stack changes the world with every feature dark" and parked
        # the merge on it. It wasn't. Same lesson, same file, twice, on the
        # same contributor's work.
        #
        # THE RULE: this list holds every field that describes the ENVELOPE
        # an event arrived in — who routed it, what it was about, which boot
        # it belongs to. It must never hold a field that describes what
        # HAPPENED. If you add an envelope field to world.emit, add its name
        # here in the same commit, or the next person to touch physics will
        # be blamed for your metadata.
        for envelope in ENVELOPE_FIELDS:
            event.pop(envelope, None)
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
