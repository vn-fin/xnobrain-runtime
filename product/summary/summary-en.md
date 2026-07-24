# Brain4All — Twin Terminal · Executive Brief

## In one line

Brain4All is a self-hosted platform for building AI agents, evolving into a **"Bloomberg
Terminal for verified expert AI twins"**: experts encode their craft into twins that pass
a fidelity-certification gate, run on the customer's own data, and pay royalties back.

## The product

- **Today** — a self-hosted agent workspace built on Nous Research's **Hermes Agent**
  (MIT) plus a multi-provider LLM router, with an open-core split (a public runtime and a
  private enterprise control plane).
- **Goal** — domain experts encode their craft into skill/workflow **twins**. Each twin
  passes a **fidelity-certification gate** (proof it faithfully reproduces a *named*
  expert on held-out cases), earns an **expiring trust badge**, is listed in a
  **registry**, runs on the customer's own data and runtime, and pays **royalties** to the
  expert. Drift monitoring forces re-certification.
- **The scoping insight** — measuring *fidelity* is domain-agnostic (one machine works for
  every industry); judging whether the expert is *good* is not — so the terminal
  deliberately refuses that job.

## The moat

Fidelity is the agreement between a twin's decision and the same expert's decision on
**held-out cases**, scored by a blend — objective agreement, rationale consistency, blind
expert re-rating, and correct abstention — **never a single LLM-as-judge**. The gate
compounds into un-copyable assets: the craft-ledger corpus, drift benchmarks, the trusted
badge standard, and the royalty-lineage ledger.

## The market

- Agentic-AI market **~$9–12B in 2026, growing 35–50% per year**.
- Every platform launched an agent marketplace (Salesforce's reached **18,500 customers in
  under a year**), but marketplaces without verification commodify (GPT Store ≈ **$0.03 per
  conversation**).
- Expert-cloning is funded fast: **Viven $35M seed** (enterprise employee twins),
  **Delphi $16M / Sequoia**, **Cloneable 100× ARR**; digital-twin round sizes roughly
  **doubled** year over year.
- AI-evaluation adoption is set to **triple to 60% of engineering teams by 2028**.

## The whitespace (and it is narrowing)

The "clone a named expert" square is now crowded — Viven, IgniteTech, Interloom, Delphi,
Coachvox all clone people. **No one certifies fidelity to a specific named expert** with an
expiring badge, monitors drift, and settles royalties across organizations while the twin
runs on the customer's own runtime. The imperative: **lead with certification and royalty,
not with "twins."**

## Business model & technology

- **Open-core** — a permissive/AGPL open-source runtime plus a closed enterprise control
  plane. Because the moat is private by design, the core can stay genuinely open.
- **Do not rewrite Hermes in Go.** The agent loop is bound by model latency, so a rewrite
  yields no real speedup, forfeits Hermes's upstream, and breaks the Python plugin
  ecosystem. Keep Hermes warm behind its own API, route through the LLM router, and build
  the moat and control plane in **Go + PostgreSQL**.

## The plan

0. **Ship the open-source product** — choose a license (a launch blocker).
1. **Wedge** — one vertical: build workshop capture, a fidelity scorer, and the badge; run
   twins internally at a design partner. This proves the moat.
2. **Accumulate** — internal councils, drift monitoring, usage metering.
3. **Open the shelf** — a cross-organization registry with royalties.
4. **Terminal** — provenance and fidelity become the industry's shared standard.

## Key clarifications

- **Hermes is a real product** (Nous Research, MIT) — not an internal alias.
- **The open-source runtime is Python**; Go belongs to the enterprise layer.
- **The open-source core still needs a license chosen** before public distribution.
