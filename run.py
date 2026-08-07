"""Pepperton entry point.

  python run.py                 # serve the observatory + live sim
  python run.py --headless 96   # run one sim-day fast, print transcript tail
  python run.py --seed 7        # pin the cast
  python run.py --live          # force real Ollama minds (overrides config)
  python run.py --mock          # force mock minds
"""

import argparse
import sys

sys.path.insert(0, ".")

import config  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", type=int, metavar="TICKS", default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any saved town state and start a new world")
    args = ap.parse_args()

    if args.live:
        config.MOCK_MODE = False
    if args.mock:
        config.MOCK_MODE = True
    if args.seed is not None:
        config.WORLD_SEED = args.seed
    if args.fresh:
        import os
        state_path = getattr(config, "STATE_PATH", "data/world_state.json")
        for p in (state_path, state_path + ".tmp"):
            if os.path.exists(p):
                os.remove(p)

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
