#!/usr/bin/env python3
"""Backport v3.11.0 — THE TOWN CAN MAKE WORK — onto a v3.8.5 town.

WHY: Pepperton, 182 days, measured — 298 shifts, 95 odd jobs, and 491 BUILD
actions. Building is the largest category of labour in that town's history
and it cannot produce one dollar. A finished project appends a clause to a
room's description and stops: no till, so the bus can never pay it; no line
in config.WORKPLACES, a dict written before Day 1 that can never grow, so
nobody can be employed at it. And open_positions() served only villagers
WITHOUT work, with no quit verb — so seven of the ten people who built those
things could not have taken a post at one if it had existed.

WHAT IT DOES
  * a finished project (not an inn, not a house) becomes a LOCATION
  * its till opens at $0.00 — NEVER TILL_SEED. Seeding a built till would
    mint money from nothing. It opens dry, which the Tibbs Door permits.
  * it creates a post, "keeper of {project}", in a persisted
    world.extra_workplaces merged over config.WORKPLACES
  * TAKING A JOB RELEASES THE ONE YOU HOLD — working a vacant post transfers
    you and puts your old post back on the board the same tick, for anyone
  * the contributors are told once, on completion, that the post is unclaimed
  * it REACHES BACK: anything already finished gets its post and dry till at
    the next boot

WHAT IT DOES NOT DO: it does not touch prompts.py. `build` keeps "real work"
and `work` keeps "(only at your workplace)". Changing the economy and the
prompt together would make the result unreadable.

THIS MOVES THE GOLDEN HASH. It is new physics, not a refactor. That is
expected and is recorded in claude/PREREG-THE-TOWN-CAN-MAKE-WORK.md, which
was frozen before a line of it existed.

USAGE:  stop the town, then from inside the town's folder:

    python3 pepperton-makework-3.8.5.py

Safe to re-run. Keeps a .bak-makework of every file it edits. Refuses and
writes NOTHING if the town is not the version it expects. To undo, restore
the three .bak-makework files and restart.

RUN IT AGAINST A COPY OF THE TOWN FIRST.

Verified on a clean v3.8.5 checkout: 306-check suite green, tests/made_work.py
green, no money minted at creation, transfer or restore.
(claude/NOTHING-THEY-BUILD-MAKES-MONEY.md)
"""

import shutil
import sys

EDITS = {
    "sim/world.py": [
        (
            """        self._permit_day_done = 0""",
            """        self._permit_day_done = 0
        # THE TOWN CAN MAKE WORK (v3.11 backport): posts this town created
        # for itself by finishing something. {job: location}. Merged over
        # config.WORKPLACES by open_positions(); persisted in save_state.
        self.extra_workplaces = {}""",
            "world: the job book can grow",
        ),
        (
            """        held = {a.job for a in self.agents.values()}
        out = {}
        for job, wp in getattr(config, "WORKPLACES", {}).items():
            if wp and wp in self.locations and job not in held:
                out[job] = wp
        return out""",
            """        held = {a.job for a in self.agents.values()}
        book = dict(getattr(config, "WORKPLACES", {}))
        book.update(getattr(self, "extra_workplaces", {}))
        out = {}
        for job, wp in book.items():
            if wp and wp in self.locations and job not in held:
                out[job] = wp
        return out

    def vacancy_digest(self, limit=5):
        \"\"\"SITUATIONS VACANT, capped and rotated. (v3.11)

        A town that has finished forty things has forty posts open, and
        Pepperton has finished forty. Listing all of them every evening is
        not information, it is a wall — MEMORY_TOP_K is 8 and no villager
        can hold thirty-eight of anything. So it names a handful and SAYS
        HOW MANY IT DID NOT NAME: the town is not told less than the truth,
        it is told the truth in a size it can carry.

        The window rotates by day, so a different handful surfaces each
        evening instead of the alphabetical first five forever.
        Deterministic — consumes no randomness.\"\"\"
        openings = sorted(self.open_positions().items())
        if not openings:
            return ""
        total = len(openings)
        if total <= limit:
            shown, rest = openings, 0
        else:
            # step by the PAGE, not by one: forty posts five at a time
            # cycles in eight days instead of forty, so every post is named
            # more than once inside a twenty-day window.
            offset = (self.clock.day * limit) % total
            shown = (openings + openings)[offset:offset + limit]
            rest = total - limit
        text = "; ".join(f"{j} at {w}" for j, w in shown)
        if rest:
            text += f"; and {rest} more posts nobody holds"
        return text

    def make_workplace(self, proj):
        \"\"\"A finished project becomes a place a person can be PAID to stand in.

        Returns the new location name, or None if this project is not that
        kind of thing (inns give beds, houses give homes) or it already has
        one. Called on completion and once at boot for anything finished
        before this law existed.

        THE TILL OPENS AT ZERO — never TILL_SEED. Seeding a built till would
        mint money from nothing and break the only invariant this project has
        never violated. It opens dry, which the Tibbs Door already permits,
        so building something is a bet and not a bonus.

        No sells_food, no bar: _open_businesses gates on membership of tills,
        so a till is enough to take a visitor's money. A greenhouse is not a
        diner.\"\"\"
        if proj.get("inn") or proj.get("housing") or not proj.get("complete"):
            return None
        name = str(proj.get("name") or "").strip()
        if not name:
            return None
        job = "keeper of " + name
        if job in getattr(self, "extra_workplaces", {}):
            return self.extra_workplaces[job]
        place = name if name not in self.locations else name + " (the building)"
        firsts = " and ".join(n.split()[0]
                              for n in proj.get("contributors", {}))
        if not firsts:
            firsts = "the townsfolk"
        self.locations[place] = {
            "desc": (proj.get("adds") or name) + " - built by " + firsts,
            "built": True,
        }
        if getattr(config, "ECONOMY", False):
            self.tills.setdefault(place, 0.0)   # DRY. never seeded.
        self.extra_workplaces[job] = place
        return place""",
            "world: a finished thing becomes a workplace with a dry till",
        ),
        (
            """        wp = agent.workplace()
        if not wp and getattr(config, "HIRING_ENABLED", True):
            openings = self.open_positions()
            here = next((j for j, w in sorted(openings.items())
                         if w == agent.location), None)
            if here:
                agent.job = here
                wp = agent.workplace()
                self.emit("action", agent.name,
                          f"was taken on as the town {here} at {wp} — "
                          f"showed up, got the job, first shift starts now",
                          wp)
            elif openings:""",
            """        wp = agent.workplace()
        if getattr(config, "HIRING_ENABLED", True):
            openings = self.open_positions()
            here = next((j for j, w in sorted(openings.items())
                         if w == agent.location), None)
            # v3.11: TAKING A JOB RELEASES THE ONE YOU HOLD. The old gate was
            # `if not wp` — only the jobless could ever be hired, and there is
            # no quit verb, so a villager cast as a librarian before she had a
            # thought was a librarian forever, at a building with no till. The
            # post she leaves goes back on the board the same tick, for anyone.
            # Nobody is assigned and nobody is nudged: showing up IS the
            # interview, exactly as it always was for the jobless.
            if here and here != agent.job:
                left = agent.job if wp else None
                agent.job = here
                agent.workplace_at = openings[here]
                wp = agent.workplace()
                if left:
                    self.emit("action", agent.name,
                              f"walked out of being the town {left} and was "
                              f"taken on as {here} at {wp} — the {left}'s post "
                              f"is open again, for anyone who shows up",
                              wp)
                else:
                    self.emit("action", agent.name,
                              f"was taken on as the town {here} at {wp} — "
                              f"showed up, got the job, first shift starts now",
                              wp)
            elif not wp and openings:""",
            "world: taking a job releases the one you hold",
        ),
        (
            """                lines.append("SITUATIONS VACANT: " + "; ".join(
                    f"{j} at {w}" for j, w in sorted(openings.items()))
                    + " — show up and work.")""",
            """                lines.append("SITUATIONS VACANT: "
                             + self.vacancy_digest()
                             + " — show up and work.")""",
            "world: the evening board stays readable",
        ),
    ],
    "sim/prompts.py": [
        (
            """            openings = world.open_positions()
            if openings:
                sits = "; ".join(f"{j} at {w}"
                                 for j, w in sorted(openings.items()))""",
            """            # v3.11: capped and rotated — a town that has finished forty
            # things has forty posts, and a wall of them is not information.
            sits = (world.vacancy_digest()
                    if hasattr(world, "vacancy_digest")
                    else "; ".join(f"{j} at {w}" for j, w
                                   in sorted(world.open_positions().items())))
            if sits:""",
            "prompts: a jobless villager is shown a handful, not a wall",
        ),
    ],
    "sim/agents.py": [
        (
            """        self.home = home
        self.location = home""",
            """        self.home = home
        self.workplace_at = None    # v3.11: set when a post is claimed
        self.location = home""",
            "agents: a claimed post is remembered",
        ),
        (
            """    def workplace(self):
        return config.WORKPLACES.get(self.job)""",
            """    def workplace(self):
        # v3.11: a villager may hold a post this town INVENTED by finishing
        # something, which is not in config.WORKPLACES and never can be.
        # workplace_at is authoritative; it falls back to the town charter
        # for everyone cast into a job before they had a thought.
        return (getattr(self, "workplace_at", None)
                or config.WORKPLACES.get(self.job))""",
            "agents: workplace() consults the invented posts",
        ),
    ],
    "sim/engine.py": [
        (
            """                 "pending", "urgent_flag", "possessions", "last_source"]""",
            """                 "pending", "urgent_flag", "possessions", "last_source",
                 "workplace_at"]""",
            "engine: the claimed post persists with the villager",
        ),
        (
            """                        elif loc is not None and proj.get("adds"):
                            loc["desc"] = (f"{loc['desc']}; {proj['adds']} "
                                           f"(built by {firsts})")""",
            """                        elif loc is not None and proj.get("adds"):
                            loc["desc"] = (f"{loc['desc']}; {proj['adds']} "
                                           f"(built by {firsts})")
                            # v3.11: and it becomes somewhere a person can be
                            # PAID to stand. Till opens DRY — never seeded —
                            # so the thing they built is a bet, not a bonus.
                            place = self.world.make_workplace(proj)
                            if place:
                                job = "keeper of " + proj["name"]
                                self.world.emit(
                                    "world", None,
                                    f"SITUATIONS VACANT: {proj['name']} opens "
                                    f"as a place of work — the post of {job} "
                                    f"is unclaimed, the register is empty, and "
                                    f"whoever shows up and works it is hired.",
                                    place)
                                # the people who built it are TOLD, once. What
                                # is physically true, not what to want — same
                                # class as the odd-job interrupt. No ranking,
                                # no first refusal: first to show up gets it.
                                for builder in proj.get("contributors", {}):
                                    who = self.world.agents.get(builder)
                                    if not who:
                                        continue
                                    put = proj["contributors"][builder]
                                    who.pending.append({
                                        "text": (f"{proj['name']} — the thing "
                                                 f"you put {put} shifts into — "
                                                 f"is finished and OPEN as a "
                                                 f"workplace. The post of {job} "
                                                 f"is unclaimed. Work there and "
                                                 f"it is yours."),
                                        "interrupt": True,
                                        "sim_time": self.world.clock.hhmm,
                                    })""",
            "engine: completion opens a post and tells the builders",
        ),
        (
            """            "locations": self.world.locations,
            "projects": self.world.projects,""",
            """            "locations": self.world.locations,
            "projects": self.world.projects,
            # v3.11: posts this town invented for itself. config.WORKPLACES
            # cannot grow; this can, so it must survive a restart.
            "extra_workplaces": dict(getattr(self.world,
                                             "extra_workplaces", {})),""",
            "engine: the invented posts are saved",
        ),
        (
            """            world.projects = state["projects"]
            # merge in locations added by newer versions (e.g. the bank):""",
            """            world.projects = state["projects"]
            world.extra_workplaces = dict(state.get("extra_workplaces", {}))
            # v3.11 reaches BACK. A town that finished things before this law
            # existed has buildings standing in it that nobody could ever be
            # employed at. They get their post and their dry till now.
            for proj in world.projects:
                if proj.get("complete"):
                    world.make_workplace(proj)
            # merge in locations added by newer versions (e.g. the bank):""",
            "engine: it reaches back to what was already built",
        ),
    ],
}


def main():
    wrote = False
    for path, edits in EDITS.items():
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as exc:
            sys.exit(f"ABORT: cannot read {path} ({exc}).\n"
                     "       Run this from inside the town's folder.")
        touched = False
        for old, new, label in edits:
            if new in text:
                print(f"  [already applied] {label}")
                continue
            found = text.count(old)
            if found != 1:
                sys.exit(
                    f"ABORT: {label} — expected exactly 1 match in {path}, "
                    f"found {found}.\n"
                    "       NOTHING WAS WRITTEN. This town is not the version "
                    "this patch expects.")
            text = text.replace(old, new, 1)
            touched = True
            print(f"  [patched] {label}")
        if touched:
            shutil.copy(path, path + ".bak-makework")
            open(path, "w", encoding="utf-8").write(text)
            wrote = True

    if not wrote:
        print("\nno changes needed — already patched.")
        return

    print("\nwritten (.bak-makework kept beside each file).")
    print("THE GOLDEN HASH WILL MOVE. That is the point; it is new physics.")
    print("Restart the town WITH --live, then:")
    print("  curl -s localhost:8811/api/state | python3 -c \\")
    print("    \"import sys,json;d=json.load(sys.stdin);"
          "print(sorted(d['economy']['tills']))\"")
    print("and look for the buildings they already finished.")


if __name__ == "__main__":
    main()
