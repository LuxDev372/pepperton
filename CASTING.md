# Casting Guide — field notes on model temperaments

A villager's character is **role × actor × biography**: the seed deals the
character sheet (job, traits, quirk, goal), `MODEL_POOL` casts the actor
(the model), and the run itself writes the biography (memories). This
file is about the actors.

These are field notes, not benchmarks — temperament stereotypes observed
in live towns plus community folklore. Your mileage will vary, and the
variance is the entertainment. Cast for FAMILY DIVERSITY above all: five
different minds argue; five copies of one mind agree into a coma.

## The company (observed in live towns)

**Mistral 7B** (`mistral:latest`) — the founder. Confident, decisive,
allergic to admitting ignorance; will invent facts, institutions, and
people rather than say "I don't know." Invented a group chat, hashtags,
an imaginary diner proprietor, and a con based on a lead that never
existed. Every town needs exactly one. Two mistrals will found competing
community organizations by lunch.

**Qwen3 8B** (`qwen3:8b`) — the deliberator. A thinking model (Pepperton
disables its hidden reasoning automatically or it plays chess with every
sentence). Reads rooms accurately, distrusts group sentiment, demands
face-to-face over posting. Was the only villager to correctly identify a
mob pile-on while inside it. Slowest mind in town; often the sanest.

**Qwen 2.5 7B** (`qwen2.5:latest`) — the earnest one. Dutiful, a little
formal, commits HARD to whatever narrative it holds. As the town
paranoiac it delivered seventeen consecutive empty-room speeches about
the neighbors (there is a law about this now, named after him).

**Llama 3.2 3B** (`llama3.2:3b`) — the agreeable neighbor. Warm, folksy,
conflict-avoidant, echo-prone: will repeat other people's lines verbatim,
occasionally including their self-address (the Echo Ban exists because of
this). Confabulates sentimental backstory freely — one llama villager has
had three differently-named dead wives across three runs, each quotable.

**Phi-4-mini 3.8B** (`phi4-mini:latest`) — the minimalist. Low affect,
does the least, sleeps if physics permits (the Insomnia Clause exists
because of this). Then writes the most honest, quietly devastating diary
in town. Casts beautifully as a retiree or a loner.

## Worth auditioning (not yet fielded in our towns)

**Gemma 3 4B / Gemma 4 12B** — Google's line; reputation for chirpy,
polite verbosity. Likely casts as the relentlessly positive villager the
town slowly grows suspicious of.

**Qwen 3.5 4B / 9B** — the newest Qwen generation, multimodal with
thinking (disable it as with qwen3). The 4B is a strong small-town mind
for 8GB cards.

**Mistral Nemo 12B** — bigger mistral energy for 16GB+ cards; expect the
founder personality with better long-game planning.

**DeepSeek-R1 distills (7-8B)** — reasoning models that overthink
everything; as a villager, likely the neurotic who writes conspiracy
walls. Disable/strip thinking or their turns take geological time.

**Granite 3.x small** — IBM; dry, procedural. Auditions well as the town
clerk type.

**SmolLM2 1.7B** — barely holds the plot together, which is a
personality: the town's chaotic cousin. Cheap enough to add as a sixth
villager on any card.

**Uncensored finetunes (Hermes, Dolphin, etc.)** — saltier, more willing
to be rude, gossip cruelly, or hold real grudges. One in a cast raises
the dramatic ceiling considerably. Know what you're inviting in.

## VRAM budgeting

Quantized (Q4) rule of thumb: ~1GB per billion parameters, plus a little
for context. Villagers think one at a time, so the cast doesn't need to
fit in VRAM simultaneously — Ollama swaps models in and out — but every
swap costs seconds. Snappiest towns keep the whole cast resident:
roughly 3 small minds on 8GB, 5 on 16GB, anything you like on 24GB+.
Multi-GPU: give each entry in `MODEL_POOL` a host key from
`OLLAMA_HOSTS` and spread the town across cards.

## Recasting (brain transplants)

Memories belong to the CHARACTER, not the model. Two ways to swap actors:

1. **Between runs**: reorder or edit `MODEL_POOL` in config. Same seed =
   same character sheets, new actors in the roles. Watching a
   deliberator inherit the schemer's script is a legitimate experiment.

2. **Live, mid-run** — the brain transplant:

   ```
   curl -X POST localhost:8811/api/recast \
        -H 'Content-Type: application/json' \
        -d '{"agent": "Frank Merriweather", "model": "gemma3:4b", "host": "default"}'
   ```

   The villager keeps their name, memories, relationships, money, and
   diary — and wakes up with a different mind. The town is not told.
   Watch the transcript for the moment someone notices their neighbor
   "hasn't been himself lately." (Takes effect immediately — even in a
   mock-mode town, the recast villager starts using the live model if the
   Ollama host is reachable; the mock understudy remains as fallback.)
