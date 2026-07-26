# Skill Evaluation Framework — Working Spec

**Status:** working note for discussion (2026-07-26). Grounded in: the XNOBrain
Alignment Addendum (two-track verification, ledger, expiring badges), the marketplace
spec ([`plans.md`](plans.md) §6), and first-hand research of the local Hermes install
(`.tools/hermes-agent`, v0.19.0 — **183 skills**: 77 built-in + 106 optional).

The core problem: skills are heterogeneous — there is no universal quality metric.
This spec defines how every skill in the marketplace gets evaluated anyway.

---

## 1. Principles (settled in discussion)

- **No universal score.** A single blended number collapses meaning, invites gaming,
  and forces LLM-as-judge as the only universal grader — the trap the addendum names.
  We publish **scorecards**, never one number.
- **Route by ground-truth regime, not subject.** The right evaluation method depends on
  *who or what can prove the output right, and when* — not on the skill's topic.
- **Split "works" from "is right."** A universal **operational base layer** certifies
  reliability for every skill; a per-type **correctness track** certifies quality only
  where a regime supports it.
- **Badges name their regime.** "Ledger-verified", "Execution-verified 98%",
  "Truth-class B", "Fidelity-certified", "Community-proven" — never interchangeable.
- **Uplift is a first-class metric.** A skill must beat the *bare agent without it* on
  the same tasks; zero uplift = noise regardless of accuracy.
- **LLM-as-judge is triage only.** Allowed as a cheap pre-filter; never the certified
  result.
- **Performance is conditional on context, and context shifts.** Scorecards are
  context-conditioned; expiry clocks are tied to detecting context shifts (§7).

## 2. The five ground-truth regimes

Extends the addendum's three tracks (Outcome / truth_class / fidelity) with two more
that a general agent marketplace needs.

| # | Regime | Truth comes from… | Methods | Gameability |
|---|---|---|---|---|
| G1 | **World-graded** (addendum Track 1) | reality, later | outcome ledger: timestamped calls settled vs realized results (win rate, PnL attribution, hit ratio) | hardest to fake |
| G2 | **Source-graded** (addendum Track 2) | a verifiable reference, now | golden datasets, truth_class A–E, WER, citation validity | hard |
| G3 | **Execution-graded** (new) | running it | sandbox acceptance suites, end-state assertions, test pass rate, idempotency | hard |
| G4 | **Expert-graded** (addendum Track 3) | blind expert panel | fidelity: agreement + rationale consistency + abstention; rubric + inter-rater agreement | medium |
| G5 | **Crowd-graded** (new) | user *behavior* (not ratings) | retention, repeat-use, completion-without-retry, refund/uninstall rate | weakest — universal fallback |

Published gaming-resistance ordering: **G1 > G3 > G2 > G4 > G5**. Royalty tier and
premium pricing may be gated by regime strength (consistent with the addendum's
"only certified, unexpired skills earn royalties").

**Universal base layer (all skills, regardless of regime):** error/crash rate, latency,
cost per run (from existing usage metering), abstention correctness (refuses
out-of-scope input), determinism/stability across versions, safety violations,
prompt-injection resistance (adversarial probes).

**Certified = base layer passed + every declared facet's pack passed.**

## 3. Method catalog

- **Golden datasets** — curated inputs + reference answers (accuracy, F1, exact-match, WER).
- **Execution testing** — run in the sandbox, assert end state / tests pass.
- **Property / metamorphic testing** — no reference answer needed; test invariants:
  same input → consistent output, paraphrase → same conclusion, output schema-valid,
  *balance sheet balances* (finance models). Objective signal on label-free tasks.
- **Outcome ledger** — log timestamped calls now, settle against reality later.
  Backtests may appear on a listing (labeled) but **never earn the badge** — only
  forward, settled, regime-tagged results do.
- **Blind expert panel** — rubric scoring, inter-rater agreement (G4 spec = the
  original report's fidelity methodology, kept as the expansion track).
- **Behavioral telemetry** — kept-installed / reused / refunds; free from usage metering.
- **Adversarial probes** — injection, out-of-scope, unsafe-request handling (base layer).
- **Uplift vs baseline** — same agent, same tasks, with vs without the skill; report Δ.
- **LLM-as-judge** — triage/pre-filter only.

## 4. Task taxonomy → metric packs

A **versioned, finite taxonomy (~12 types)**. Each type maps to a metric pack:
metrics + method + data requirement + thresholds + its own expiry clock.

| Task type | Regime | Method | Core metrics | Data requirement | Expiry clock |
|---|---|---|---|---|---|
| Extraction | G2 | golden dataset | accuracy vs source, truth_class A–E | ✅ golden documents + references | source-format changes |
| Classification / labeling | G2 | golden dataset | precision / recall / F1 | ✅ labeled set | quarterly |
| Transformation / formatting | G2/G3 | golden set + validators | exact/schema match, round-trip | 🟡 input fixtures | quarterly |
| Calculation / quant modeling | G2 | deterministic recompute + property tests | numeric equality; invariants hold | 🟡 case fixtures | stable |
| Code generation | G3 | execution suite | test pass rate, compile rate | 🟡 repo fixtures + tests | dependency bumps |
| Workflow automation / tool-use | G3 | sandbox end-state assertions | task completion, idempotency, no side-effects | ❌ **fixtures**, not datasets | API changes |
| Retrieval / research | G2 | citation verification + seeded corpus | citation validity %, coverage/recall | 🟡 time-versioned corpora | continuous spot-check |
| Summarization | G2+G5 | faithfulness checks + behavior | hallucination rate; retention | 🟡 source/reference pairs | continuous |
| Content generation / creative | G5+property | validators + behavior (+ optional panel) | renders/valid, constraints met; retention | ❌ prompt suites + validators | continuous |
| Prediction / signal | **G1** | outcome ledger | win rate, PnL attribution, hit ratio — **regime-conditioned** (§7) | ⚠️ ledger + regime labels | **weeks** (measured decay / regime shift) |
| Advisory / judgment | G4 | blind expert panel | fidelity agreement, rationale, abstention | ✅ expert-labeled cases | re-rating cadence |
| Conversation / support | G5 | behavioral + property | resolution rate, escalation correctness | 🟡 scenario scripts | continuous |

**Knowledge/guidance packs** (a skill *shape*, not a task type — e.g. most of Hermes'
mlops group): evaluated by **uplift** on a per-skill task suite (10–30 scenarios),
graded by execution. Accuracy is meaningless for these; Δ vs bare agent is the metric.

## 5. Pipeline: classify-then-run

```
publish → declare facets (task types + output claim classes)   ← contractual, like app-store permissions
       → security gate (existing import-code quarantine + adversarial probes)
       → base layer (universal operational metrics)
       → per-facet metric packs (golden / execution / ledger / panel / behavioral)
       → uplift check vs bare agent
       → scorecard + per-facet badges, each with its own expiry clock
       → live: telemetry + drift monitoring → forced re-certification on expiry
```

- **Classification is declared, then verified — not auto-guessed.** The publisher
  declares task type(s); the harness probes the output schema to verify; a classifier
  may *suggest* but is never load-bearing. You can only advertise what was tested;
  misdeclaration is grounds for delisting.
- Reuses existing infrastructure: usage metering (base layer + G5), local sandbox
  (G3 harness), import quarantine (security gate). Net-new builds: the Ledger (G1)
  and the panel machinery (G4) — matching the addendum's sequencing (Ledger v0 first,
  fidelity R&D later).

## 6. Multi-task skills: decompose, don't average

- **Facet decomposition.** A listing declares each capability as a facet; each facet is
  classified and evaluated independently. The result is a **scorecard**, never an
  average — averaging a verified extractor with an unsettled signal is false for both.
- **Worked example — "BCTC analyst" skill:**
  - *extract financials* → extraction pack → truth_class B (94% vs source)
  - *compute ratios* → calculation pack → 100% deterministic recompute
  - *buy/sell view* → signal pack → ledger-enrolled, settling (badge pending N settled calls)
- **Per-claim grading inside one output.** Output schemas tag claim classes; each class
  is graded under its own regime — "citations verified; recommendation is
  expert-fidelity only."
- **Primary badge + facet list.** Headline badge = the primary facet (what buyers buy
  it for); secondary facets listed with their own results and clocks.
- **Gate rules.** Undeclared → untested → cannot be advertised or sold on that claim.
  Safety-relevant facets are weakest-link: one failure caps the listing state.

## 7. Context conditioning & the regime problem

A signal skill's win rate in a trending market says nothing about high-vol. An
unconditional number would be dishonest. Resolution:

- **Regime-tagged settlement.** Every settled ledger call carries a regime label at
  signal time. *XNO's Layer 3 metadata already does regime tagging — plug it in.*
- **Badges are scoped claims.** Not "62% win rate" but: *"Ledger-verified in trending
  markets (n=142, 62%); insufficient sample in high-vol (n=11)."* Unconditional badges
  require minimum settled samples per regime.
- **Abstention is graded positively.** A skill that declines to signal outside its
  regime is *more* certifiable, not less.
- **Regime shift = expiry trigger.** A detected shift suspends the badge to
  "re-settling" until new-regime samples accumulate. This generalizes the addendum's
  alpha-decay expiry.
- **Generalization — this is not just trading:** extraction breaks on filing-template
  changes; code breaks on dependency bumps; research decays with the web. Uniform rule:
  **every facet's scorecard is conditioned on its context, and its expiry clock fires
  on detected context shift** (market regime, source format version, dependency
  version, corpus date).
- **Live-Verified** (addendum tier) sits atop G1 — proven with real capital; a G3
  analog is possible ("running in production for N orgs").

## 8. Worked example: the Hermes library (183 skills) under this taxonomy

First-hand inventory of `.tools/hermes-agent` (v0.19.0):

| Task type | ~Count | Representative skills | Data requirement |
|---|--:|---|---|
| Workflow automation / tool-use | ~60 | github×5, google-workspace, notion, airtable, obsidian, shopify, docker-management, telephony, stripe, xurl, imessage, himalaya, openhue, computer-use, mcporter, watchers | fixtures only |
| Knowledge/guidance packs | ~35 | mlops group (peft, fsdp, faiss, qdrant, vllm, axolotl, trl, unsloth, whisper, stable-diffusion…), dspy, jupyter, bioinformatics | small uplift task suites |
| Code gen & dev process | ~20 | claude-code/codex/opencode delegation, tdd, systematic-debugging, plan, spike, requesting-code-review, fastmcp, debuggers | repo fixtures + tests |
| Content generation / creative | ~25 | architecture-diagram, excalidraw, pixel-art, manim-video, meme-generation, p5js, humanizer, comfyui | prompt suites + validators |
| Retrieval / research | ~15 | arxiv, duckduckgo/searxng, scrapling, osint-investigation, domain-intel, qmd, polymarket, stocks | time-versioned corpora |
| Transformation / formatting | ~8 | pdf, docx, xlsx, powerpoint, excel-author, pptx-author | input fixtures |
| Calculation / domain modeling | ~6 | dcf-model, lbo-model, 3-statement-model, comps, merger-model | case fixtures + property tests |
| Extraction | ~4 | ocr-and-documents, instructor, whisper (transcribe) | **golden sets** |
| Summarization | ~3 | youtube-content, teams-meeting-pipeline | source/reference pairs |
| Advisory / judgment | ~3 | fitness-nutrition, drug-discovery (partly) | expert-labeled cases |
| **Prediction / signal** | **0** | — (stocks/polymarket/hyperliquid are *data access*, not signals) | ledger + regime labels |
| Policy-gated (governance, not metrics) | ~6 | godmode, obliteratus, web-pentest, sherlock, unbroker | listing policy decision |

**Findings:**
1. **~85% of the Hermes catalog is execution-evaluable (G3/uplift) with fixtures** —
   no labeling program needed for the bulk of a marketplace.
2. **Hermes ships zero signal skills** — the ledger (G1) exists entirely for XNOBrain's
   marketplace additions (phím hàng, BCTC), which is exactly where XNO owns proprietary
   data (market data, filings, Layer 3 regime tags). Evaluation cost lands on the data
   advantage.
3. **Knowledge packs need uplift, not accuracy** — the only honest metric for
   guidance-shaped skills, and the one buyers care about.
4. The taxonomy covered all 183 skills with **no orphans** — validation that ~12 types
   suffice.

## 9. Open questions (for the next discussion)

1. **G3 acceptance-suite format** — the highest-design-decision area: who authors the
   fixtures (publisher-shipped + platform-audited?), how are mock services provisioned,
   what's the minimum suite size per task type?
2. **Thresholds** — pass bars per pack (e.g. extraction ≥ truth_class C to list,
   ≥ B for premium?). Tie to pricing tiers?
3. **Minimum regime samples** — how many settled calls per regime before an
   unconditional signal badge (n=30? 100?)?
4. **Policy-gated skills** — which of the security/red-team class are listable at all,
   and under what buyer verification?
5. **Uplift baseline pinning** — uplift Δ depends on the base model; re-run packs on
   model upgrades, or pin a reference model per certification cycle?
6. **Who pays for evaluation** — listing fee covers harness runs? Panel costs for G4?
   Ledger settlement is free (market does it) — price accordingly.

---
*Companions: [`plans.md`](plans.md) §6 (marketplace tiers) · XNOBrain Alignment
Addendum (ledger, royalty, expiry, Live-Verified) · `oss.md` / `enterprise.md`.*
