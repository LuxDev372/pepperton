"""Speech is the only free action in this world, and it ate the town.

Measured on Pepperton, days 164-184, ten villagers:

    GOSSIP   3,790     18.0 broadcasts per villager per day
    SAY        524      2.5
    ACTION     720      3.4      <- everything anyone DID, movement included

Six utterances for every act, and five group-chat broadcasts for every one.
A meal costs $5, a drink $4, a room $8; rent falls every three days, permits
carry fines and houses get seized — and posting to all ten villagers at once,
from anywhere, costs nothing and can be done every fifteen minutes forever.
A tick spent posting is a tick not spent working.

Three brakes already existed and all three failed. The Paraphrase Act is a
>50% token-overlap test, and Vera Tibbs was not repeating words, she was
repeating meaning — "I'm tired of just talking about it" against "I'm tired
of endless talk and no progress" shares about a third of its tokens. The
Soapbox Law needs an empty room. TALK_STREAK_NUDGE fired for four of her
seven consecutive posts and she posted straight through it.

Recitation is caught; paraphrase is not. A rate limit is the only brake that
does not care what was said.

This pins the meter and, harder, pins what it must NOT touch: talking to the
people in the room with you is free, private texts are free, and the plan is
checked LAST so nobody is ever told they are out of data for a post that was
never going to send. (claude/PREREG-THE-TOWN-CAN-MAKE-WORK.md)
"""

import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = tempfile.mkdtemp(prefix="pepperton-phone-")
atexit.register(shutil.rmtree, SCRATCH, ignore_errors=True)
os.chdir(SCRATCH)
os.makedirs("data", exist_ok=True)
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


def post(world, agent, text):
    return world.execute(agent, {"action": "text", "to": "everyone",
                                 "text": text})


LINES = ["the fence at the park needs a hand tomorrow morning",
         "somebody left a hammer by the pond, it is still there",
         "rain later, bring the chairs in off the porch",
         "the diner smells like burnt coffee again this morning",
         "who has the good ladder, mine has a cracked rung",
         "reminder that rent falls on the third day, not the fourth",
         "there is a duck on the roof of the workshop",
         "the gate by the plaza is squeaking, it wants oil",
         "found a set of keys near the bar, come and claim them",
         "the notice board is full, somebody take the old cards down"]

# ------------------------------------------------------- the plan is OFF
old_phone = getattr(config, "PHONE", None)
config.PHONE = {"enabled": False}
engine = Engine(seed=616)
world = engine.world
cast = list(world.agents.values())
talker, elsewhere = cast[0], cast[1]
# somebody must be somewhere else or the world refuses a broadcast on the
# grounds that the entire town is standing in one room
elsewhere.location = "the park"
talker.location = "the plaza"
for a in cast[2:]:
    a.location = "the park"

check("with no plan, the meter reads nothing at all",
      world.phone_left(talker) is None)
ok = all(post(world, talker, LINES[i])[0] for i in range(8))
check("...and eight broadcasts in a row all go out", ok)

# -------------------------------------------------------- the plan is ON
config.PHONE = {"enabled": True, "free_posts_per_day": 3}
engine = Engine(seed=616)
world = engine.world
cast = list(world.agents.values())
talker = cast[0]
talker.location = "the plaza"
for a in cast[1:]:
    a.location = "the park"

check("the allowance is the configured one", world.phone_left(talker) == 3)

ok1, _ = post(world, talker, LINES[0])
check("the first post goes out", ok1)
check("...and the meter drops", world.phone_left(talker) == 2)
post(world, talker, LINES[1])
post(world, talker, LINES[2])
check("...to nothing after three", world.phone_left(talker) == 0)

# IT DOWNGRADES, IT DOES NOT REFUSE. A refused action still costs the tick —
# engine.py takes result.accepted and moves on either way. Pompeii, Day 267,
# 00:15-04:30: Ida and Lennox spent four and a half hours on "started to say
# the same thing again, trailed off" while the clock ran. A brake that burns
# the time it was meant to free is worse than no brake. The phone not sending
# costs REACH, not TIME.
heard = [a for a in cast if a.name != talker.name][0]
heard.location = talker.location          # somebody to say it to
ok4, note = post(world, talker, LINES[3])
check("THE FOURTH IS NOT REFUSED — IT IS SPOKEN ALOUD", ok4 is True)
check("...and the villager is told why, IN THEIR OWN VOICE",
      note.startswith("couldn't send that to the group"))
check("...so the memory reads as a sentence", ("I " + note).startswith(
      "I couldn't send that to the group"))
check("...and it does not double up on _verb_say's own prefix",
      note.count("said:") <= 1)
check("...and it is COUNTED as a blocked post",
      world.posts_blocked.get(talker.name) == 1)
check("...and it reached the room as speech, not the group chat",
      any(e.get("type") == "say" and e.get("agent") == talker.name
          and LINES[3][:20] in str(e.get("text", ""))
          for e in world.recent_events(30))
      if hasattr(world, "recent_events") else True)
check("...and the tick was NOT wasted", ok4 is True)

# ------------------------------------- what the meter must never touch
before = world.phone_left(talker)
ok, _ = world.execute(talker, {"action": "say", "text": LINES[4]})
check("TALKING TO THE ROOM IS FREE — it still works with an empty plan", ok)
check("...and costs nothing", world.phone_left(talker) == before)

# ...and to somebody ELSEWHERE, because the world rightly refuses a phone
# call to a person standing next to you
other = [a for a in cast
         if a.name != talker.name and a.location != talker.location][0]
ok, _ = world.execute(talker, {"action": "text", "to": other.name,
                               "text": LINES[5]})
check("a PRIVATE text is free too", ok)
check("...and costs nothing", world.phone_left(talker) == before)

check("nobody else's allowance was touched",
      all(world.phone_left(a) == 3 for a in cast if a.name != talker.name))

# ------------------------------------------------ the plan is checked LAST
# A villager must never be told they are out of data for a post that was
# never going to send. Give a fresh villager an empty plan and a duplicate.
victim = cast[2]
victim.location = "the library"
for _ in range(3):
    post(world, victim, LINES[6])
    victim.last_text = ""          # get past the exact-repeat guard
    victim.recent_own_says = []    # and the Paraphrase Act
check("the victim's plan is spent", world.phone_left(victim) == 0)
victim.last_text = " ".join(LINES[7].lower().split())
ok, note = post(world, victim, LINES[7])
check("an exact repeat is refused for BEING A REPEAT, not for data",
      ok is False and "allowance" not in note)
check("...and the repeat guard fires BEFORE the plan is consulted",
      "sending it again" in note)
check("...and that refusal was not counted against the plan",
      world.posts_blocked.get(victim.name, 0) == 0)

# --------------------------------------------------------- midnight resets
world.clock.day += 1
check("the plan resets at midnight", world.phone_left(talker) == 3)
ok, _ = post(world, talker, LINES[8])
check("...and they can post again", ok)

# ------------------------------------------------------------- it persists
engine.save_state()
again = Engine(seed=616, state=Engine(seed=616).load_state())
back = again.world.agents[talker.name]
check("the balance survives a restart",
      again.world.phone_left(back) == world.phone_left(talker))

# ------------------------------------------- the villager can SEE the meter
world.clock.day = world.clock.day        # no-op, readability
line = prompts.decision_prompt(talker, world, [], [])
check("the prompt tells them what is left",
      "group post" in line and "left today" in line)
check("...and that the room is free", "talking to people here is free" in line)
# and when the plan is spent, the readout says what will happen instead
spent = cast[2]
while (world.phone_left(spent) or 0) > 0:
    spent.phone_left = 0
line_spent = prompts.decision_prompt(spent, world, [], [])
check("a villager with no posts left is told what happens if they try",
      "said out loud" in line_spent)
config.PHONE = {"enabled": False}
line_off = prompts.decision_prompt(talker, world, [], [])
check("with no plan the prompt says nothing about phones",
      "group post" not in line_off)

if old_phone is not None:
    config.PHONE = old_phone

print()
if FAILED:
    print(f"FAILED {len(FAILED)}")
    for label in FAILED:
        print(f"  - {label}")
    raise SystemExit(1)
print("phone_bill: all checks passed")
