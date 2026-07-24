# Executive Summary

Brain4All is a self-hosted workspace for building and running AI agents, today built on
Nous Research's open-source **Hermes Agent** (MIT) plus a multi-provider LLM router, with
a clean open-core split already designed (a public runtime and a private enterprise
control plane). The commodity capabilities — the agent loop, memory, skills, tool/MCP
integration, and provider routing — are built or rented. This document sets out what the
product is, the market it enters, the competitive landscape, the defensible moat, the
business model, the technical architecture, and a phased plan to build the commercial
product.

## The product thesis

The commercial goal is a **"Bloomberg Terminal for verified expert AI twins."** Domain
experts encode their craft into skill/workflow **twins**. Each twin passes a
**fidelity-certification gate** — a measurement that the twin faithfully reproduces a
*named, real* expert's judgment on held-out cases — earns an **expiring trust badge**,
is listed in a **registry**, runs **on the customer's own data and runtime**, and pays
**royalties** back to the expert. Drift monitoring forces re-certification over time.

The strategic insight is one of scope. **Measuring fidelity is domain-agnostic** — it
compares a twin's decisions to the same expert's decisions, needing no knowledge of the
field — so one machine works for a steel analyst, a radiologist, or an M&A lawyer.
**Judging whether the expert is any good is not domain-agnostic**, so the terminal
deliberately refuses that job and leaves it to the market. That distinction is what makes
the platform both defensible and horizontally scalable.

## The market is large, fast, and validating

- The agentic-AI market is roughly **$9–12 billion in 2026, growing 35–50% per year**
  across multiple independent forecasts.
- Every major platform has launched an agent marketplace — Salesforce's reached **18,500
  customers in under a year** — but marketplaces without verification commodify (the GPT
  Store economics work out to roughly **$0.03 per conversation**).
- Expert-cloning is being funded fast and hard: **Viven** raised a **$35M seed** for
  enterprise "employee digital twins," **Delphi** raised **$16M** from Sequoia, and
  **Cloneable** reported **100× ARR growth**. Digital-twin round sizes have roughly
  **doubled** year over year.
- AI-evaluation adoption is set to **triple to 60% of engineering teams by 2028**.

## The whitespace — and it is narrowing

The "clone a named expert" square is now crowded: Viven, IgniteTech, Interloom,
Cloneable, Delphi, and Coachvox all clone people. "We make expert twins" is therefore **no
longer differentiating**. What *no one* does — verified across more than twenty
companies — is **certify that a clone faithfully reproduces a specific named expert**
(measured, with an expiring badge), **monitor drift**, and **settle royalties** across
organizations while the twin runs on the **customer's own runtime**. Evaluation startups
measure *application quality*, not fidelity to a person; marketplaces vet *partners*, not
humans. The strategic imperative is to lead with **certification and royalty**, not with
"twins" — otherwise Brain4All is one of a dozen.

## The moat, made concrete

Fidelity is defined operationally as the agreement between a twin's decision and the same
expert's decision on **held-out cases the twin never saw**, scored by a **blend** — objective
agreement, rationale consistency, blind expert re-rating, and correct abstention on
out-of-coverage cases — **never a single LLM-as-judge** (whose biases are documented). The
gate compounds into assets competitors cannot copy: the paired craft-ledger corpus, the
longitudinal drift benchmarks that justify badge expiry, the trusted badge standard
itself, and the royalty-lineage settlement ledger.

## Business model and technology

The company follows a proven **open-core** model: a permissive/AGPL open-source runtime
plus a closed enterprise control plane. Because the moat (certification, registry,
identity, royalty, billing) is private by design, the core can stay genuinely open,
maximizing adoption. Firms self-host or run in a managed cloud; revenue evolves from
enterprise licenses to per-twin fees to certification fees and royalties.

Technically, the recommended architecture keeps the Hermes agent runtime warm behind its
own API, routes LLM traffic through the router, and builds the moat and control plane in a
separate **Go + PostgreSQL** layer. A full reimplementation of the agent runtime is
explicitly *not* recommended (see the architecture and build-vs-rewrite sections).

## The plan in one line

Ship the open-source product with a clear license; prove the certification moat with a
Phase-1 internal wedge in a single vertical; then make certification matter across an
organization, open a cross-organization shelf with royalties, and finally establish the
provenance-and-fidelity standard as the industry's shared language.

## Key clarifications carried through this report

- **Hermes is a real product** — Nous Research's MIT-licensed Hermes Agent — not an
  internal alias. MIT permits building a closed commercial layer on top, but it also means
  the moat cannot be "we wrapped Hermes"; it must be certification, craft data, identity,
  and royalty.
- **The open-source runtime is Python; Go belongs to the enterprise layer.** An earlier
  Go/Postgres path in the open-source repository was retired; Go now lives on the
  enterprise control-plane side.
- **The open-source core still needs a license chosen** before public distribution — a
  hard launch prerequisite.
- Market-sizing figures are directional (independent firms disagree by ~25%); ranges are
  shown rather than a single point estimate.
