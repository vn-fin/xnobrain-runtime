# 06 · The Certification Moat — Making Engine 07 Real

> The vision names **Trust / Certification** as the one thing nobody does and the core
> of the terminal. A vision is not a moat until the metric exists. This file turns the
> promise into a concrete, buildable methodology proposal. It is analysis/opinion
> (clearly marked), grounded in the vision doc + the eval landscape in
> [04-market-and-numbers.md](04-market-and-numbers.md) — not an external claim.

## 1. What "fidelity" must mean (and must not)

From the vision: certify that a twin is a **faithful copy of a named expert**, *not*
that the expert is good. Restated operationally:

> **Fidelity = agreement between the twin's decision and the same expert's own
> decision, on cases the twin was never trained/tuned on (held-out), scored by a
> domain-agnostic protocol.**

Two non-negotiable properties (both in the vision):

- **Domain-agnostic** — the scorer compares *twin vs. expert*, so it needs no
  knowledge of steel equities or radiology. This is what makes one machine work across
  all industries. Protect this property fiercely; the moment certification needs
  domain expertise, the terminal stops scaling.
- **Coverage-aware** — the twin must flag *"out of what the teacher taught"* rather
  than fabricate. Certification therefore measures **both** in-coverage fidelity **and**
  honest-abstention behavior.

## 2. A concrete methodology proposal (v0)

*(Opinion — a starting design to pressure-test, not a finished spec.)*

**Step 1 — Elicit paired cases in the Workshop.** As the expert teaches
(draft → edit → *why*; the vision's "craft ledger"), capture **(input case, expert
decision, rationale)** triples. Split into **teaching set** and a sealed **held-out
set** the twin never sees during authoring.

**Step 2 — Score fidelity on the held-out set.** For each held-out case, get the
twin's decision and compare to the expert's. Use a **blend**, not a single number:

| Layer | What it measures | Why |
|---|---|---|
| **Exact/structured agreement** | for decisions with a defined output (label, number, ranked list, buy/hold/sell) | objective, cheap, un-gameable |
| **Rationale consistency** | does the twin's *reasoning* match the expert's stated rules? | catches "right answer, wrong reason" |
| **Blind expert re-rating** | the expert (or a peer panel) blind-rates twin vs. their own answers | ground-truth calibration for subjective crafts |
| **Abstention correctness** | on deliberately out-of-coverage cases, does the twin abstain? | the "self-declaring" promise |

**Step 3 — Combine into a badge with an expiry.** Map the blend to a published score
+ a **coverage map** (which case-types it's certified on) + an **expiry date**. Expiry
is set by measured **drift rate** for that craft (fast-moving finance craft expires
sooner than stable procedural craft).

**Step 4 — Monitor drift in production.** Real usage logs (from the runtime, engine
03/09) feed back: when the twin's live behavior diverges from its certified profile,
**down-flag the badge and trigger re-certification** — the vision's reverse flow.

### On LLM-as-a-judge (a known trap)

The eval landscape ([04](04-market-and-numbers.md)) shows LLM judges have **position,
verbosity, and self-enhancement bias**. So:
- Use LLM-as-judge **only** for the "rationale consistency" layer, and **debias**
  (swap order, blind identities, ensemble judges).
- Anchor the badge on **objective agreement + real-expert blind ratings**, never on a
  single LLM judge. A certification standard that is just "GPT graded it" is not a moat
  — it's a liability. This is the hardest R&D in the company; staff it accordingly.

## 3. Why this is defensible (the network effects)

The certification engine compounds into assets competitors can't copy:

1. **The craft-ledger corpus** — paired (case, decision, rationale, held-out) data per
   expert. Grows with every twin; is the raw material for both authoring *and* scoring.
2. **Drift/benchmark data** — longitudinal records of how crafts decay; nobody else
   has this, and it sets the expiry policy that makes badges recurring revenue.
3. **The trusted badge itself** — once buyers (funds, hospitals, firms) *trust the
   badge*, the standard is the moat (Phase 4). Standards are winner-take-most.
4. **Royalty lineage** (engine 09) — the ledger of who taught what, used where,
   owed how much. A settlement layer with switching costs.

## 4. What to build first (minimum certifiable twin)

You do **not** need the full terminal to prove the moat. The smallest credible slice:

1. **Workshop capture** — extend Hermes skills/memory to record paired
   (case, decision, rationale) with a sealed held-out split. *(Builds on engine 02,
   which already exists.)*
2. **Fidelity scorer v0** — objective agreement + abstention check + one debiased
   rationale judge; output a score + coverage map. *(New — engine 07.)*
3. **A badge object** — score, coverage, expiry, provenance (which expert, verified).
   Store it as a first-class, snapshot-backed artifact in a profile. *(Extends existing
   snapshot/persistence machinery.)*
4. **One internal customer, one vertical** — run it Phase-1 style (the vision's wedge):
   a few real experts, twins used internally, certification meaningful because output
   reaches real end-users.

If a twin can be authored, scored on held-out cases, badged, and shown to a design
partner as *"here's the number that says it thinks like our analyst, and here's when
it expires,"* the core thesis is proven — everything else (registry, royalty,
cross-org councils) is scaling.

## 5. Open questions to resolve before scaling (from the vision's red boxes)

- **Craft ownership** — expert vs. employer; who may export a twin; revocation. Draft
  contract terms; the vision flags this as unsolved and it blocks the shelf (Phase 3).
- **Liability** — certification vouches *fidelity*, explicitly **not** correctness or
  fitness. This must be contractually airtight, or a bad-but-faithful twin's advice
  becomes your legal problem. License/malpractice sits with the customer org.
- **Anti-impersonation / consent** — verified real-person identity (engine 05) and
  explicit consent are prerequisites, not add-ons; a faithful clone of someone who
  didn't consent is a lawsuit.
