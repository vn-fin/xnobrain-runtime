# Brain4All / Twin Terminal — Strategy & Research Report

> Written 2026-07-24. A grounded analysis of your vision, your current build, the
> market, the competitive landscape, and a sequenced plan. Every external claim is
> linked and dated; every opinion/recommendation is marked as such. Nothing here is
> fabricated — where a number couldn't be verified, the file says so.

## How to read this

Start here, then open the file you need. Each file is standalone and cross-links the
others.

| # | File | What it answers |
|---|---|---|
| 01 | [Current state](01-current-state.md) | What Brain4All *is* today (code, architecture, edition split, gap to vision) |
| 02 | [The vision](02-vision-twin-terminal.md) | "Twin Terminal" deconstructed from your HTML artifacts (5 promises, hourglass, 9 engines, 4 phases) |
| 03 | [Core tech & competitors](03-core-tech-and-competitors.md) | Hermes Agent capabilities/limits/**MIT license**, the router landscape, OpenClaw & other runtimes |
| 04 | [Market & numbers](04-market-and-numbers.md) | Hard figures: market size, funding, adoption — with sources |
| 05 | [Startup landscape](05-startup-landscape.md) | Delphi, Cloneable, OpenClaw, Salesforce & the **whitespace you can own** |
| 06 | [The certification moat](06-certification-moat.md) | How to make engine 07 (fidelity certification) real and buildable |
| 07 | [Business model & licensing](07-business-model-and-licensing.md) | Open-core precedents + concrete **license recommendations** |
| 08 | [Roadmap](08-roadmap.md) | Studio → Terminal, sequenced by phase and engine |
| 09 | [Hermes use-case catalogue](09-hermes-use-cases.md) | All 262 Hermes user stories catalogued in detail + which are nearest to Twin Terminal |
| 10 | [Go rewrite analysis](10-golang-rewrite-analysis.md) | Should we reimplement Hermes in Go + Postgres? (build-vs-rewrite decision, time estimates) |

## Executive summary (the whole thing in one page)

**What you have.** A working, self-hosted **agent studio**: one FastAPI process
wrapping **Nous Research's Hermes Agent** (MIT — self-improving skills, 3-layer memory,
MCP, sandboxes) plus a multi-provider LLM router, with a clean **open-core split**
already designed (public `brain4all` + private Go `brain4all-enterprise` control
plane). The commodity engines are built or rented. [→01](01-current-state.md),
[→03](03-core-tech-and-competitors.md)

**What you're actually selling (the vision).** A **"Bloomberg Terminal for verified
expert AI twins."** Experts encode their craft into twins; a **fidelity-certification
gate** (the moat) guarantees each twin is a faithful copy of a *named real expert* —
measured on held-out cases, with **expiring badges** and **drift-triggered
re-certification** — then twins are shelved, run on the customer's data, and pay
**royalties** back. The genius is scoping: certifying *fidelity* is domain-agnostic
(one machine, every industry); judging *expertise* is not, so the terminal refuses it.
[→02](02-vision-twin-terminal.md)

**The market is real and the timing is right.**
- Agentic-AI market **~$9–12B in 2026**, growing **35–50%/yr** across multiple firms.
- Every platform shipped an agent marketplace (Salesforce's hit **18,500 customers**
  in <1yr) — but **unverified marketplaces commodify** (GPT Store ≈ **$0.03/conv**).
- Expert-cloning is funded fast: **Delphi $16M/Sequoia**, **Cloneable 100× ARR**.
- AI-evaluation adoption set to triple to **60% of eng teams by 2028**.
[→04](04-market-and-numbers.md)

**The whitespace you own.** *No one* certifies that a clone faithfully reproduces a
**specific named expert** with an **expiring badge**, monitors drift, and settles
**royalties** across orgs while running on the **customer's own data**. Delphi/Cloneable
clone experts but don't certify fidelity; eval startups measure app quality, not
fidelity-to-a-person; marketplaces vet partners, not humans. That intersection is
empty. [→05](05-startup-landscape.md)

**The moat, made concrete.** Fidelity = held-out agreement between twin and expert,
scored by a **blend** (objective agreement + rationale consistency + blind
expert re-rating + abstention correctness), **not** a single LLM judge (whose biases
are documented). It compounds into un-copyable assets: the craft-ledger corpus, drift
benchmarks, the trusted badge standard, and royalty lineage. [→06](06-certification-moat.md)

**How you make money & protect it.** Textbook **open-core**: permissive/AGPL OSS core
+ closed enterprise control plane (the moat is *already* private by design, so the
core can stay genuinely open). Firms self-host or run in your cloud; revenue evolves
from enterprise licenses → per-twin fees → certification fees + royalties.
[→07](07-business-model-and-licensing.md)

**What to do next (the sequence).**
1. **Ship the honest OSS product** — *pick the core license* (launch blocker), fix the
   Python-vs-Python+Go story, harden the enterprise boundary.
2. **Phase 1 wedge** — one vertical (vision hints **finance/Vietnam**), build
   **Workshop capture + fidelity scorer v0 + badge + identity v0**, run twins
   *internally* at a design partner. **This proves the moat.**
3. Then: make certification matter (Phase 2) → open the shelf with royalties
   (Phase 3) → become the standard (Phase 4).
[→08](08-roadmap.md)

## Three corrections to your framing (read these)

1. **"Hermes" is not an alias — it's a real product.** Brain4All genuinely wraps
   **Nous Research's Hermes Agent** (MIT, launched Feb 2026). MIT means you *can*
   legally build a closed commercial layer on top — but it also means your moat
   **cannot** be "we wrapped Hermes"; it must be certification + craft data + identity
   + royalty. [→03](03-core-tech-and-competitors.md) §1.
2. **The OSS repo is Python, not "Python + Go."** The Go/Postgres OSS path was
   *retired*; Go now lives on the **enterprise** side. Reconcile your pitch with the
   code — recommended: OSS = Python + React, Go = enterprise only.
   [→01](01-current-state.md) §4, [→07](07-business-model-and-licensing.md) §2.
3. **Your OSS core has no license yet.** The README says "choose and add a license
   before public distribution." This is a hard launch blocker.
   [→07](07-business-model-and-licensing.md) §2.

## Method & honesty note

- **Local sources:** the repository (`AGENTS.md`, `README.md`, `docs/*`), and your two
  vision artifacts (`product/twin_terminal_*.html`).
- **External sources:** live web search + fetch (July 2026). The deep-research
  multi-agent workflow was attempted but failed on a harness error (structured-output
  retry cap); research was completed via direct, cited searches instead — every claim
  in files 03–07 carries a source link and date.
- **Opinion vs fact:** market/funding/license facts are linked; strategy
  recommendations (files 06–08 especially) are explicitly marked as opinion for you to
  pressure-test.
- Market-sizing figures are directional (firms disagree ~25%); ranges are shown rather
  than a single cherry-picked number.
