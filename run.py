"""Pepperton entry point.

  python run.py                 # serve the observatory + live sim
  python run.py --headless 96   # run one sim-day fast, print transcript tail
  python run.py --seed 7        # pin the cast
  python run.py --live          # force real Ollama minds (overrides config)
  python run.py --mock          # force mock minds
"""

import argparse
import json
import os
import sys

sys.path.insert(0, ".")

import config  # noqa: E402


RUNTIME_FILES = (
    ("NEWS_CACHE", "news_cache.json"),
    ("DB_PATH", "pepperton.db"),
    ("STATE_PATH", "world_state.json"),
    ("TRANSCRIPT_JSONL", "transcript.jsonl"),
    ("TRANSCRIPT_LOG", "transcript.log"),
    ("LEDGER_PATH", "experiments.json"),
)


def reset_runtime_data() -> list[str]:
    """Remove every generated artifact for a genuinely fresh town.

    Unknown files are deliberately left alone. A live town lock aborts the
    reset before anything is removed so ``--fresh`` cannot corrupt another
    process using the same data directory.
    """
    from sim.store import TMP_PREFIXES, TownStore

    paths = {
        os.path.abspath(getattr(
            config, attribute, os.path.join("data", filename)))
        for attribute, filename in RUNTIME_FILES
    }
    state_path = os.path.abspath(getattr(
        config, "STATE_PATH", "data/world_state.json"))
    lock_path = os.path.join(os.path.dirname(state_path) or ".", ".town.lock")

    if os.path.exists(lock_path):
        try:
            with open(lock_path, encoding="utf-8") as handle:
                lock = json.load(handle)
            pid = int(lock.get("pid", -1))
        except (OSError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"cannot safely reset {os.path.dirname(state_path) or '.'}: "
                f"town lock {lock_path} is unreadable") from exc
        if pid != os.getpid() and TownStore._alive(pid):
            raise RuntimeError(
                f"cannot reset {os.path.dirname(state_path) or '.'}: "
                f"{lock.get('town', 'this town')} is running as PID {pid} "
                f"(since {lock.get('opened_utc', '?')})")

    candidates = set(paths)
    for path in paths:
        candidates.update((path + ".tmp", path + "-wal", path + "-shm",
                           path + "-journal"))

    for directory in {os.path.dirname(path) or "." for path in paths}:
        try:
            names = os.listdir(directory)
        except FileNotFoundError:
            continue
        for name in names:
            if (name.endswith(".tmp")
                    and any(name.startswith(prefix)
                            for prefix in TMP_PREFIXES)):
                candidates.add(os.path.join(directory, name))

    candidates.discard(lock_path)
    removed = []
    for path in sorted(candidates):
        try:
            os.unlink(path)
            removed.append(path)
        except FileNotFoundError:
            pass
    if os.path.exists(lock_path):
        os.unlink(lock_path)
        removed.append(lock_path)
    return removed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", type=int, metavar="TICKS", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--fresh", action="store_true",
                    help="delete generated run data and start a new world")
    args = ap.parse_args()

    if args.live:
        config.MOCK_MODE = False
    if args.mock:
        config.MOCK_MODE = True
    if args.seed is not None:
        config.WORLD_SEED = args.seed
    if args.fresh:
        try:
            removed = reset_runtime_data()
        except RuntimeError as exc:
            ap.error(str(exc))
        print(f"[RUN] fresh town: removed {len(removed)} generated artifact(s)",
              flush=True)

    if args.headless is not None:
        from sim.engine import Engine
        eng = Engine(seed=config.WORLD_SEED)
        eng.run_headless(args.headless)
        print(f"Ran {args.headless} ticks -> {eng.world.clock.label}")
        print(f"Transcript: {config.TRANSCRIPT_LOG}")
        with open(config.TRANSCRIPT_LOG, encoding="utf-8") as f:
            tail = f.readlines()[-30:]
        print("".join(tail))
        return

    import uvicorn
    uvicorn.run("server.app:app", host=config.HOST, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
