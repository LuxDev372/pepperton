"""Contract proofs for serializable, privacy-safe social records."""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from sim.social import Commitment, Interaction, RESPONSE_STATUS, SPEECH_ACTS


def check(label, condition):
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


scene = Interaction(
    id="scene-1", initiator="Ada", target="Ben", location="the plaza",
    topic="library", speech_act="request", proposal={"private": "details"},
    created_tick=4, updated_tick=4)
restored_scene = Interaction.from_dict(scene.to_dict())
check("interaction round-trips", restored_scene.to_dict() == scene.to_dict())
check("open scene defaults to target responder", restored_scene.next_responder == "Ben")
check("public interaction hides proposal", "proposal" not in scene.to_public())

commitment = Commitment(
    id="commitment-1", owner="Ada", counterparty="Ben", condition="build it",
    deadline_day=5, proof={"kind": "project_complete", "project": "library"},
    fulfillment_event_ids=["event-9"])
restored_commitment = Commitment.from_dict(commitment.to_dict())
check("commitment round-trips", restored_commitment.to_dict() == commitment.to_dict())
public = commitment.to_public()
check("public commitment hides condition and proof",
      "condition" not in public and "proof" not in public)
check("public commitment exposes fulfillment count", public["fulfillment_count"] == 1)
check("speech-act contract includes all response acts",
      set(RESPONSE_STATUS).issubset(SPEECH_ACTS))
print("Social-record contract complete")
