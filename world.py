"""The world: locations, clock, and the physics that keep models honest.

Rule one, inherited from a previous administration: the world enforces
reality, not the models. An agent SAYS "I take the bread"; the world
decides whether there is bread. Liars get caught by physics.
"""

import json
import os
import re
import threading

import config


_WORDS = re.compile(r"[a-z0-9']+")


def _toks(norm):
    return set(_WORDS.findall(norm))


# Models sometimes leak JSON fragments into their speech, e.g.
#   'Mornin' Roy!', 'to': 'Roy Tibbs'
# Cut everything from a leaked  ', 'to':  /  ", "to":  onward, then strip
# stray wrapping quotes.
_JSON_LEAK = re.compile(r"""['"]\s*,\s*['"]?(to|action|text|note)['"]?\s*:.*$""",
                        re.IGNORECASE | re.DOTALL)


def _sanitize_speech(text):
    stripped = _JSON_LEAK.sub("", text).strip()
    # if the leak-strip fired, it consumed the closing quote — drop the orphan opener
    if stripped != text.strip() and stripped[:1] in "'\"":
        stripped = stripped[1:].strip()
    text = stripped
    # unwrap matching pairs of stray outer quotes (repeat if nested)
    while len(text) > 1 and text[0] == text[-1] and text[0] in "'\"":
        text = text[1:-1].strip()
    return text


class Clock:
    def __init__(self):
        h, m = map(int, config.SIM_START.split(":"))
        self.day = 1
        self.minutes = h * 60 + m
        self.step = config.PACING[config.PACING_MODE]["sim_minutes_per_tick"]

    def tick(self):
        self.minutes += self.step
        if self.minutes >= 24 * 60:
            self.minutes -= 24 * 60
            self.day += 1

    @property
    def hhmm(self):
        return f"{self.minutes // 60:02d}:{self.minutes % 60:02d}"

    @property
    def label(self):
        return f"Day {self.day}, {self.hhmm}"

    @property
    def is_night(self):
        return self.minutes >= 22 * 60 + 30 or self.minutes < 6 * 60 + 30

    def at(self, hhmm):
        """True if the current tick covers sim time hhmm."""
        h, m = map(int, hhmm.split(":"))
        t = h * 60 + m
        prev = self.minutes - self.step
        if prev < 0:
            return t >= prev + 24 * 60 or t < self.minutes
        return prev < t <= self.minutes


class World:
    def __init__(self, cast):
        self.clock = Clock()
        self.tick_no = 0
        self.agents = {a.name: a for a in cast}
        self.locations = dict(config.LOCATIONS)
        for a in cast:
            self.locations[a.home] = {"desc": f"{a.name}'s house", "home_of": a.name}
        self.events = []           # rolling event feed for the UI
        self.recent_says = []      # (tick, location, normalized_text, speaker) for the Echo Ban
        self._events_lock = threading.Lock()
        self._event_seq = 0
        os.makedirs("data", exist_ok=True)
        self._jsonl = open(config.TRANSCRIPT_JSONL, "a", encoding="utf-8")
        self._log = open(config.TRANSCRIPT_LOG, "a", encoding="utf-8")

    # ------------------------------------------------------------ helpers
    def occupants(self, loc, exclude=None):
        return [a for a in self.agents.values()
                if a.location == loc and a.name != exclude and not a.asleep]

    def public_locations(self):
        return [k for k in self.locations if "home_of" not in self.locations[k]]

    def food_locations(self):
        return [k for k, v in self.locations.items() if v.get("sells_food")]

    def radio_location(self):
        for k, v in self.locations.items():
            if v.get("radio"):
                return k
        return None

    # ------------------------------------------------------------- events
    def emit(self, etype, agent, text, loc, target=None, deliver=True):
        """Record an event; queue it as a perception for co-located agents."""
        self._event_seq += 1
        ev = {
            "seq": self._event_seq,
            "tick": self.tick_no,
            "day": self.clock.day,
            "sim_time": self.clock.hhmm,
            "type": etype,
            "agent": agent,
            "location": loc,
            "target": target,
            "text": text,
        }
        with self._events_lock:
            self.events.append(ev)
            if len(self.events) > 600:
                self.events = self.events[-400:]
        self._jsonl.write(json.dumps(ev) + "\n")
        self._jsonl.flush()
        who = f"{agent} -> {target}" if target else (agent or "WORLD")
        self._log.write(f"[{self.clock.label}] [{loc}] {etype.upper():8s} {who}: {text}\n")
        self._log.flush()
        if deliver:
            self._deliver(ev)
        return ev

    def _deliver(self, ev):
        """Turn an event into perceptions for agents who'd notice it."""
        interrupts = ev["type"] in ("say", "radio")
        for a in self.agents.values():
            if a.name == ev["agent"] or a.location != ev["location"] or a.asleep:
                continue
            if ev["type"] == "say":
                if ev["target"] == a.name:
                    line = f'{ev["agent"]} said to you: "{ev["text"]}"'
                elif ev["target"]:
                    line = f'You overheard {ev["agent"]} tell {ev["target"]}: "{ev["text"]}"'
                else:
                    line = f'{ev["agent"]} said: "{ev["text"]}"'
            elif ev["type"] == "radio":
                line = f"The radio: {ev['text']}"
            elif ev["type"] == "arrive":
                line = f"{ev['agent']} arrived at {ev['location']}."
            elif ev["type"] == "leave":
                line = f"{ev['agent']} left, heading to {ev['text']}."
            else:
                line = f"{ev['agent']}: {ev['text']}"
            a.pending.append({
                "text": line,
                "interrupt": interrupts or ev.get("target") == a.name,
                "sim_time": ev["sim_time"],
            })

    # ------------------------------------------------------------ physics
    def execute(self, agent, action):
        """Validate and apply a brain's chosen action. Returns (ok, summary)."""
        act = (action or {}).get("action", "idle")
        agent.last_action = action

        if act == "move":
            dest = action.get("to", "")
            dest = self._resolve_location(agent, dest)
            if not dest:
                return False, f"tried to go somewhere that doesn't exist ({action.get('to')!r}); stayed put"
            if dest == agent.location:
                return True, f"stayed at {dest}"
            self.emit("leave", agent.name, dest, agent.location)
            agent.location = dest
            agent.activity = None
            agent.soapbox = 0
            self.emit("arrive", agent.name, f"arrived at {dest}", dest)
            return True, f"went to {dest}"

        if act == "say":
            text = _sanitize_speech((action.get("text") or "").strip())
            if not text:
                return False, "opened their mouth and nothing came out"
            norm = " ".join(text.lower().split())
            if norm == agent.last_say:
                agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                                  "note": "caught themselves about to repeat the same line and thought better of it"}
                self.emit("action", agent.name,
                          "started to say the same thing again, trailed off",
                          agent.location, deliver=False)
                return False, ("already said exactly that — repeating it changes nothing; "
                               "do something different")
            # Echo Ban: parroting a line anyone nearby said recently is not talking
            for (t, loc, n, who) in self.recent_says:
                if n == norm and loc == agent.location and \
                        self.tick_no - t <= 8 and who != agent.name:
                    agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                                      "note": "almost repeated someone else's words verbatim, stopped"}
                    return False, (f"that's word-for-word what {who} just said — "
                                   "parroting isn't a contribution; say something of "
                                   "your OWN or act")
            # Paraphrase Act: rewording your own broken record is still a broken record
            toks = _toks(norm)
            for old in agent.recent_own_says[-6:]:
                union = toks | old
                if union and len(toks & old) / len(union) > 0.5:
                    agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                                      "note": "realized they've been saying the same thing all day"}
                    return False, ("has been saying versions of that same thing over and "
                                   "over — everyone has stopped listening. Do something "
                                   "NEW: go somewhere else, text someone, take an action")
            # Soapbox Law: three speeches to an empty room is a cry for help
            audience = self.occupants(agent.location, exclude=agent.name)
            if not audience and not action.get("to"):
                agent.soapbox += 1
                if agent.soapbox >= 3:
                    agent.activity = {"type": "idle", "until_tick": self.tick_no + 3,
                                      "note": "looked around the empty room and let the speech die"}
                    return False, ("there is NOBODY here — this is the third speech to an "
                                   "empty room. Talking to furniture accomplishes nothing; "
                                   "GO where the people are, or text someone")
            else:
                agent.soapbox = 0
            agent.recent_own_says.append(toks)
            if len(agent.recent_own_says) > 6:
                agent.recent_own_says = agent.recent_own_says[-6:]
            agent.last_say = norm
            self.recent_says.append((self.tick_no, agent.location, norm, agent.name))
            if len(self.recent_says) > 24:
                self.recent_says = self.recent_says[-16:]
            target = action.get("to")
            if target:
                target = self._resolve_agent(target)
                if not target or self.agents[target].location != agent.location:
                    # talking to someone who isn't here — the town notices
                    self.emit("say", agent.name, text, agent.location, target=None)
                    return True, f"said (to no one in particular): {text}"
            self.emit("say", agent.name, text, agent.location, target=target)
            agent.relationships.setdefault(target, 0)
            if target:
                agent.relationships[target] = agent.relationships.get(target, 0) + 1
            return True, f"said: {text}"

        if act == "text":
            text = _sanitize_speech((action.get("text") or "").strip())
            if not text:
                return False, "stared at their phone, typed nothing"
            # SMS repetition laws (same code as speech: no reruns, no paraphrase spam)
            norm = " ".join(text.lower().split())
            if norm == agent.last_text:
                return False, "already sent exactly that text — sending it again helps no one"
            toks = _toks(norm)
            for old in agent.recent_own_says[-6:]:
                union = toks | old
                if union and len(toks & old) / len(union) > 0.5:
                    return False, ("that text says the same thing they've been "
                                   "saying/sending all day — say something NEW or act")
            to_raw = str(action.get("to") or "").strip().lower()
            if to_raw in ("everyone", "all", "town", "the town", "group", "group chat",
                          "pepperton_gossip", "pepperton gossip", "the group"):
                # No posting AT people who are standing next to you
                others_awake = [a for a in self.agents.values()
                                if a.name != agent.name and not a.asleep]
                if others_awake and all(a.location == agent.location for a in others_awake):
                    return False, ("looked up from their phone — every single person "
                                   "in town is standing in this room. Posting to the "
                                   "group chat would be absurd; just SPEAK")
                # Pepperton_Gossip: the town group chat (built by popular demand)
                agent.last_text = norm
                agent.recent_own_says.append(toks)
                self.emit("gossip", agent.name, text, agent.location, deliver=False)
                for other in self.agents.values():
                    if other.name == agent.name:
                        continue
                    other.pending.append({
                        "text": (f'Your phone buzzes — Pepperton_Gossip group chat, '
                                 f'{agent.name} posted: "{text}" (the whole town saw '
                                 f'this; reply to the group by texting "everyone", or '
                                 f'text someone privately)'),
                        "interrupt": True,
                        "sim_time": self.clock.hhmm,
                    })
                return True, f"posted to Pepperton_Gossip: {text}"
            target = self._resolve_agent(action.get("to"))
            if not target:
                contacts = ", ".join(n for n in self.agents if n != agent.name)
                return False, (f"has no contact named {action.get('to')!r} — "
                               f"the only contacts in this phone are: {contacts}")
            if target == agent.name:
                return False, "almost texted themselves"
            tgt = self.agents[target]
            if tgt.location == agent.location and not tgt.asleep:
                return False, (f"{target} is standing RIGHT THERE in the same room — "
                               "texting them would be ridiculous; just talk to them")
            self.emit("text", agent.name, text, agent.location, target=target,
                      deliver=False)
            agent.last_text = norm
            agent.recent_own_says.append(toks)
            tgt.pending.append({
                "text": (f'Your phone buzzes — text from {agent.name}: "{text}" '
                         f'({agent.name} is NOT here with you — speaking out loud '
                         f'will not reach them; reply with the "text" action)'),
                "interrupt": True,
                "sim_time": self.clock.hhmm,
            })
            return True, f"texted {target}: {text}"

        if act == "eat":
            if agent.needs["fullness"] >= 85:
                return False, "couldn't eat another bite — completely full"
            loc = self.locations.get(agent.location, {})
            if loc.get("sells_food"):
                if agent.money < config.MEAL_COST:
                    self.emit("action", agent.name,
                              "counted their money and quietly put the menu down",
                              agent.location)
                    return False, f"couldn't afford a meal (${agent.money:.0f})"
                agent.money -= config.MEAL_COST
                agent.needs["fullness"] = min(100, agent.needs["fullness"] + config.MEAL_FULLNESS)
                agent.activity = {"type": "idle", "until_tick": self.tick_no + 2,
                                  "note": "digesting"}
                self.emit("action", agent.name, f"had a meal at {agent.location}", agent.location)
                return True, f"ate at {agent.location} (-${config.MEAL_COST})"
            if agent.location == agent.home:
                if agent.pantry <= 0:
                    return False, ("found the cupboard bare — the pantry restocks "
                                   "overnight; a real meal means going into town")
                agent.pantry -= 1
                agent.needs["fullness"] = min(100, agent.needs["fullness"] + 30)
                agent.activity = {"type": "idle", "until_tick": self.tick_no + 2,
                                  "note": "digesting"}
                self.emit("action", agent.name, "fixed something to eat at home", agent.location)
                return True, f"ate at home ({agent.pantry} servings left today)"
            return False, f"there's no food at {agent.location}"

        if act == "drink":
            loc = self.locations.get(agent.location, {})
            if not loc.get("bar"):
                return False, f"there's no bar at {agent.location} — the Rusty Tap is the place for that"
            if agent.money < config.DRINK_COST:
                self.emit("action", agent.name,
                          "checked their pockets and ordered a water", agent.location)
                return False, f"couldn't cover a drink (${agent.money:.0f})"
            agent.money -= config.DRINK_COST
            agent.drink_ticks = [t for t in agent.drink_ticks
                                 if self.tick_no - t <= config.TIPSY_TICKS * 2]
            agent.drink_ticks.append(self.tick_no)
            n = len(agent.drink_ticks)
            if n >= config.DRUNK_AT:
                self.emit("action", agent.name,
                          f"ordered round {n} — the room is starting to tilt",
                          agent.location)
            else:
                self.emit("action", agent.name, "ordered a drink at the bar",
                          agent.location)
            return True, f"had a drink (-${config.DRINK_COST}, round {n})"

        if act == "work":
            wp = agent.workplace()
            if not wp:
                return False, f"is {agent.job} and has no shift to work"
            if agent.location != wp:
                return False, f"can't work here — their work is at {wp}"
            agent.activity = {"type": "work", "until_tick": self.tick_no + 16,
                              "note": action.get("note", "")}
            self.emit("action", agent.name, f"started a shift at {wp}", wp)
            return True, f"working at {wp}"

        if act == "rest":
            if agent.needs["energy"] >= 85 and not self.clock.is_night:
                return False, ("is wide awake — lying down now would be pointless. "
                               "The day is out there, and so is everyone else")
            if agent.location == agent.home:
                agent.asleep = True
                agent.activity = {"type": "rest", "until_tick": None, "note": ""}
                self.emit("action", agent.name, "turned in to rest", agent.location)
                return True, "resting at home"
            if agent.location == "the park":
                agent.activity = {"type": "nap", "until_tick": self.tick_no + 8, "note": ""}
                self.emit("action", agent.name, "dozed off on a park bench", agent.location)
                return True, "napping in the park"
            return False, f"can't properly rest at {agent.location} — home or the park"

        # idle / think / anything unrecognized
        note = (action.get("note") or action.get("text") or "passed the time").strip()
        agent.activity = {"type": "idle", "until_tick": self.tick_no + 4, "note": note}
        self.emit("action", agent.name, note, agent.location, deliver=False)
        return True, f"idled: {note}"

    # ---------------------------------------------------------- resolvers
    def _resolve_location(self, agent, name):
        if not name:
            return None
        name = str(name).strip()
        if name.lower() in ("home", "my house", "house"):
            return agent.home
        for loc in self.locations:
            if loc.lower() == name.lower():
                return loc
        # lenient contains-match: models abbreviate ("diner", "the store")
        for loc in self.locations:
            if name.lower() in loc.lower() or loc.lower() in name.lower():
                return loc
        return None

    def _resolve_agent(self, name):
        if not name:
            return None
        name = str(name).strip()
        for n in self.agents:
            if n.lower() == name.lower():
                return n
        for n in self.agents:
            if name.lower() in n.lower():
                return n
        return None
