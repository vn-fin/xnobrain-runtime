# 08 · Product Roadmap — From Studio to Terminal

> Ties the current build ([01](01-current-state.md)), the vision
> ([02](02-vision-twin-terminal.md)), the moat ([06](06-certification-moat.md)), and
> the business model ([07](07-business-model-and-licensing.md)) into a sequenced plan.
> Phases mirror the vision's Wedge → Accumulate → Open-the-Shelf → Terminal, mapped to
> the 9 engines. Opinion/synthesis — adjust to your resources.

## 0. Guiding principle

> **Don't build the terminal. Build the smallest thing that proves the certification
> moat, under the cover of a useful internal tool — then let the shelf fill itself.**

The vision says this explicitly ("Bloomberg 1982 = Merrill Lynch + bonds"). The
commodity engines (brain/execution/connection/routing) are **done or rented**. Spend
almost all net-new effort on **engines 05–07 & 09**.

## Phase 0 — Ship the honest OSS product (now → next weeks)

*Goal: a clean, launchable open-source agent studio + a working enterprise boundary.
This is mostly done; close the gaps.*

- [ ] **Pick the OSS core license** (Apache-2.0 or AGPL-3.0) — README launch blocker.
- [ ] Add NOTICE / third-party license inventory (Hermes MIT + router + deps).
- [ ] Reconcile the **Python-only vs Python+Go OSS** story with the code and pitch
      ([07](07-business-model-and-licensing.md) §2).
- [ ] Harden the enterprise edition contract (RBAC/SSO/audit/quotas already specified).
- **Engines live:** 01–04, 08 (all commodity/rented). **Money:** infra/enterprise fee.
- **Exit gate:** a firm can `docker compose up` the OSS product and a design partner
  can run the enterprise edition on their server.

## Phase 1 — The Wedge: one vertical, one org, certifiable twins (the real start)

*Goal: prove engine 07 exists. This is the company's make-or-break phase.*

- [ ] **Pick the Phase-1 vertical + country.** The vision hints **finance / Vietnam**
      (steel-equity judgment, brokerage → retail). Decide explicitly.
- [ ] **Workshop capture** — extend Hermes skills/memory to record paired
      (case, decision, rationale) with a **sealed held-out split** (engine 06).
- [ ] **Fidelity scorer v0** — objective agreement + abstention check + one *debiased*
      rationale judge → score + coverage map (engine 07). See
      [06-certification-moat.md](06-certification-moat.md).
- [ ] **Badge artifact** — score + coverage + expiry + verified provenance, stored as
      a first-class snapshot-backed object (reuses existing persistence).
- [ ] **Identity v0** — verify the real named expert; consent capture (engine 05).
- [ ] Run twins **internally** at one design-partner org; output reaches real users.
- **Engines live:** +06, 07, 05 (internal). **Money:** infra fee from the org.
- **Exit gate:** *"Here's the number that says this twin thinks like our analyst, and
  here's when it expires."* Twins used weekly; a design partner will vouch.

## Phase 2 — Accumulate: many twins, one org, certification that matters

*Goal: certification gate becomes load-bearing because output reaches end customers.*

- [ ] **Internal council** — combine multiple twins into a house view; disagreement
      maps (engine 04 extension).
- [ ] **Drift monitoring** — production usage logs down-flag badges → re-certify
      (engine 07 reverse flow).
- [ ] **Entitlements + metering** for twins (engine 09 — reuse enterprise billing).
- [ ] "Atom discipline" — structure outputs as **conclusion + evidence + coverage
      flag** so errors don't compound down a chain.
- **Engines live:** +09 (metering). **Money:** per-broker / per-end-customer,
  white-label.
- **Exit gate:** certification is trusted internally; twin errors are contained.

## Phase 3 — Open the Shelf: cross-org registry + royalties

*Goal: twins rented across organizations; the marketplace and royalty flywheel start.*

- [ ] **Registry / shelf** — search by craft, public coverage maps, expiring badges,
      expert-set pricing.
- [ ] **Identity goes public** — badges trusted by outsiders (engine 05 public).
- [ ] **Royalty & lineage settlement** (engine 09 core, net-new) — who taught what,
      used where, owed how much.
- [ ] **Resolve the red boxes** — craft-ownership contracts + fidelity-liability terms
      ([06](06-certification-moat.md) §5, [07](07-business-model-and-licensing.md) §4).
- **Engines live:** all, 09 central. **Money:** royalty + certification fees.
- **Exit gate:** an outside org rents a twin it trusts *because of the badge*, and a
  royalty settles to the expert.

## Phase 4 — The Terminal: cross-field, cross-country standard

*Goal: provenance + fidelity become a shared language; you own the chokepoint.*

- [ ] Registry + lineage at scale; cross-field, cross-country twins.
- [ ] Seat fee + multi-tier royalty.
- [ ] Fight for **"the seat on the screen"** — the hardest, longest battle.
- **Condition:** open from an *earned standard*, not a blank page.

## Cross-cutting workstreams (run continuously)

- **Certification R&D** — the moat; never "done." Debias judges, validate against
  blind expert ratings, refine drift/expiry models. Staff it like core research.
- **Supply strategy** — target *retiring / about-to-exit* expertise (Cloneable's
  wedge) to dissolve the "supply wall" consent problem
  ([05](05-startup-landscape.md) §4).
- **Legal/contract** — craft ownership, liability, consent, anti-impersonation — gate
  every phase; don't let them lag the product.
- **Keep commodity engines rented** — do **not** out-build OpenRouter/LiteLLM or fork
  Hermes' core; extend from `brain4all`, per `AGENTS.md`.

## The one-line sequencing rule

> Prove the badge (Phase 1) → make it matter (Phase 2) → let it travel with royalties
> (Phase 3) → make it the standard (Phase 4). Everything commodity, rent. Everything
> moat (05/06/07/09), build — starting with the fidelity scorer.
