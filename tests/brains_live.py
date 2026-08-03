"""Live cast check — does every mind in the current cast actually answer?

    python tests/brains_live.py              # the cast in config.CAST
    python tests/brains_live.py mixed        # some other cast
    python tests/brains_live.py glm-5.2:cloud@cloud llama3.2:3b@default

Run this after adding a model or a provider. It makes one real request per
entry with the same payload the sim uses (system + user, JSON out) and
reports whether the reply parses into an action the engine would accept.
No GPU-free mock anywhere — a PASS here means that villager can think.
"""

import sys
import time

sys.path.insert(0, ".")

import config                       # noqa: E402
from sim import prompts             # noqa: E402
from sim.brains import LLMBrain     # noqa: E402

SYSTEM = ("You are a villager in a small town. Answer with JSON only, "
          'shaped exactly like {"action": "...", "text": "..."}.')
USER = ('You are standing in the plaza. Your neighbor Mabel just said hello. '
        'Reply with a JSON object: {"action": "say", "text": "<your reply>"}.')


def parse_pool(argv):
    if not argv:
        return config.CAST, config.MODEL_POOL
    if len(argv) == 1 and argv[0] in getattr(config, "CASTS", {}):
        return argv[0], config.CASTS[argv[0]]
    pool = []
    for spec in argv:
        model, _, host = spec.partition("@")
        pool.append((model, host or "default"))
    return "argv", pool


def main():
    label, pool = parse_pool(sys.argv[1:])
    print(f"cast {label!r} — {len(pool)} minds\n")
    failures = 0
    for model, host in pool:
        brain = LLMBrain(model, host)
        t0 = time.monotonic()
        try:
            raw = brain._chat(SYSTEM, USER)
            elapsed = time.monotonic() - t0
            parsed = prompts.parse_json_reply(raw)
            if isinstance(parsed, dict) and "action" in parsed:
                said = str(parsed.get("text", ""))[:60]
                print(f"PASS {model} @{host} — {elapsed:5.1f}s — {said!r}")
            else:
                failures += 1
                print(f"FAIL {model} @{host} — {elapsed:5.1f}s — "
                      f"unparseable: {raw[:100]!r}")
        except Exception as e:                        # noqa: BLE001
            failures += 1
            print(f"FAIL {model} @{host} — {time.monotonic() - t0:5.1f}s — "
                  f"{type(e).__name__}: {str(e)[:140]}")

    print(f"\n{len(pool) - failures}/{len(pool)} minds answered")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
