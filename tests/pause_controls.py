"""Focused proof that an observatory pause interrupts a live policy wait."""

import atexit
import os
import shutil
import sys
import tempfile
import threading


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-pause-controls-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)

import config
from sim.engine import Engine
from sim.policy import Decision
from sim.world import World


config.MOCK_MODE = True
config.RADIO_ENABLED = False
config.CHAOS["enabled"] = False


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


engine = Engine(seed=90210)
entered_policy = threading.Event()
release_policy = threading.Event()


def blocked_decide(task):
    entered_policy.set()
    release_policy.wait(2)
    return Decision({"action": "idle"})


# Keep the real PolicyScheduler: it must stop waiting when pause_event is set,
# even though provider work already running in a daemon thread cannot be killed.
engine.scheduler._decide = blocked_decide
worker = threading.Thread(target=engine.step)
worker.start()
check("test tick reached its policy wait", entered_policy.wait(1))

status = engine.toggle_pause()
check("pause reports that the active tick is unwinding",
      status == {"paused": True, "pause_pending": True})
worker.join(0.5)
check("pause does not wait for the model timeout", not worker.is_alive())
check("pause becomes settled after the tick unwinds",
      engine.pause_status() == {"paused": True, "pause_pending": False})

release_policy.set()
engine.stop()
World.close_all()
print("Pause controls proof complete")
