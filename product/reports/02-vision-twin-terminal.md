# 02 · The Vision — "Twin Terminal" Deconstructed

> Scope: a faithful English rendering and analysis of the two Vietnamese vision
> artifacts in `product/` (`twin_terminal_product_map.html` and
> `twin_terminal_flow_network.html`). Nothing here is invented — it is the product
> vision restated for a working audience and stress-tested against the current build
> and the market.

## 1. The one-line thesis

> **"Bloomberg guarantees the data. This terminal guarantees the twin."**

Bloomberg's product was never "financial data" in the abstract — it was a
**trusted, standardized, verified** rail that every professional sits in front of all
day. Twin Terminal proposes the same move for **expert AI twins**: each twin encodes
one named expert's craft (a skill/workflow), and the terminal's job is to
**guarantee the twin is a faithful copy of that real, named person** — not to judge
whether the expert is any good.

That distinction is the whole strategy, and it is why the machine is
**industry-agnostic**:

- **Measuring fidelity** = comparing the twin's output to the *same expert's* own
  judgment on held-out cases. This needs **no domain knowledge** → one machine works
  for a steel analyst, a radiologist, or an M&A lawyer.
- **Judging expertise quality** = needs deep domain knowledge → the terminal
  deliberately **refuses** this job and leaves it to the market.

Bloomberg analogy, stated in the artifact: *Bloomberg guarantees the price is
reported accurately; it does not guarantee the bond is worth buying.* Same logic.

## 2. The five promises (the certification gate's contract)

Every twin that flows through the terminal is guaranteed to be:

1. **Real provenance** — genuinely from the named person; identity verified,
   anti-impersonation.
2. **Measured** — fidelity to the expert, scored on held-out cases. Badges **expire**.
3. **One-click install** — no integration project; plugs into the customer's existing
   data.
4. **Runs stably** — no mid-run crashes, no infinite loops, predictable cost.
5. **Self-declaring** — when out of coverage it says *"the teacher never taught me
   this"* — it does not fabricate.

What the terminal **vouches for**: faithful copy, real named provenance, installable
and stable. What it **explicitly does not vouch for**: whether the expert is good, or
whether the advice is correct. *The market judges the person; the terminal judges the
copy.*

## 3. The flow (the hourglass)

Both artifacts describe a **many-to-many hourglass with a single chokepoint** —
winning the chokepoint is the entire strategy.

```
SUPPLY (experts, every field, every country)
   → Workshop (teach the craft: draft–edit–why, contrast cases, guess-rules-to-refute → "craft ledger", FREE)
      → ★ CERTIFICATION GATE  (real-person provenance · fidelity · stability — FAIL = not shelved)  ← the core
         → Shelf / Registry (search by craft · badge + expiry · public coverage map · expert sets price)
            → Runtime + Council (one-click install · runs on customer data · cross-org twin panels)
               → Commerce layer (entitlements · metering · lineage & royalty)
DEMAND (customers, every field, every country) — pay: seat fee + per-installed-twin fee
```

Three **reverse flows** at the bottom keep the system alive:

- **Usage logs** → drift measurement → triggers **re-certification** (craft decays
  over time, e.g. financial craft).
- **Royalty** → flows back to the expert and their host organization.
- **Hard cases** → feed back into the teaching loop.

Two **unanswered red-box questions** the artifacts honestly flag:

- **Supply side:** *Who owns the craft?* (the expert, or their employer? who may
  press "export"?) — must be settled contractually up front.
- **Demand side:** *Where does the license/liability sit?* — with the customer
  organization, not the terminal. Regulator + malpractice risk lives with the buyer.

## 4. The 9 engines (and which are moats)

Color legend from the artifact: **grey = rented / open standard**, **teal =
accumulating asset**, **brass = the terminal itself, must be built**.

| # | Engine | Type | Note |
|---|---|---|---|
| 01 | **Brain** (multi-model LLM, swappable) | grey | *Never a moat.* |
| 02 | **Memory** (craft ledger travels with the twin) | teal | Mechanism is commodity; **content is the asset.** |
| 03 | **Execution** (run twin on customer data, sandbox, predictable cost) | grey | |
| 04 | **Orchestration** (router by craft, cross-org councils, disagreement maps) | grey→ | |
| 05 | **Identity** (verify real person, anti-impersonation, name on every atom) | **brass** | Moat. |
| 06 | **Improvement loop** (extract tacit knowledge — what fills the shelf) | **brass** | Moat. *"No platform has this yet."* |
| 07 | **Trust / Certification** ★ | **brass CORE** | The one thing nobody does: fidelity + coverage flags + drift + **expiring badges.** |
| 08 | **Connection** (MCP/connectors, chat/embed/API) | grey | Ride open standards. |
| 09 | **Commerce** (entitlements, metering, billing, **lineage & royalty**) | **brass** | Moat. |

**Mapping to the current build** (see [01-current-state.md](01-current-state.md)):
Engines 01, 03, 08 and most of 04 are **already delivered or rented** via Hermes +
9router. Engine 02's *mechanism* exists (Hermes memory/skills). Engines **05, 06, 07,
09** are the greenfield moats — and 07 is the single hardest, highest-leverage build.

## 5. The go-to-market sequence (how the terminal fills)

The artifact is refreshingly honest that **the terminal is the destination, not the
starting point** — *"Bloomberg 1982 = Merrill Lynch + bonds."* Sequence:

| Phase | Name | Shape | Engines on | Money | Gate to advance |
|---|---|---|---|---|---|
| **1** | Wedge | One field, one country. A few experts; twins used **internally** by one org. No shelf. | 01–04, 06 | Infra fee from the org | Twins genuinely converge; used weekly |
| **2** | Accumulate | Many twins, one org. Internal councils, house view. **Certification first becomes meaningful** (output reaches end customers). | +07, 09 | Per-broker & end-customer, white-label | "Atom discipline" — errors don't compound down a chain |
| **3** | Open the shelf | Cross-org, one field. Twins rented to funds, wealth banks, other orgs. **Royalty starts flowing.** | 05 goes public, 09 central | Royalty + certification fees | Badges trusted by outsiders |
| **4** | Terminal | Cross-field, cross-country. Provenance + fidelity standards become a **shared language.** | all + registry + lineage | Seat fee + multi-tier royalty | Open from an earned standard, not a blank page |

The chicken-and-egg problem **dissolves** because supply for the shelf is generated in
Phases 1–2 under the cover of an internal tool.

**Two hard, unsolved problems the artifact names honestly:**

1. **The supply wall** — will genuinely great experts consent to being cloned?
2. **The seat on the screen** — the most expensive real estate in the world;
   Bloomberg took ~40 years to win it.

## 6. Why this vision is defensible (analyst's read)

- It picks a **moat that is industry-agnostic** (fidelity measurement) while
  **refusing** the parts that require domain expertise — a rare, disciplined scoping
  choice.
- It has a **credible cold-start path** (internal tool → shelf) instead of a
  build-the-marketplace-and-pray plan.
- It attaches a **recurring, defensible revenue mechanic** (expiring badges + drift →
  forced re-certification; royalties + lineage) rather than a one-time sale.
- The moats compound: identity + certification + the craft ledger + royalty lineage
  are **data/network assets that get stronger with use** and are hard to copy.

## 7. Where the vision needs sharpening (feeds the roadmap)

1. **Fidelity metric definition.** "Consistency with the expert on held-out cases" is
   the core claim — it needs a concrete, defensible methodology (metric, held-out
   protocol, score→badge mapping, expiry policy). This is the single most important
   artifact to author next. See [06-certification-moat.md](06-certification-moat.md).
2. **Craft-ownership contract** (the supply-side red box) — template terms for
   expert-vs-employer IP, export rights, and revocation.
3. **Liability boundary** (the demand-side red box) — explicit contract language that
   licensing/malpractice sits with the customer org.
4. **Twin ≠ the expert, legally and in UX** — anti-impersonation and clear
   "faithful copy, not the person" labeling to avoid deception and defamation risk.
5. **Which vertical is Phase-1?** The flow diagram hints **finance in Vietnam**
   (steel-equity judgment, brokerages → retail investors). That should be an explicit
   decision, since everything downstream depends on it.
