"""World and checkpoint contracts for social-record persistence."""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-social-persistence-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
from sim.engine import Engine
from sim.social import Commitment, Interaction

config.MOCK_MODE = True
config.RADIO_ENABLED = False


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


engine = Engine(seed=918)
engine.world._social_seq = 4
scene = Interaction("scene-4", "Ada", "Ben", "the plaza", "library", "request",
                    proposal={"private": "details"})
commitment = Commitment("commitment-4", "Ada", "Ben", "build it",
                        proof={"kind": "project_complete", "project": "library"})
engine.world.interactions[scene.id] = scene
engine.world.commitments[commitment.id] = commitment
engine.save_state()
restored = Engine(state=Engine.load_state())
check("social sequence survives checkpoint", restored.world._social_seq == 4)
check("interaction survives checkpoint", restored.world.interactions[scene.id].to_dict() == scene.to_dict())
check("commitment survives checkpoint", restored.world.commitments[commitment.id].to_dict() == commitment.to_dict())
social = restored.snapshot()["social"]
check("snapshot publishes social records", len(social["interactions"]) == 1 and len(social["commitments"]) == 1)
check("snapshot hides private social fields", "proposal" not in social["interactions"][0] and "proof" not in social["commitments"][0])
engine.stop()
restored.stop()
print("Social-persistence proof complete")
