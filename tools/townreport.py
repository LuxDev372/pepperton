"""Town Report — one self-contained HTML file telling a town's whole story.

Every Pepperton in the world is a private terrarium. The observatory has no
authentication, so it never leaves the LAN, which means the only way anybody
has ever shared a town is a screenshot and a typed-out quote.

This reads a town's own data directory and writes a single HTML file: the
cast and which model plays whom, the vitals, the work history, the economy,
every law that fired and the villager who forced it, and the lines worth
keeping. No server, no network, no dependencies. Hand it to someone and they
have your town.

    python tools/townreport.py                     # ./data -> town_report.html
    python tools/townreport.py --data ../pepperton2/data --out pompeii.html

Read-only. It opens nothing for writing inside data/ and cannot perturb a
running simulation.
"""

import argparse
import html
import json
import os
import re
from collections import Counter, defaultdict

# --- palette: dataviz reference instance, validated both modes ------------
# node scripts/validate_palette.js "#3987e5,#d95926,#199e70" --mode dark  -> ALL PASS
# node scripts/validate_palette.js "#2a78d6,#eb6834,#1baf7a" --mode light -> ALL PASS
LIGHT = {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e",
         "ink3": "#84837d", "line": "#e6e5e1",
         "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a",
         "good": "#0ca30c", "warn": "#fab219", "crit": "#d03b3b"}
DARK = {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7",
        "ink3": "#8a8981", "line": "#333330",
        "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70",
        "good": "#0ca30c", "warn": "#fab219", "crit": "#d03b3b"}


# ------------------------------------------------------------------ input
def load(data_dir):
    state, events = {}, []
    sp = os.path.join(data_dir, "world_state.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
    tp = os.path.join(data_dir, "transcript.jsonl")
    if os.path.exists(tp):
        wid = state.get("world_id")
        with open(tp, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if wid and ev.get("wid") not in (wid, None, "legacy"):
                    continue    # another town's lines in a shared directory
                events.append(ev)
    return state, events


def load_runs(data_dir, world_id):
    """The experiment ledger for THIS town, newest run last.

    Without this section the report is a very handsome way to publish a
    story about a script. Every number above it — shifts, wallets, laws —
    is only worth reading if the minds were actually running while it
    happened, and the only place that is recorded is here."""
    path = os.path.join(data_dir, "experiments.json")
    try:
        with open(path, encoding="utf-8") as f:
            runs = json.load(f).get("runs", [])
    except (OSError, ValueError):
        return []
    if world_id:
        runs = [r for r in runs if r.get("world_id") in (world_id, None)]
    return runs


# --------------------------------------------------------------- the story
# Each law leaves a fingerprint in the transcript. This is how a town's own
# legal history is recovered from what actually happened to it.
#
# A law fires when the WORLD records it, never when a villager MENTIONS it.
# v3.0.1 counted both, and the jar row read 424 in a town where the jar had
# been used a handful of times — every one of those hits was somebody saying
# "the poor box" out loud, which this town does constantly, because talking
# about charity is the cheapest virtue available to it. The report was
# measuring the conversation and calling it the law. Statutes leave marks in
# 'world' and 'action' events; speech is 'say', 'gossip' and 'text'.
LAW_EVENT_TYPES = ("world", "action")

LAWS = [
    ("FORECLOSURE at",        "crit", "the Holt Act — the bank took a house"),
    ("has PURCHASED",         "warn", "the debt market — the bank bought the town's arrears"),
    ("WAS FORCED IN THE NIGHT", "crit", "the lockbox was robbed"),
    ("THE CRANE BONUS",       "good", "a long run of work was paid for"),
    ("on an empty till",      "good", "the Tibbs Door — a business opened with a dry register"),
    ("taken on as the town",  "good", "Situations Vacant — somebody claimed a vacant job"),
    ("bank's sale",           "warn", "a deed changed hands"),
    ("door back",             "good", "a house was redeemed before it sold"),
    ("PERMIT EXPIRED",        "warn", "the Fall Fair Act — a permit ran out"),
    ("CONDEMNED",             "crit", "a project was torn down"),
    ("poor box",              "good", "the jar"),
    ("coach pulls into",      "s1",   "the bus route"),
    ("found every door shut", "crit", "the visitors left with their money"),
    ("in this town today",    "good", "the visitors spent money here"),
    ("odd job",               "good", "a townsperson paid for work"),
    ("stranger steps off",    "s1",   "somebody arrived"),
]


def build(state, events, runs=()):
    out = {}
    out["runs"] = list(runs)
    agents = state.get("agents", {})
    out["day"] = state.get("day", 0)
    out["tick"] = state.get("tick_no", 0)
    out["seed"] = state.get("seed")
    out["tills"] = state.get("tills", {})

    # cast
    cast = []
    for name, a in agents.items():
        cast.append({
            "name": name, "job": a.get("job", ""),
            "model": a.get("model", "?"), "host": a.get("host", ""),
            "money": round(float(a.get("money", 0)), 2),
            "home": a.get("home"),
            "traits": ", ".join(a.get("traits", [])[:3]),
            "goal": a.get("goal", ""),
        })
    out["cast"] = sorted(cast, key=lambda c: -c["money"])

    # work history per day, and who did it
    shifts = defaultdict(int)
    by_worker = Counter()
    for ev in events:
        if ev.get("type") != "action":
            continue        # a shift is a thing done, not a thing said
        t = str(ev.get("text", ""))
        if "started a shift" in t or "on an empty till" in t:
            shifts[ev.get("day", 0)] += 1
            if ev.get("agent"):
                by_worker[ev["agent"]] += 1
    out["shifts_by_day"] = dict(sorted(shifts.items()))
    out["shifts_by_worker"] = by_worker.most_common()

    # words vs deeds — the number this project exists to look at
    said = sum(1 for e in events if e.get("type") in ("say", "gossip", "text"))
    did = sum(1 for e in events if e.get("type") == "action")
    out["said"], out["did"] = said, did

    # the legal history — deeds only, never talk about deeds
    deeds = [e for e in events if e.get("type") in LAW_EVENT_TYPES]
    hits = []
    for needle, tone, label in LAWS:
        matched = [e for e in deeds if needle in str(e.get("text", ""))]
        if matched:
            hits.append({"label": label, "tone": tone, "n": len(matched),
                         "first": matched[0], "last": matched[-1]})
    out["laws"] = hits

    # headline moments, in order, deduplicated
    big = []
    seen = set()
    for ev in deeds:
        t = str(ev.get("text", ""))
        for needle, tone, _ in LAWS:
            if needle in t and tone in ("crit", "warn", "good"):
                key = t[:60]
                if key in seen:
                    break
                seen.add(key)
                # action events are verb phrases ("was taken on as the town
                # bartender"), so they need their actor in front exactly as
                # the live feed renders them
                shown = (f"{ev['agent']} {t}"
                         if ev.get("type") == "action" and ev.get("agent")
                         else t)
                big.append({"day": ev.get("day"), "time": ev.get("sim_time"),
                            "tone": tone, "agent": ev.get("agent"),
                            "text": shown})
                break
    out["moments"] = big

    # lines worth keeping: spoken, substantial, not a repeat
    quotes, qseen = [], set()
    for ev in events:
        if ev.get("type") not in ("say", "gossip"):
            continue
        t = str(ev.get("text", "")).strip()
        if not (40 <= len(t) <= 240):
            continue
        norm = re.sub(r"[^a-z ]", "", t.lower())[:60]
        if norm in qseen:
            continue
        qseen.add(norm)
        quotes.append({"day": ev.get("day"), "time": ev.get("sim_time"),
                       "who": ev.get("agent"), "text": t})
    step = max(1, len(quotes) // 24)
    out["quotes"] = quotes[::step][:24]
    out["events_total"] = len(events)

    # how much of this town was actually thought
    decisions = understudy = live = unparsed = interventions = 0
    mock_runs = 0
    for run in out["runs"]:
        d = run.get("decisions") or {}
        decisions += sum(d.values())
        live += d.get("model", 0) + d.get("possessed", 0)
        understudy += d.get("understudy", 0) + d.get("dark", 0)
        unparsed += d.get("unparsed", 0)
        interventions += len(run.get("interventions") or [])
        if run.get("mock_mode"):
            mock_runs += 1
    out["integrity"] = {
        "runs": len(out["runs"]), "decisions": decisions, "live": live,
        "understudy": understudy, "unparsed": unparsed,
        "interventions": interventions, "mock_runs": mock_runs,
        "live_pct": round(100.0 * live / decisions, 1) if decisions else None,
    }
    return out


# ----------------------------------------------------------------- render
def bars(series, colour, w=680, h=120):
    """Change-over-time, small and honest: one series, no legend needed —
    the caption names it. Thin marks, 4px rounded data-ends on the baseline,
    2px surface gap between bars, recessive axis, selective labels only."""
    if not series:
        return '<p class="muted">no work recorded</p>'
    days = list(series)
    vals = [series[d] for d in days]
    top = max(vals) or 1
    n = len(days)
    gap = 2
    bw = max(2.0, (w - (n - 1) * gap) / n) if n else w
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" '
             f'role="img" aria-label="shifts worked per day">']
    parts.append(f'<line x1="0" y1="{h-18}" x2="{w}" y2="{h-18}" '
                 f'stroke="var(--line)" stroke-width="1"/>')
    for i, (d, v) in enumerate(zip(days, vals)):
        x = i * (bw + gap)
        bh = (v / top) * (h - 30)
        y = (h - 18) - bh
        if v > 0:
            parts.append(
                f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{bh:.1f}" rx="{min(4, bw/2):.1f}" '
                f'fill="{colour}"><title>Day {d}: {v} shift'
                f'{"s" if v != 1 else ""}</title></rect>')
        else:
            parts.append(
                f'<rect x="{x:.1f}" y="{h-20:.1f}" width="{bw:.1f}" '
                f'height="2" rx="1" fill="var(--line)">'
                f'<title>Day {d}: nobody worked</title></rect>')
    # selective direct labels: first, last, and the peak — never every point
    # selective direct labels: first, last, and the peak — but drop the peak
    # label when it would sit on top of an end label
    peak = vals.index(top)
    wanted = [0, n - 1]
    if n > 4 and min(abs(peak - 0), abs(peak - (n - 1))) > 1:
        wanted.append(peak)
    for i in sorted(set(wanted)):
        if 0 <= i < n:
            x = i * (bw + gap) + bw / 2
            parts.append(f'<text x="{x:.1f}" y="{h-5}" text-anchor="middle" '
                         f'font-size="10" fill="var(--ink3)">'
                         f'{days[i]}</text>')
    parts.append(f'<text x="0" y="12" font-size="10" fill="var(--ink3)">'
                 f'peak {top}/day</text></svg>')
    return "".join(parts)


def esc(s):
    return html.escape(str(s if s is not None else ""))


def render(town, r, version):
    def css(m):
        return "".join(f"--{k}:{v};" for k, v in m.items())
    homeless = sum(1 for c in r["cast"] if not c["home"])
    worked = r["shifts_by_worker"]
    total_shifts = sum(n for _, n in worked)
    ratio = (r["said"] / r["did"]) if r["did"] else 0

    moments = "".join(
        f'<li class="m {m["tone"]}"><span class="when">Day {esc(m["day"])} '
        f'{esc(m["time"])}</span><span class="what">{esc(m["text"])}</span></li>'
        for m in r["moments"][-40:]) or '<li class="muted">nothing yet</li>'

    quotes = "".join(
        f'<figure><blockquote>{esc(q["text"])}</blockquote>'
        f'<figcaption>— {esc(q["who"])}, Day {esc(q["day"])} '
        f'{esc(q["time"])}</figcaption></figure>' for q in r["quotes"])

    castrows = "".join(
        f'<tr><td><b>{esc(c["name"])}</b><div class="sub">{esc(c["traits"])}</div></td>'
        f'<td>{esc(c["job"])}</td><td class="mono">{esc(c["model"])}</td>'
        f'<td class="num">${c["money"]:.2f}</td>'
        f'<td>{"—" if not c["home"] else esc(c["home"])}</td>'
        f'<td class="num">{dict(worked).get(c["name"], 0)}</td></tr>'
        for c in r["cast"])

    lawrows = "".join(
        f'<tr><td><span class="dot {l["tone"]}"></span>{esc(l["label"])}</td>'
        f'<td class="num">{l["n"]}</td>'
        f'<td class="sub">first Day {esc(l["first"].get("day"))} · '
        f'last Day {esc(l["last"].get("day"))}</td></tr>'
        for l in r["laws"]) or '<tr><td colspan="3" class="muted">no laws have fired</td></tr>'

    tillrows = "".join(
        f'<tr><td>{esc(k)}</td><td class="num">${v:,.2f}</td></tr>'
        for k, v in sorted(r["tills"].items(), key=lambda kv: -kv[1]))

    # ---- provenance. The section that decides whether the rest is real.
    ig = r.get("integrity") or {}
    pct = ig.get("live_pct")
    if not ig.get("runs"):
        prov_tone, prov_badge, prov_verdict = "warn", "no ledger", (
            "No experiment ledger was found for this town. Nothing here can "
            "be verified as having come from a language model — it may all "
            "be true, but this file cannot tell you so.")
    elif ig.get("mock_runs") == ig.get("runs") and not ig.get("live"):
        prov_tone, prov_badge, prov_verdict = "warn", "mock run", (
            "This town ran in mock mode. Every decision below was made by "
            "the built-in script, not by a model. It is a rehearsal.")
    elif pct is not None and pct >= 99.0 and not ig.get("understudy"):
        prov_tone, prov_badge, prov_verdict = "good", "100% live minds", (
            "Every decision in this town was made by a language model. No "
            "understudy ever took a seat.")
    elif pct is not None and pct >= 90.0:
        prov_tone, prov_badge, prov_verdict = "warn", f"{pct}% live minds", (
            f"{100 - pct:.1f}% of decisions were made by the understudy — the "
            f"built-in script that stands in when a model is unreachable. "
            f"Those stretches are not findings.")
    else:
        prov_tone, prov_badge, prov_verdict = "crit", f"{pct}% live minds", (
            f"Only {pct:.1f}% of decisions came from a model. Most of what "
            f"happened here was a script. Read it as fiction.")

    # The banner line at the top of this file is a CLAIM. It is only allowed
    # to make the strong one when the ledger backs it.
    if prov_tone == "good":
        lede_claim = ("Every villager below is a different language model. "
                      "Nobody is playing them.")
    elif prov_badge == "mock run":
        lede_claim = ("Every villager below is a placeholder script — this "
                      "town ran with its minds switched off.")
    else:
        lede_claim = ("Every villager below is meant to be a different "
                      "language model; see the provenance section for how "
                      "much of this run actually was.")

    runrows = "".join(
        f'<tr><td class="mono">{esc(run.get("run_id"))}</td>'
        f'<td>{esc(run.get("code_version"))}</td>'
        f'<td class="num">{esc(run.get("seed"))}</td>'
        f'<td>Day {esc(run.get("opened_day"))}&ndash;'
        f'{esc(run.get("closed_day", run.get("opened_day")))}</td>'
        f'<td class="num">'
        f'{(run.get("integrity") or {}).get("live_pct", "—")}</td>'
        f'<td class="num">{len(run.get("interventions") or [])}</td>'
        f'<td class="sub">{esc(run.get("ended_reason", "running"))}</td></tr>'
        for run in r["runs"][-12:]) or \
        '<tr><td colspan="7" class="muted">no runs recorded</td></tr>'

    hands = []
    for run in r["runs"]:
        for i in (run.get("interventions") or []):
            hands.append(i)
    handrows = "".join(
        f'<li class="m warn"><span class="when">Day {esc(i.get("day"))} '
        f'tick {esc(i.get("tick"))}</span>'
        f'<span class="what">{esc(i.get("kind"))}: {esc(i.get("detail"))}'
        f'</span></li>' for i in hands[-25:])

    # the work-history table is the relief for the chart (contrast rule)
    workrows = "".join(
        f'<tr><td>Day {esc(d)}</td><td class="num">{n}</td></tr>'
        for d, n in r["shifts_by_day"].items())

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(town)} — town report, Day {esc(r["day"])}</title>
<style>
.viz-root {{ color-scheme: light; {css(LIGHT)} }}
@media (prefers-color-scheme: dark) {{
  :root:where(:not([data-theme="light"])) .viz-root {{ color-scheme: dark; {css(DARK)} }}
}}
:root[data-theme="dark"] .viz-root {{ color-scheme: dark; {css(DARK)} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--surface); color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
.wrap {{ max-width: 860px; margin: 0 auto; padding: 32px 20px 80px; }}
h1 {{ font-size: 30px; margin: 0 0 4px; letter-spacing:-.01em; }}
h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing:.09em;
  color: var(--ink3); margin: 44px 0 12px; font-weight:600; }}
.lede {{ color: var(--ink2); margin: 0 0 28px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr));
  gap:1px; background:var(--line); border:1px solid var(--line); border-radius:8px;
  overflow:hidden; }}
.tile {{ background:var(--surface); padding:12px 14px; }}
.tile b {{ display:block; font-size:22px; letter-spacing:-.01em; }}
.tile span {{ font-size:10px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--ink3); }}
table {{ width:100%; border-collapse:collapse; font-size:14px; }}
th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line);
  vertical-align:top; }}
th {{ font-size:10px; text-transform:uppercase; letter-spacing:.07em;
  color:var(--ink3); font-weight:600; }}
td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.mono {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px;
  color:var(--ink2); }}
.sub {{ font-size:12px; color:var(--ink3); }}
.muted {{ color:var(--ink3); }}
ul.moments {{ list-style:none; padding:0; margin:0; }}
li.m {{ display:flex; gap:12px; padding:8px 0 8px 12px; border-left:3px solid var(--line);
  border-bottom:1px solid var(--line); }}
li.m .when {{ flex:0 0 108px; font-size:12px; color:var(--ink3);
  font-variant-numeric:tabular-nums; }}
li.m.crit {{ border-left-color:var(--crit); }}
li.m.warn {{ border-left-color:var(--warn); }}
li.m.good {{ border-left-color:var(--good); }}
.verdict {{ padding:14px 16px; border-left:4px solid var(--line);
  background:var(--surface); margin:0 0 14px; font-size:14px; }}
.verdict b {{ display:block; font-size:11px; text-transform:uppercase;
  letter-spacing:.08em; margin-bottom:5px; }}
.verdict.good {{ border-left-color:var(--good); }}
.verdict.good b {{ color:var(--good); }}
.verdict.warn {{ border-left-color:var(--warn); }}
.verdict.warn b {{ color:var(--warn); }}
.verdict.crit {{ border-left-color:var(--crit); }}
.verdict.crit b {{ color:var(--crit); }}
.dot {{ display:inline-block; width:9px; height:9px; border-radius:50%;
  margin-right:8px; vertical-align:middle; }}
.dot.crit {{ background:var(--crit); }} .dot.warn {{ background:var(--warn); }}
.dot.good {{ background:var(--good); }} .dot.s1 {{ background:var(--s1); }}
figure {{ margin:0 0 18px; padding-left:14px; border-left:3px solid var(--s1); }}
blockquote {{ margin:0; font-size:15px; }}
figcaption {{ font-size:12px; color:var(--ink3); margin-top:4px; }}
details {{ margin-top:10px; }} summary {{ cursor:pointer; font-size:12px;
  color:var(--ink3); }}
footer {{ margin-top:56px; padding-top:16px; border-top:1px solid var(--line);
  font-size:12px; color:var(--ink3); }}
rect {{ transition: opacity .12s; }} rect:hover {{ opacity:.75; }}
</style></head>
<body class="viz-root"><div class="wrap">

<h1>{esc(town)}</h1>
<p class="lede">Day {esc(r["day"])} · tick {esc(r["tick"])} · seed
{esc(r["seed"])} · {esc(r["events_total"])} recorded events. {lede_claim}</p>

<div class="tiles">
  <div class="tile"><b>{len(r["cast"])}</b><span>villagers</span></div>
  <div class="tile"><b>{total_shifts}</b><span>shifts ever worked</span></div>
  <div class="tile"><b>{homeless}</b><span>homeless</span></div>
  <div class="tile"><b>${sum(c["money"] for c in r["cast"]):,.0f}</b><span>all wallets</span></div>
  <div class="tile"><b>${r["tills"].get("the town fund", 0):,.0f}</b><span>town fund</span></div>
  <div class="tile"><b>{ratio:.1f}&times;</b><span>words per deed</span></div>
</div>

<h2>Was any of this real?</h2>
<p class="sub">A town can run without its minds. When a model host is
unreachable the built-in understudy takes the seat and the simulation carries
on looking exactly the same — which is how ten days of this project's own
history were once mistaken for something they were not. The ledger below is
kept by the engine itself, so this question has an answer that is not a
memory.</p>
<div class="verdict {prov_tone}"><b>{esc(prov_badge)}</b>{esc(prov_verdict)}</div>
<p class="sub">{ig.get("decisions", 0):,} decisions across
{ig.get("runs", 0)} run(s) · {ig.get("live", 0):,} from a model ·
{ig.get("understudy", 0):,} from the understudy ·
{ig.get("unparsed", 0):,} unparsed · {ig.get("interventions", 0):,} human
interventions.</p>
<table><thead><tr><th>Run</th><th>Version</th><th>Seed</th><th>Days</th>
<th>% live</th><th>Hands</th><th>Ended</th></tr></thead>
<tbody>{runrows}</tbody></table>
{f'<details><summary>Every time a human reached in ({len(hands)})</summary>'
 f'<ul class="moments">{handrows}</ul></details>' if hands else
 '<p class="sub">Nobody reached in. No possessions, no injected events, no '
 'hand on the scale.</p>'}

<h2>Who lives here</h2>
<table><thead><tr><th>Villager</th><th>Job</th><th>Mind</th><th>Money</th>
<th>Home</th><th>Shifts</th></tr></thead><tbody>{castrows}</tbody></table>

<h2>Who actually worked</h2>
<p class="sub">Shifts started per day, across the town's whole life.</p>
{bars(r["shifts_by_day"], "var(--s1)")}
<details><summary>Show the numbers</summary>
<table><thead><tr><th>Day</th><th>Shifts</th></tr></thead>
<tbody>{workrows}</tbody></table></details>

<h2>The laws that fired</h2>
<p class="sub">Each statute in this town was passed in response to something a
villager actually did. This is which ones have bitten.</p>
<table><thead><tr><th>Law</th><th>Times</th><th>When</th></tr></thead>
<tbody>{lawrows}</tbody></table>

<h2>What happened</h2>
<ul class="moments">{moments}</ul>

<h2>Where the money is</h2>
<table><thead><tr><th>Held by</th><th>Amount</th></tr></thead>
<tbody>{tillrows}</tbody></table>

<h2>Lines worth keeping</h2>
{quotes or '<p class="muted">nobody has said anything yet</p>'}

<footer>Generated by Pepperton {esc(version)} · a town where every villager is
a different LLM. This file is self-contained — no network, no tracking, no
scripts. Share it.</footer>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Write a town's story to one HTML file.")
    ap.add_argument("--data", default="data", help="the town's data directory")
    ap.add_argument("--out", default="town_report.html")
    ap.add_argument("--town", default=None, help="override the town name")
    args = ap.parse_args()

    state, events = load(args.data)
    if not state and not events:
        raise SystemExit(f"nothing to read in {args.data!r} — point --data at a "
                         f"town's data directory")
    town = args.town
    if not town:
        try:
            import config
            town = config.TOWN_NAME
        except Exception:
            town = "Pepperton"
    version = "dev"
    for p in ("VERSION", os.path.join(os.path.dirname(__file__), "..", "VERSION")):
        try:
            version = open(p).read().strip()
            break
        except OSError:
            pass

    r = build(state, events, load_runs(args.data, state.get("world_id")))
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(town, r, version))
    print(f"{args.out}: {town}, Day {r['day']} — {len(r['cast'])} villagers, "
          f"{r['events_total']} events, {len(r['laws'])} laws fired")


if __name__ == "__main__":
    main()
