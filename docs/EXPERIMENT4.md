# EXPERIMENT 4 — THE TWO ADJECTIVES

**PRE-REGISTERED. Rewritten 9 August 2026 after adversarial review, before
any run. This file is committed to the repository so its hash provably
predates every run log it will be compared against.**

Nothing below may be edited after the first tick of arm A. If it needs
changing, the run is abandoned and re-registered.

---

## THE CLAIM UNDER TEST

For 169 days, `sim/prompts.py` has told every villager, in the verb list
rendered into every prompt:

    {"action": "build", ...}   (real work, at the project's site
                                — the whole town sees who actually builds)

    {"action": "work"}         (only at your workplace)

**We labelled one of their two jobs the real one, then spent six months
grading them on choosing it.**

And `work` is described **falsely**. A villager with no job may claim any
Situations Vacant post by standing in it and using `work` — `_verb_work`
hires on the spot: *"was taken on as the town {job} at {workplace} — showed
up, got the job, first shift starts now."* The verb list says the action is
*only at your workplace*. A newcomer has none.

Day 169: Hazel Pike, back in Pepperton as a stranger with $3.90, walked to
the workshop at 08:00 — the hour the bell advertises *handyman at the
workshop* — and did not take it. **Situations Vacant has never been claimed
in the recorded history of either town.**

## WHY THIS IS A CORRECTION, NOT AN INTERVENTION

`real work` and `who actually builds` are not physics; they are adjectives,
and an adjective in the perception layer is an outcome wearing a
description's coat. `only at your workplace` is simply untrue.

Discriminator applied and passed: *would you still make this change if the
town were thriving?* Yes — false statements are false at any level of
prosperity. (Funding the bank fails the same test and is not in this
experiment.)

---

## THE ARMS — THREE, NOT TWO

Review found the flaw and it is not resolved by acknowledging it: two edits
in one arm against one aggregate metric cannot be attributed afterwards. If
shifts rise, we could not tell whether the `work` correction let villagers
discover Situations Vacant, or whether de-editorialising `build` merely made
it relatively less attractive and pushed effort toward whatever was next.

    ARM A — CONTROL          prompts.py exactly as shipped

    ARM B — WORK FIX ONLY    only the `work` gloss corrected:
                             (a shift at your workplace — or at any job
                              listed in SITUATIONS VACANT: go there and use
                              this action, showing up is the interview)

    ARM C — BOTH             arm B, plus `build` de-editorialised:
                             (work on a project from the notice board,
                              at its site)

**Arm B is the hypothesis with evidence behind it** (Hazel at the workshop).
Arm C tells us whether removing the praise adds anything on top.

Three seeds per arm. **Nine runs.** At roughly 2,900 decisions and about an
hour per arm on LostBits, that is an overnight job, not a week.

## THE HARNESS

**No live town is touched.** Both arms run from copies of the same
checkpoint, so they inherit the same 169 days of projects, debts, tills and
homes.

    cp -r ~/Downloads/pepperton  /tmp/exp4-A   (and -B, -C)
    apply the edits to /tmp/exp4-B and /tmp/exp4-C only
    run.py --headless 1920 --live        # 20 sim-days, real minds

### ⚠️ SAMPLING MUST BE PINNED — found by review, and it was not

`OLLAMA_OPTIONS = {"temperature": 0.8, "num_ctx": 4096}`.

**Temperature 0.8, no seed.** Our `WORLD_SEED` governs the shuffle, the mock
brains and the townsfolk — it governs **nothing about how the models
sample**. "Three seeds" was measuring a far wider noise band than the design
implied.

**Required before any run:** add an explicit `seed` to `OLLAMA_OPTIONS` in
the scratch towns, and use the *same* sampling seed across A/B/C within a
triplet. Vary it only between triplets. Otherwise the arms differ by prompt
**and** by dice, and we cannot separate them.

### ⚠️ HOST CONTENTION IS A BLOCKING CONFOUND

Anything that differentially pushes one arm past the Ollama timeout —
contention, a cold cache inherited from the previous run, thermal drift over
a long session — differentially seats the understudy in that arm. We built
the instrument that catches this. Use it.

* **Run the arms interleaved or on separate hosts. Never sequentially on
  shared hardware** — whichever runs second inherits whatever drifted during
  the first.
* **`live_pct` parity between arms is a HARD PRECONDITION, not a footnote.**
  Any triplet where the arms differ by more than **2 percentage points** of
  `live_pct` is **discarded and re-run**, before its shift counts are looked
  at.
* Any arm containing an understudy, an unparsed reply or an `echoed`
  decision is discarded and re-run, not adjusted.

## THE ELIGIBLE POPULATION — measured, not assumed

Review asked whether the effect has room to appear at all. Pepperton, Day
169:

    VACANCIES (open_positions):  shopkeeper · librarian · handyman
    ELIGIBLE (no workplace):     Sam Fletcher (retired)
                                 Frank Sorrel (newcomer)
                                 Hazel Pike   (newcomer)

Three posts, three villagers who could take one. The ceiling is not the
binding constraint.

## THE BAR — CORRECTED, AND THE OLD ONE WAS BROKEN

The previous draft said *"arm B produces at least twice the shifts of arm
A."* **Days 166, 167 and 168 recorded zero shifts in the entire town.** Twice
zero is zero. A control that does nothing would have satisfied it trivially.
Ratios are unusable against a floor of zero.

Absolute differences only:

> **MOVED:** an arm produces **≥ 10 `started a shift` events**, AND **≥ 8
> more than arm A**, in **at least 2 of the 3 seed triplets**, with
> `live_pct` parity satisfied in every triplet counted.
>
> **SEPARATELY REPORTED, WEAKER:** any Situations Vacant claim — the
> `was taken on as the town …` event. Baseline is **zero in 168 days across
> two towns**, so a single claim is interesting. One event is not a finding
> and will be reported as suggestive only.
>
> **NOT MOVED:** anything else. Including a result that appears in one
> triplet and not the others.

**If it does not move, the adjectives are exonerated and Window 2's
interpretation stands.** That is the outcome that costs us most, which is why
it is written here first.

## SCOPE — WHAT THIS RESULT MAY AND MAY NOT BE CALLED

Review's point, and it is right: 169 days under the incorrect prompt means
this cast's habits were shaped by the bug's own downstream effects. A
villager who has spent months not considering a vacant post may be slower to
change than one who never formed the habit.

> **This design can support:** *"does this correction unstick this specific
> stuck pattern in this cast?"*
>
> **It cannot support:** *"does this correction improve employment dynamics
> in Pepperton."* That needs fresh-seed towns and a different experiment.

Reported effects are therefore **lower bounds** on a new town, not estimates.

## WHAT THIS DOES NOT TEST

* **Memory.** The knobs stay frozen; all arms carry the same ~6–8 hour
  reachable past. DAY131's ruling holds.
* **The price list.** Wages unchanged in all arms. If an arm moves, the
  economics were never sufficient alone.
* **The road.** Travel is not armed in the scratch towns. Nobody leaves.
* **Pompeii, Claudeville, or the live Pepperton.**

## WHAT HAPPENS AFTER

**If arm B moves:** the `work` gloss was misinforming them, `WHY-THE-DOORS-
STAY-SHUT` Part Two is upgraded from *may be* to *was*, and Window 2's
*substitute, not gateway* is formally withdrawn as a claim about LLM agents
and reissued as a claim about prompt wording.

**If only arm C moves:** the effect needed both edits and neither is
sufficient alone. Report as a bundle; do not attribute.

**If nothing moves:** the diff still ships, because the statements are still
false. Part Two is marked *tested and not supported*, and the next suspects
are the price list and the channel, in that order.

**Either way the diff ships.** We do not get to keep a false sentence in the
prompt because it produced an interesting result.

---

*Nothing in this experiment steers a villager. It removes two sentences we
wrote and corrects one that was untrue. If the town behaves differently
afterwards, that difference was ours all along.*

*Design corrected after adversarial review: three arms instead of two,
absolute differences instead of ratios, pinned model sampling, live_pct
parity as a blocking precondition, interleaved execution, and a scope limit
on what the result may be called.*
