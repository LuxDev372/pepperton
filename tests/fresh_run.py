"""Contract check for run.py's fresh-town reset."""

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config
from run import reset_runtime_data


ATTRIBUTES = {
    "NEWS_CACHE": "news_cache.json",
    "DB_PATH": "pepperton.db",
    "STATE_PATH": "world_state.json",
    "TRANSCRIPT_JSONL": "transcript.jsonl",
    "TRANSCRIPT_LOG": "transcript.log",
    "LEDGER_PATH": "experiments.json",
}


def main():
    original = {attribute: getattr(config, attribute) for attribute in ATTRIBUTES}
    try:
        with tempfile.TemporaryDirectory() as data_dir:
            for attribute, filename in ATTRIBUTES.items():
                setattr(config, attribute, os.path.join(data_dir, filename))
                with open(getattr(config, attribute), "w", encoding="utf-8") as f:
                    f.write("stale run data")
            for suffix in ("-wal", "-shm", "-journal"):
                with open(config.DB_PATH + suffix, "w", encoding="utf-8") as f:
                    f.write("stale sqlite sidecar")
            stale_temps = (
                config.STATE_PATH + ".tmp",
                config.LEDGER_PATH + ".tmp",
                os.path.join(data_dir, ".world_state.abcd.tmp"),
                os.path.join(data_dir, ".transcript.abcd.tmp"),
            )
            for path in stale_temps:
                with open(path, "w", encoding="utf-8") as f:
                    f.write("stale temporary data")
            unrelated = os.path.join(data_dir, "keep-me.txt")
            with open(unrelated, "w", encoding="utf-8") as f:
                f.write("not owned by Pepperton")
            lock_path = os.path.join(data_dir, ".town.lock")
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump({"pid": -1, "town": "stale"}, f)

            removed = reset_runtime_data()
            assert removed
            for attribute in ATTRIBUTES:
                assert not os.path.exists(getattr(config, attribute))
            for path in stale_temps:
                assert not os.path.exists(path)
            for suffix in ("-wal", "-shm", "-journal"):
                assert not os.path.exists(config.DB_PATH + suffix)
            assert not os.path.exists(lock_path)
            assert os.path.exists(unrelated)

            with open(config.STATE_PATH, "w", encoding="utf-8") as f:
                f.write("must survive refusal")
            with open(lock_path, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getppid(), "town": "active test town",
                           "opened_utc": "now"}, f)
            try:
                reset_runtime_data()
            except RuntimeError as exc:
                assert "is running as PID" in str(exc)
            else:
                raise AssertionError("fresh reset accepted a live town lock")
            assert os.path.exists(config.STATE_PATH)
    finally:
        for attribute, value in original.items():
            setattr(config, attribute, value)

    print("fresh run contract: ok")


if __name__ == "__main__":
    main()
