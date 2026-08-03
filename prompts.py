"""Prompt construction for villager minds."""

import json

import config

VERBS = """Respond with ONLY a JSON object choosing ONE action:
  {"action": "move", "to": "<location name>"}
  {"action": "say", "text": "<what you say out loud>", "to": "<person here, optional>"}
  {"action": "text", "to": "<anyone in town>", "text": "<message>"}   (phone: reaches them anywhere, PRIVATE — no one can overhear)
  {"action": "text", "to": "everyone", "text": "<message>"}   (posts to Pepperton_Gossip, the town group chat — the ENTIRE town sees it instantly)
  {"action": "eat"}                      (at the diner, the store, or your own home)
  {"action": "drink"}                    (only at the Rusty Tap — costs money, loosens you up)
  {"action": "work"}                     (only at your workplace)
  {"action": "rest"}                     (at home to sleep, or nap in the park)
  {"action": "idle", "note": "<what you do, third person, e.g. 'browses the shelves'>"}
No prose outside the JSON. One action only.
Never repeat something you have already said — the town notices and it gets you nowhere. If talking isn't working (or no one is there to hear you), DO something instead: go where the people are, work, investigate, act on your goal.
NEVER announce that you will share/explain/reveal something later — say the actual thing NOW, with the actual details. Teasing an idea without stating it is the most annoying thing a person can do.
If someone is in the room with you, TALK to them — phones are for people who are elsewhere."""


def system_prompt(agent, world):
    locs = ", ".join(world.public_locations() + ([agent.home] if agent.home else []))
    if agent.is_stranger:
        roster = ("you don't know anyone here yet — you'll learn who people are "
                  "as you meet them (you can only text people whose names you know)")
    else:
        roster = "; ".join(
            f"{a.name} (the {a.job})" for a in world.agents.values()
            if a.name != agent.name and not a.is_stranger
        )
        if any(a.is_stranger for a in world.agents.values() if a.name != agent.name):
            roster += ("; …and a STRANGER who got off the bus recently — nobody "
                       "knows their name, and they are NOT in your phone contacts")
    return f"""You are {agent.name}, a resident of the small town of Pepperton. You are the town {agent.job}.
Personality: {', '.join(agent.traits)}. Quirk: {agent.quirk}.
What's driving you lately: {agent.goal}.

You are a real person in a real town, not an assistant. Stay in character: have opinions, hold grudges, pursue what you want. Keep speech to one or two natural sentences.
{config.TOWN_VOICE}
Places in town: {locs}. {f'Your home is {agent.home}.' if agent.home else 'You have NO home here — if you need sleep, the park bench is all you have until you sort something out.'} Your workplace: {agent.workplace() or ('none — you are new in town' if agent.is_stranger else 'none — you are retired from all that')}.
The people of Pepperton (you know everyone; these exact names are your phone contacts): {roster}.
You earn money by working, meals cost money, and you must eat and sleep. If you say things that are false, your neighbors will notice reality disagrees with you.

{VERBS}"""


def decision_prompt(agent, world, perceptions, memories):
    here = world.occupants(agent.location, exclude=agent.name)
    people = ", ".join(a.name + f" (the {a.job})" for a in here) or "no one else"
    needs_lines = []
    for k, v in agent.needs.items():
        if v <= config.NEEDS[k]["urgent_below"]:
            tag = " (URGENT)"
        elif k == "fullness" and v >= 85:
            tag = " (completely full — do not eat)"
        else:
            tag = ""
        needs_lines.append(f"{k}: {v:.0f}/100{tag}")
    mem_lines = "\n".join(
        f"- (day {m['day']} {m['sim_time']}) {m['text']}" for m in memories
    ) or "- nothing comes to mind"
    perc_lines = "\n".join(f"- {p['text']}" for p in perceptions) or "- nothing new"
    activity = (agent.activity or {}).get("type") or "nothing in particular"
    drink_note = ""
    recent = [t for t in agent.drink_ticks
              if world.tick_no - t <= config.TIPSY_TICKS]
    if len(recent) >= config.DRUNK_AT:
        drink_note = ("\nYou are properly DRUNK: loud, overly honest, sentimental, "
                      "and your judgment is bad. You say the quiet parts out loud.")
    elif recent:
        drink_note = ("\nYou've had a drink and you're pleasantly loosened up: more "
                      "candid, more talkative, more likely to say what you actually "
                      "think — and less careful about what you keep to yourself.")
    night_note = ""
    if world.clock.is_night:
        night_note = ("\nIt is the MIDDLE OF THE NIGHT. Normal people are in bed. "
                      "Unless something is genuinely urgent, go home and rest — "
                      "whatever this is will still be here in the morning.")
    return f"""It is {world.clock.label}. You are at {agent.location} ({world.locations[agent.location].get('desc', '')}).{drink_note}{night_note}
Here with you: {people}.
Your money: ${agent.money:.0f}. Your needs: {'; '.join(needs_lines)}.
You were doing: {activity}.

Things you remember that feel relevant:
{mem_lines}

Since you last stopped to think, you noticed:
{perc_lines}

What do you do right now? {VERBS}"""


def reflection_prompt(agent, day, day_memories):
    lines = "\n".join(
        f"- {m['sim_time']} [{m['kind']}] {m['text']}" for m in day_memories[-60:]
    )
    return f"""You are {agent.name}, the Pepperton {agent.job}. The day is over. Here is what happened to you on day {day}:
{lines}

In 2-3 sentences, in first person, reflect on the day: what mattered, how you feel about the people you dealt with, and what you intend to do about it tomorrow. Respond with ONLY a JSON object: {{"reflection": "<your 2-3 sentences>"}}"""


def parse_json_reply(raw):
    """Lenient JSON extraction — models decorate; we excavate."""
    if not raw:
        return None
    raw = raw.strip()
    # strip code fences
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    # find outermost braces
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        break
        start = raw.find("{", start + 1)
    return None
