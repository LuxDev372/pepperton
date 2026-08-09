"""A villager reciting the goal we assigned them is not a live decision.

`agent.goal` is rendered into the SYSTEM prompt every tick — "What's driving
you lately: {goal}." — and on Day 182 Ida Merriweather posted config.GOALS[0]
to the Pompeii group chat, in first person, as ordinary speech:

    "I need to organize a town event nobody asked for."

It parsed clean and counted toward live_pct, because `echoed_template` knew
our placeholders (v3.8.3) and the models' sentinels (v3.8.7) and nothing at
all about our goals. Third time in the same family, third layer out.

This pins the distinction that matters: **recitation is caught, paraphrase
is not.** A villager restating their preoccupation in their own words is
thinking. A villager handing back the string is handing back the form.

Also pins that `goal` is readable from outside the process, so a claim about
a villager's motivation can be checked instead of inferred.
(claude/WHATS-DRIVING-YOU.md)
"""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-goalecho-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
sys.path.insert(0, ROOT)
os.environ.setdefault("PEPPERTON_TEST", "1")

import config
from sim import prompts
from sim.engine import Engine

config.MOCK_MODE = True
config.RADIO_ENABLED = False

FAILED = []


def check(label, condition):
    if condition:
        print(f"PASS {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}")


def echo(text):
    return prompts.echoed_template({"action": "say", "text": text})


# --------------------------------------------------- RECITATION IS CAUGHT
check("the real Day 182 line is caught",
      echo("I need to organize a town event nobody asked for. "
           "Let's make it something that brings everyone together.")
      == "organize a town event nobody asked for")

check("...converted to first person, it is still caught",
      echo("I want someone — anyone — to appreciate my work.") is not None)

check("...left in third person, it is caught too",
      echo("She wants someone — anyone — to appreciate their work.") is not None)

check("every goal in config is covered",
      all(echo(g) is not None for g in config.GOALS))

check("stranger goals are covered as well",
      all(echo(g) is not None for g in getattr(config, "STRANGER_GOALS", [])))

# ------------------------------------------------ PARAPHRASE IS NOT CAUGHT
# This is the line that keeps the detector honest. A villager who says the
# same thing in their own words has thought about it.
for label, text in (
    ("Marlow's real paraphrase",
     "I need to find someone to partner up with on this venture."),
    ("plain speech",
     "Let's meet at the diner at nine and finish the pool."),
    ("near-topic but original",
     "I'd like someone to appreciate what I do around here."),
    ("Roy investigating",
     "I've got some intel on Marlow's whereabouts."),
    ("a villager naming a real project",
     "Let's get the Community Kitchen at Rosie's Diner finished."),
):
    check(f"NOT caught: {label}", echo(text) is None)

# ------------------------------------------------------- scraped, not fixed
config_goals = list(config.GOALS)
try:
    config.GOALS = ["wants to paint every fence in the county twice over"]
    prompts._GOAL_FRAGMENTS = None
    check("a town that edits its own goal list is covered without code changes",
          echo("I will paint every fence in the county twice over") is not None)
    check("...and the old goals stop matching once they are gone",
          echo(config_goals[0]) is None)
finally:
    config.GOALS = config_goals
    prompts._GOAL_FRAGMENTS = None

check("a fragment must be long enough to be distinctive",
      all(len(f) >= 20 for f in prompts.goal_fragments()))

# ------------------------------------------- goal is readable from outside
engine = Engine(seed=311)
someone = list(engine.world.agents.values())[0]
public = someone.to_public()
check("a villager's goal is exposed in the public payload", "goal" in public)
check("...and it is the real one, not a placeholder",
      public["goal"] == someone.goal and bool(public["goal"]))
check("...for every villager in the cast",
      all("goal" in a.to_public() for a in engine.world.agents.values()))

# and it must not have become something a villager can read about themselves
# through the world — the API is ours, the prompt is theirs
check("exposing it changed nothing a villager perceives",
      "goal" not in str(engine.world.snapshot())
      if hasattr(engine.world, "snapshot") else True)

print()
if FAILED:
    print(f"FAILED {len(FAILED)}")
    for label in FAILED:
        print(f"  - {label}")
    raise SystemExit(1)
print("goal_echo: all checks passed")
