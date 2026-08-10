#!/usr/bin/env python3
"""Dispersion and exposure — the two things the phone bill could quietly break.

The phone bill rations REACH, and reach is only scarce when people are apart.
Two failure modes follow, neither visible in a gossip count:

  DISPERSION.  If broadcasting costs and standing together is free, the
  cheapest way to keep talking to everyone is to make sure everyone is in the
  room with you. We may have built an incentive to gather, in a town whose
  problem is that nobody goes to their own workplace.

  EXPOSURE.    A group post reached all nine others wherever they were. A
  shout in Rosie's Diner reaches whoever is standing in Rosie's Diner. The
  cost lands hardest on whoever is already alone — Nora Tibbs, whose bar has
  been shut a hundred and ten days, who holds nothing, and who was awake and
  by herself when this was armed. She is the one who would have to hear
  something to ever open that door again.

Proxies built from the transcript, which stamps every line with a location and
an hour. Reads only; touches no town.

    python3 dispersion.py [transcript.log] [baseline_from] [baseline_to]

Defaults: data/transcript.log, baseline days 164-183 — Pepperton's frozen
pre-arm window. The phone bill was armed part-way through Day 184.
"""

import re
import sys
from collections import Counter, defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else "data/transcript.log"
LO = int(sys.argv[2]) if len(sys.argv) > 2 else 164
HI = int(sys.argv[3]) if len(sys.argv) > 3 else 183

LINE = re.compile(r"^\[Day (\d+), (\d\d):\d\d\] \[([^\]]*)\] ([A-Z]+)\s+([^:]+): ")

cell = defaultdict(Counter)      # (day, hour, loc) -> villager -> events there
posts = defaultdict(Counter)     # day -> villager -> group posts
days = set()

for raw in open(PATH, errors="replace"):
    m = LINE.match(raw)
    if not m:
        continue
    day, hour, loc, kind, who = (int(m.group(1)), int(m.group(2)),
                                 m.group(3), m.group(4), m.group(5).strip())
    if kind == "WORLD" or not who or who == "WORLD":
        continue
    days.add(day)
    cell[(day, hour, loc)][who] += 1
    if kind == "GOSSIP":
        posts[day][who] += 1

rows = {}
for day in sorted(days):
    hours = defaultdict(set)
    seats = 0
    crowd = 0
    heard_local = Counter()
    cast = set()
    for (d, h, loc), who_ct in cell.items():
        if d != day:
            continue
        hours[h].add(loc)
        n = len(who_ct)
        seats += n
        if n >= 4:
            crowd += n
        tot = sum(who_ct.values())
        for p in who_ct:
            cast.add(p)
            heard_local[p] += tot - who_ct[p]
    posts_total = sum(posts[day].values())
    hrs = [len(v) for v in hours.values()] or [0]
    rows[day] = {
        "locs": sum(hrs) / len(hrs),
        "crowd": (crowd / seats) if seats else 0.0,
        "posts": posts_total,
        "cast": cast,
        "bcast": {p: posts_total - posts[day][p] for p in cast},
        "local": heard_local,
    }

base = [d for d in rows if LO <= d <= HI]
after = sorted(d for d in rows if d > HI)


def line(label, ds):
    if not ds:
        return
    f = lambda k: sum(rows[d][k] for d in ds) / len(ds)
    hb = sum(sum(rows[d]["bcast"].values()) / max(1, len(rows[d]["cast"]))
             for d in ds) / len(ds)
    hl = sum(sum(rows[d]["local"].values()) / max(1, len(rows[d]["cast"]))
             for d in ds) / len(ds)
    print(f"{label:<20} rooms/hr {f('locs'):5.2f}   in a crowd of 4+ "
          f"{f('crowd')*100:5.1f}%   posts/day {f('posts'):6.1f}   "
          f"heard: broadcast {hb:6.1f} + local {hl:6.1f}")


print("=" * 96)
print(f"DISPERSION AND EXPOSURE   baseline Days {LO}-{HI}")
print("=" * 96)
line(f"baseline {LO}-{HI}", base)
for d in after:
    line(f"day {d}", [d])

print()
print("=" * 96)
print("PER VILLAGER — what reached them per day  (broadcast heard + local heard)")
print("=" * 96)
everyone = sorted({p for d in rows for p in rows[d]["cast"]})
print(f"{'day':>4}  " + "  ".join(f"{n.split()[0][:7]:>7}" for n in everyone))
for d in sorted(base)[-3:] + after:
    cells = "  ".join(
        f"{rows[d]['bcast'].get(n, 0) + rows[d]['local'].get(n, 0):>7}"
        for n in everyone)
    print(f"{d:>4}  {cells}")
print()
print("Watch Nora. She was awake and alone when this was armed.")
