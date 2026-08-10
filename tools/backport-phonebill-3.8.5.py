#!/usr/bin/env python3
"""Backport THE PHONE BILL onto a v3.8.5 town that already has the makework patch.

WHY: measured on Pepperton, days 164-184, ten villagers —

    GOSSIP  3,790     18.0 group broadcasts per villager per day
    SAY       524      2.5
    ACTION    720      3.4     <- everything anyone DID, movement included

Six utterances for every act. Speech is the only free action in this world:
a meal is $5, a drink $4, a room $8, rent falls every three days, permits
carry fines and houses get seized — and broadcasting to the entire town, from
anywhere, costs nothing and can be done every fifteen minutes forever. A tick
spent posting is a tick not spent working.

Three brakes already exist and all three fail. The Paraphrase Act is a >50%
token-overlap test and villagers repeat MEANING, not words. The Soapbox Law
needs an empty room. TALK_STREAK_NUDGE fired for four of Vera Tibbs' seven
consecutive posts and she posted straight through it. Recitation is caught;
paraphrase is not. A rate limit is the only brake that ignores content.

WHAT IT METERS: group broadcasts only, against a daily allowance.

WHAT IT NEVER TOUCHES: talking to the people in the room is free and always
will be — that needs no carrier. Private texts are free. And the plan is
checked LAST, so nobody is told they are out of data for a post that was
never going to send.

THE ALLOWANCE IS UNCONDITIONAL. It cannot be bought, so it cannot be denied.
It refills regardless of money, of employment, of whether a single door in
that town ever opens again. Gating it behind a shop would build the meal
famine a third time, in the one channel they use to organise out of it.

SHIPS INERT: the code reads getattr(config, "PHONE", None) and defaults off.
Arm it by appending to the town's own config.py:

    PHONE = {"enabled": True, "free_posts_per_day": 6}

USAGE:  stop the town, then from inside the town's folder:

    python3 pepperton-phonebill-3.8.5.py

Safe to re-run. Keeps .bak-phone of each file. Refuses and writes NOTHING
unless the makework backport is already applied.
"""

import shutil
import sys

PHONE_METHOD = '    def phone_left(self, agent):\n        """Group-chat posts this villager has left today, or None if the plan\n        is off. (v3.11)\n\n        Speech is the only free action in this world. A meal is $5, a drink\n        $4, a room $8; rent falls every three days and houses get seized —\n        and broadcasting to every villager at once costs nothing and can be\n        done every fifteen minutes forever. Pepperton days 164-184: 3,790\n        group posts against 720 actions of any kind. A tick spent posting is\n        a tick not spent working.\n\n        Talking to the people in the room with you is FREE and always will\n        be: that needs no carrier. This meters the broadcast only.\n\n        Refilled lazily on the day it is asked for, so it needs no hook in\n        the tick loop and consumes no randomness."""\n        cfg = getattr(config, "PHONE", None) or {}\n        if not cfg.get("enabled"):\n            return None\n        if getattr(agent, "phone_day", 0) != self.clock.day:\n            agent.phone_day = self.clock.day\n            agent.phone_left = int(cfg.get("free_posts_per_day", 6))\n        return agent.phone_left\n'
GATE = '            # THE PHONE BILL (v3.11): the plan is checked LAST, after every\n            # other reason a post could fail, so a villager is never told\n            # they are out of data for a post that was never going to send.\n            left = self.phone_left(agent)\n            if left is not None:\n                if left <= 0:\n                    # IT DOWNGRADES, IT DOES NOT REFUSE.\n                    #\n                    # A refused action still costs the villager their tick —\n                    # engine.py takes result.accepted and moves on either way.\n                    # Pompeii, Day 267, 00:15-04:30: Ida Merriweather and\n                    # Lennox Tibbs spent four and a half hours on\n                    # "started to say the same thing again, trailed off",\n                    # the Paraphrase Act refusing them over and over while\n                    # the clock ran. A brake that burns the time it was\n                    # meant to free is worse than no brake.\n                    #\n                    # So the phone not sending costs REACH, not TIME: the\n                    # words come out of their mouth instead, to whoever is\n                    # standing there, for free. Same class as an unknown\n                    # verb becoming "passed the time" — the world\'s handling\n                    # of an action it cannot perform as asked. If the room\n                    # is empty the Soapbox Law is already waiting.\n                    self.posts_blocked[agent.name] = \\\n                        self.posts_blocked.get(agent.name, 0) + 1\n                    spoke, note = self._verb_say(\n                        agent, {"action": "say", "text": text})\n                    if spoke:\n                        return True, ("their phone wouldn\'t send it — the "\n                                      "group-chat allowance is spent until "\n                                      "midnight — so they said it out loud "\n                                      "instead: " + note)\n                    return False, ("their phone won\'t send it (allowance spent "\n                                   "until midnight) and saying it aloud did "\n                                   "not land either: " + note)\n                agent.phone_left = left - 1\n'
PROMPT_BLOCK = '    # THE PHONE BILL (v3.11) — a status readout, in the same class as their\n    # money. Without it a refused post is inexplicable, and a villager who\n    # cannot see the constraint cannot budget against it. It says nothing\n    # about what to do with the balance.\n    phone_note = ""\n    _pl = world.phone_left(agent) if hasattr(world, "phone_left") else None\n    if _pl is not None:\n        phone_note = (\n            (f". Phone: {_pl} group post{\'\' if _pl == 1 else \'s\'} left today "\n             f"(talking to people here is free)")\n            if _pl > 0 else\n            ". Phone: no group posts left today — anything you try to post "\n            "gets said out loud to whoever is here instead, and the plan "\n            "resets at midnight")\n'

EDITS = {
    "sim/agents.py": [
        ("        self.workplace_at = None    # v3.11: set when a post is claimed",
         "        self.workplace_at = None    # v3.11: set when a post is claimed\n"
         "        self.phone_day = 0          # v3.11: the day this allowance belongs to\n"
         "        self.phone_left = 0         # v3.11: group posts left today",
         "agents: the phone carries a balance"),
    ],
    "sim/engine.py": [
        ('                 "workplace_at"]',
         '                 "workplace_at", "phone_day", "phone_left"]',
         "engine: the balance persists with the villager"),
    ],
    "sim/world.py": [
        ("        self.extra_workplaces = {}",
         "        self.extra_workplaces = {}\n"
         "        # THE PHONE BILL: posts the plan refused, per villager.\n"
         "        # Operator-facing only — never serialised, never shown to a\n"
         "        # villager beyond their own remaining balance.\n"
         "        self.posts_blocked = {}",
         "world: refused posts are counted"),
        ("    def _verb_text(self, agent, action):",
         PHONE_METHOD + "\n    def _verb_text(self, agent, action):",
         "world: the meter"),
        ("            # Pepperton_Gossip: the town group chat (built by popular demand)\n"
         "            agent.last_text = norm",
         GATE + "            # Pepperton_Gossip: the town group chat (built by popular demand)\n"
         "            agent.last_text = norm",
         "world: a broadcast costs one post"),
    ],
    "sim/prompts.py": [
        ("Your money: ${agent.money:.0f}{money_tag}. Your needs:",
         "Your money: ${agent.money:.0f}{money_tag}{phone_note}. Your needs:",
         "prompts: the balance is on their money line"),
        ('    mem_lines = "\\n".join(',
         PROMPT_BLOCK + '    mem_lines = "\\n".join(',
         "prompts: they can see what is left"),
    ],
}


def main():
    try:
        if "extra_workplaces" not in open("sim/world.py", encoding="utf-8").read():
            sys.exit("ABORT: the makework backport is not applied to this town.\n"
                     "       NOTHING WAS WRITTEN.")
    except OSError as exc:
        sys.exit("ABORT: cannot read sim/world.py (%s).\n"
                 "       Run this from inside the town's folder." % exc)
    wrote = False
    for path, edits in EDITS.items():
        text = open(path, encoding="utf-8").read()
        touched = False
        for old, new, label in edits:
            if new in text:
                print("  [already applied] %s" % label)
                continue
            found = text.count(old)
            if found != 1:
                sys.exit("ABORT: %s — expected exactly 1 match in %s, found %d.\n"
                         "       NOTHING WAS WRITTEN." % (label, path, found))
            text = text.replace(old, new, 1)
            touched = True
            print("  [patched] %s" % label)
        if touched:
            shutil.copy(path, path + ".bak-phone")
            open(path, "w", encoding="utf-8").write(text)
            wrote = True
    if not wrote:
        print("\nno changes needed — already patched.")
        return
    print("\nwritten (.bak-phone kept beside each file).")
    print("THIS PATCH ALONE CHANGES NOTHING. To arm it, append to config.py:")
    print('    PHONE = {"enabled": True, "free_posts_per_day": 6}')


if __name__ == "__main__":
    main()
