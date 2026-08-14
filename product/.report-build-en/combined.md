# Executive Summary

XNOBrain is a self-hosted workspace for building and running AI agents, today built on
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
"twins" — otherwise XNOBrain is one of a dozen.

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


# The Vision — "Twin Terminal" Deconstructed

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

**Mapping to the current build** (see the Current State section):
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
   artifact to author next. See the Certification Moat section.
2. **Craft-ownership contract** (the supply-side red box) — template terms for
   expert-vs-employer IP, export rights, and revocation.
3. **Liability boundary** (the demand-side red box) — explicit contract language that
   licensing/malpractice sits with the customer org.
4. **Twin ≠ the expert, legally and in UX** — anti-impersonation and clear
   "faithful copy, not the person" labeling to avoid deception and defamation risk.
5. **Which vertical is Phase-1?** The flow diagram hints **finance in Vietnam**
   (steel-equity judgment, brokerages → retail investors). That should be an explicit
   decision, since everything downstream depends on it.


# Market & Numbers

> Hard figures with sources and dates, to support the vision and fund-raising
> narrative. Where firms disagree, all estimates are shown — do not cherry-pick one
> number without the range. Market-sizing reports are directional, not gospel; treat
> them as order-of-magnitude.

## 1. The agentic-AI market (your category)

The 2026 market size estimates cluster around **$9–12 billion**, with very high
growth forecasts (mid-30s to ~50% CAGR):

| Source | 2026 size | CAGR | Long-term |
|---|---|---|---|
| [Fortune Business Insights](https://www.fortunebusinessinsights.com/agentic-ai-market-114233) | ~$9.14B | 40.5% (→2034) | ~$139B by 2034 |
| [Grand View Research](https://www.grandviewresearch.com/industry-analysis/ai-agents-market-report) | ~$10.9B | 49.6% (2026–33) | ~$182.9B by 2033 |
| [Precedence Research](https://www.precedenceresearch.com/ai-agents-market) | ~$11.55B | 43.57% (2026–35) | ~$294.66B by 2035 |
| [Roots Analysis](https://www.rootsanalysis.com/ai-agents-market) | — | 34.64% (→2035) | — |

**Honest read:** the *level* is uncertain (methodologies differ ~25%), but the
*direction* — a multi-tens-of-billions market growing 35–50%/yr — is consistent across
independent firms. Good enough to anchor a "large and fast" narrative; don't over-cite
a single point estimate.

## 2. The agent economy / marketplaces (your Phase 3–4 shape)

Every major platform shipped an agent marketplace in the last ~18 months — the
distribution layer you'd plug into, and evidence the "shelf" model is real:

- **Salesforce AgentExchange** (launched **March 2025**): a *trusted* marketplace for
  Agentforce; **200+ partners** at launch; reached **18,500 customers** and **29,000+
  cumulative deals** by Q4 FY2026 — *"fastest-growing organic product in Salesforce
  history"* [[Salesforce](https://www.salesforce.com/news/press-releases/2025/03/04/agentexchange-announcement/)],
  [[CIO](https://www.cio.com/article/3837608/salesforces-agentexchange-targets-ai-agent-adoption-monetization.html)].
  — Note the word *"trusted"*: even Salesforce is groping toward a trust/vetting
  story. You can go deeper (fidelity certification) than a partner-vetting badge.
- **OpenAI GPT Store**: **3M+ custom GPTs**, but monetization is weak — an
  engagement-based share of roughly **$0.03/conversation** (≈33,000 quality
  conversations to earn $1,000/mo) [[Fast.io](https://fast.io/resources/top-ai-agent-marketplaces/)].
  — Lesson: **marketplaces without quality/verification and real economics commodify
  to near-zero**. Your royalty + certification model is the antidote.
- Google, Microsoft, AWS all shipped agent marketplaces; commentators frame this as a
  race to own a *"multi-trillion-dollar digital labor market"*
  [[Fastio](https://fast.io/resources/top-ai-agent-marketplaces/)] (that trillion
  figure is a distribution-channel narrative, not a measured TAM — cite carefully).

## 3. Verified expertise / expert-cloning traction (your supply thesis)

The most on-thesis proof points — startups that clone *specific human expert
judgment* and are being funded and used:

- **Delphi** ("digital minds" — experts turn themselves into chatbots): **$16M Series
  A** led by **Sequoia** (with Anthropic's Anthology Fund, Menlo, others), **June
  2025**; **$19M total**; **4× revenue growth** since Nov 2024
  [[Delphi/Sequoia](https://www.delphi.ai/blog/delphi-raises-16m-series-a-from-sequoia)],
  [[FinSMEs](https://www.finsmes.com/2025/06/delphi-raises-16m-in-series-a-funding.html)],
  [[Fast Company](https://www.fastcompany.com/91356476/delphi-ai-digital-mind)].
- **Cloneable** (shadows human experts in heavy industry — energy/utilities/rail — and
  redeploys their workflows as agents): **$4.6M seed** (Congruent Ventures), **$5.35M
  total**; **100× ARR growth** Feb→end-2025; customers include **American Electric
  Power, Southern California Edison**; claims an 8-hour engineering task → **<2 min**
  [[Crunchbase News](https://news.crunchbase.com/venture/cloneable-cloning-expert-worker-knowledge-ai-infrastructure/)],
  [[BusinessWire](https://www.businesswire.com/news/home/20260423437615/en/)].
- **Personal AI, Kamoto.AI, Miria** — persona/expert clones with subscription and
  IP-licensing / revenue-share models
  [[Entrepreneur](https://www.entrepreneur.com/science-technology/ai-clones-are-no-longer-science-fiction-theyre-real/494683)].

**Critical gap (this is your wedge):** none of these publicly **certify fidelity to
the named expert on held-out cases with an expiring badge.** They sell the clone; they
do not *guarantee and measure* that it faithfully reproduces the person. That is
precisely engine 07. See the Startup Landscape section and
the Certification Moat section.

## 4. AI evaluation / trust tooling (adjacent to your moat)

- **Gartner** projects **60% of software-engineering teams** will adopt AI evaluation
  & observability platforms by **2028**, up from **18% in 2025**
  [[via getmaxim.ai](https://www.getmaxim.ai/articles/top-5-ai-evaluation-platforms-in-2026-2/)].
- Eval platforms (Maxim, Confident AI/DeepEval, Arize, Braintrust, LangSmith, Azure
  AI Foundry) offer **faithfulness / hallucination** metrics and **LLM-as-a-judge**
  — but for *app quality*, **not for "does this clone match a specific person."**
  Known LLM-judge weaknesses (position, verbosity, self-enhancement bias) mean
  **verification is an open, hard problem**
  [[DeepEval](https://deepeval.com/blog/llm-as-a-judge)],
  [[Microsoft](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/evaluating-ai-agents-can-llm%E2%80%91as%E2%80%91a%E2%80%91judge-evaluators-be-trusted/4480110)].

## 5. Router economics (your cost lever)

- **OpenRouter**: **$500M valuation** (April 2025), **$40M raised**, **>$100M
  annualized inference** processed, monetizes at **~5%** of spend
  [[Sacra](https://sacra.com/c/openrouter/)].
- **LiteLLM**: **470k+ downloads**, self-hostable, enterprise users (Netflix,
  Lemonade, RocketMoney) [[Xenoss](https://xenoss.io/blog/openrouter-vs-litellm)].

## 6. Numbers that support the pitch (one-paragraph version)

> The agentic-AI market is ~**$9–12B in 2026** growing **35–50%/yr**
> (multiple firms). Every major platform has
> launched an agent marketplace — Salesforce's reached **18,500 customers in under a
> year** — but marketplaces without verification commodify (GPT Store: ~**$0.03 per
> conversation**). Expert-cloning is being funded fast (**Delphi $16M/Sequoia**,
> **Cloneable 100× ARR**) yet **nobody certifies that a clone faithfully reproduces
> the named human** — even as AI-evaluation adoption is set to triple to **60% of eng
> teams by 2028**. XNOBrain owns that gap: the certification gate for verified expert
> twins.


# Startup Landscape — Who's Nearest, and the Whitespace

> The companies closest to XNOBrain's "verified expert-twin terminal," organized by
> segment, with funding/traction and — the column that matters — **whether they certify
> fidelity to a named human.** Every figure is linked and dated. This is a landscape
> scan, not an endorsement; treat funding figures as reported, not audited.
>
> ⚠ **Reality check up front:** since the first draft, this space has gotten *more*
> crowded and *closer* to the XNOBrain concept (Viven, IgniteTech, Interloom, Twinnin all landed
> in late-2025/2026). The whitespace is still open — but the walls are moving in, which
> raises the urgency of owning the **certification + royalty** layer first (see §9).

## 1. The map: still nobody sits at the full intersection

The XNOBrain vision needs **four** things at once that most startups do only one or two of:
**(A)** a self-hostable agent runtime, **(B)** cloning a *named* expert's judgment,
**(C)** **certified fidelity** to that person (measured, expiring), **(D)** a
cross-org registry with **royalties**, running on the **customer's own data**.

```
                         (A) self-host   (B) clone a    (C) CERTIFY      (D) registry +
                          runtime        named expert   fidelity         royalty
Hermes / XNOBrain          ●●●             ○ (planned)    ○ (the bet)      ○ (planned)
OpenClaw                    ●●●             ○              ○                ○
Viven                       ○               ●●● (employee) ○                ○
IgniteTech MyPersonas       ○               ●●● (employee) ○                ○
Interloom                   ○               ●● (tacit)     ○                ○
Cloneable                   ● (deploys)     ●●● (field)    ○                ○
Delphi                      ○               ●●● (creator)  ○                ● (rev-share)
Coachvox                    ○               ●● (coach)     ○                ● (10% fee)
Synthesia / HeyGen / Tavus  ○               ●● (avatar)    ○                ○
11x / Wonderful / Sierra    ○               ○ (role, not   ○                ○
                                             a person)
Braintrust / Galileo        ○               ○              ●● (app quality, ○
                                                            not fidelity-to-person)
Salesforce AgentExchange    ○               ○              ○ (partner-vet)  ●● (marketplace)
```

**● strong · ○ weak/none.** The row with all four still doesn't exist. Several now have
three of the four *minus C* — which is exactly why C (fidelity certification) is the
lever.

---

## 2. Segment A — Enterprise "employee/expert digital twins" (the closest, and newest)

These clone a *specific named employee/expert's* knowledge for the enterprise. This is
the segment that moved most since the first draft.

- **Viven** — **$35M seed** (Khosla Ventures, Foundation Capital, FPV, Operator
  Collective), out of stealth **Oct 2025**. Builds "AI digital twins" — *personalized
  digital counterparts for employees, capturing their knowledge, decisions, and
  context* for the enterprise
  [[PRNewswire](https://www.prnewswire.com/news-releases/viven-emerges-from-stealth-with-35m-in-funding-to-bring-ai-digital-twins-to-the-enterprise-302585135.html)],
  [[StartupHub](https://www.startuphub.ai/ai-news/funding-round/2025/viven-raises-35m-to-advance-ai-digital-twin-technology)].
  → **The single closest funded company to your "twin" primitive.** But it's a hosted
  enterprise product; no public fidelity-certification badge, no cross-org royalty
  registry, no run-on-customer-runtime story. Your gate + royalty + self-host is the
  differentiation.
- **IgniteTech — MyPersonas** — demoed **CES 2026**: AI replicas of employees from
  *video, voice, and written materials*; the stand-in answers questions, chats on
  video, responds in **160 languages**
  [[Euronews](https://www.euronews.com/next/2026/01/07/ai-software-that-can-create-digital-clones-of-employees-unveiled-at-ces-2026)].
  → Employee-replica UX; again, no fidelity measurement/expiry.
- **Interloom** — **$16.5M** (DN Capital, Bek, Air Street; after a $3M seed Mar 2024).
  Captures a company's **tacit knowledge** to power AI agents that understand its
  workflows
  [[Yahoo/Fortune](https://finance.yahoo.com/sectors/technology/articles/exclusive-interloom-startup-capturing-tacit-070000121.html)].
  → Same *"codify tacit knowledge → agents"* thesis; workflow-centric, not
  named-person-fidelity-certified.
- **Cloneable** — **$4.6M seed** (Congruent), **$5.35M total**; shadows field experts
  in energy/utilities/rail, redeploys workflows as agents; **100× ARR** Feb→end-2025;
  customers AEP, SCE
  [[Crunchbase News](https://news.crunchbase.com/venture/cloneable-cloning-expert-worker-knowledge-ai-infrastructure/)].
  → Vertical-services-heavy (they do the shadowing); you can be the horizontal
  *certification* rail they'd list on.
- **Tribal AI** — **$10M seed**; "metadata-native" agents via a *Metadata Fabric* that
  maps an org's enterprise-system metadata
  [[SiliconANGLE](https://siliconangle.com/2026/05/20/tribal-ai-lands-10m-seed-funding-bring-metadata-native-agents-enterprise/)].
  → Adjacent (data/metadata, not person-clone), but same "capture what's in people's
  heads" narrative.
- **Twinnin** — a *"controversial"* AI platform opening a first raise **targeting ~$3M**,
  explicitly **signing up "twins"**
  [[Deadline](https://deadline.com/2026/05/ai-plaform-twinnin-funding-round-3-million-signs-up-twins-1236882734/)].
  → Closest in *language* (a marketplace of "twins"); watch it — it may be an early
  attempt at the same category, controversy and all.

> **Market signal:** among digital-twin startups whose last round closed in 2025/26,
> the **average round is ~$56M — roughly 2× the ~$27M** average for pre-2024 rounds
> [[New Market Pitch](https://newmarketpitch.com/blogs/news/digital-twin-top-startups-fundraising)].
> Capital is accelerating into exactly this segment.

## 3. Segment B — Creator/expert self-clones + monetization (the royalty precedent)

Consumer/creator platforms where an expert clones *themselves* and sells access. Their
**revenue-share numbers are the clearest precedent for your royalty engine.**

- **Delphi** — **$16M Series A / Sequoia** (Jun 2025), **$19M total**, **4× revenue**
  since Nov 2024; a "digital mind" deployed across chat/SMS/WhatsApp/Slack/**voice**;
  last documented take rate **~20% of subscription revenue**
  [[Delphi/Sequoia](https://www.delphi.ai/blog/delphi-raises-16m-series-a-from-sequoia)],
  [[Personify comparison](https://personify.fyi/blog/delphi-vs-coachvox/)].
- **Coachvox** — clones coaches/consultants/speakers to scale beyond 1:1; take rate
  **10% per transaction + Stripe fees**; lead-gen oriented
  [[Personify comparison](https://personify.fyi/blog/delphi-vs-coachvox/)].
- **BuddyPro**, **Personify** — comparable "AI expert / coaching clone" platforms
  [[BuddyPro](https://buddypro.ai/blog/buddypro-vs-coachvox-vs-delphi-alternatives)].
- **Personal AI**, **Kamoto.AI**, **Miria** — persona/expert clones; subscription +
  IP-licensing / revenue-share models; Miria (founded Oct 2025) targets
  executives/experts/creators with ~20 profiles
  [[Entrepreneur](https://www.entrepreneur.com/science-technology/ai-clones-are-no-longer-science-fiction-theyre-real/494683)].

**Read:** the market already prices **platform-vs-expert splits (10–20%)** — your
royalty mechanic isn't novel *economically*; what's novel is attaching it to a
**certified, cross-org, run-on-customer-data** twin instead of a hosted creator chatbot.

## 4. Segment C — Interactive avatar / persona infrastructure (the "face + voice")

Not judgment-cloning, but the *embodiment* layer experts already pay for — potential
partners or a front-end for twins.

- **Synthesia** — **$500M+ raised**, **$200M Series E at $4B valuation** (late 2025),
  **$146M ARR**, 70% of Fortune 100 [[Sacra](https://sacra.com/c/heygen/)].
- **HeyGen** — **$60M Series A** (Jun 2024), **~$95–100M ARR** by late 2025
  [[Sacra](https://sacra.com/c/heygen/)].
- **Tavus** — **$40M Series B / CRV** (Nov 2025), **~$64M total**; real-time
  conversational video avatars (PALs) [[Sacra](https://sacra.com/c/tavus/)].
- **D-ID** — 280K developers.

**Read:** avatars are commodity *embodiment*, not certified judgment. Relevant only as a
**delivery skin** for a twin — don't confuse a talking-head clone with a fidelity-
certified expert twin.

## 5. Segment D — Vertical AI agents / "AI employees" (clone a *role*, not a person)

The best-funded slice of agentic AI — but they replicate a *job function*, not a *named
individual*, and don't certify fidelity to anyone.

- **Vertical AI agents = 55.7% of disclosed agentic-AI capital — $2.64B across 30
  deals**; Legal/Insurance/Construction/Healthcare = 72.6% of that
  [[New Market Pitch](https://newmarketpitch.com/blogs/news/vertical-ai-funding-analysis)].
- **Wonderful** — **$250M across two rounds in 4 months**, **$2B valuation**;
  multilingual customer-service agents
  [[funding tracker](https://aifundingtracker.com/top-ai-agent-startups/)].
- **11x.ai** — **$24M Series A / Benchmark**; "automated digital workers" (SDRs)
  [[TechCrunch](https://techcrunch.com/2024/09/16/ai-digital-employee-startup-11xai-raises-24m-led-by-benchmark)].
- (Plus Sierra, Cognition, Decagon, etc. — role-replacement agents.)

**Read:** these prove enterprises *buy* "AI that does a job." Your edge: you don't sell
a generic role-agent — you sell a *certified copy of a specific trusted expert*, which
commands scarcity pricing a commodity role-agent can't.

## 6. Segment E — Agent marketplaces (the distribution/shelf layer)

- **Salesforce AgentExchange** (Mar 2025): "trusted" marketplace; **18,500 customers,
  29,000+ deals** by Q4 FY2026 — fastest-growing product in Salesforce history
  [[Salesforce](https://www.salesforce.com/news/press-releases/2025/03/04/agentexchange-announcement/)].
  Its "trust" = **partner vetting**, not measured fidelity.
- **AWS Marketplace / Google / Microsoft** agent stores; **GPT Store** (3M+ GPTs,
  ~$0.03/conversation economics) [[Fastio](https://fast.io/resources/top-ai-agent-marketplaces/)].
- Broader "AI agent" market cited at **$5.25B (2024) → $7.84B (2025) → ~$52.6B by
  2030**, **10,000+ custom agents published weekly**
  [[Fastio](https://fast.io/resources/top-ai-agent-marketplaces/)] (broad "AI agent"
  framing — larger and looser than the "agentic AI" figures in
  the Market section; cite the range, not one point).

**Read:** marketplaces exist and scale, but all vet *partners/security*, none certify
*fidelity to a human*. Your registry's differentiator is the **badge + expiry +
royalty**, not another listing surface.

## 7. Segment F — Eval / trust / observability (the certification-adjacent tooling)

The people building the *machinery* nearest your gate — but for **app quality, not
fidelity-to-a-person.**

- **Braintrust** — **$80M Series B / Iconiq** (a16z, Greylock, Elad Gil), **$800M
  valuation**; observability + eval, hallucination/drift/regression, LLM-as-judge
  scorers [[SiliconANGLE](https://siliconangle.com/2026/02/17/braintrust-lands-80m-series-b-funding-round-become-observability-layer-ai/)].
- **Galileo AI** — eval/observability with **Luna-2** proprietary small eval models for
  low-latency scoring; research-backed faithfulness/hallucination metrics
  [[parsers.vc](https://parsers.vc/startup/galileo.ai/)] *(funding figures inconsistent
  across sources — treat as unverified)*.
- (Plus Arize, Confident AI/DeepEval, Maxim, LangSmith.) Gartner: eval-platform adoption
  **18% (2025) → 60% of eng teams (2028)** (04).

**Read:** these measure *"is the output correct/faithful to ground truth"* — **not "does
this clone reproduce Dr. X's judgment."** Their drift/scoring tech is exactly what your
certification engine needs; **partner-or-build-on** rather than compete. And note the
known LLM-judge biases (the Certification Moat section) mean the
*fidelity-to-a-person* metric is still unbuilt R&D — your moat.

---

## 8. The one comparison that matters

| Company | Self-host | Clone a **named** person | **Certify fidelity** (measured, expiring) | Royalty / rev-share | Runs on customer's data |
|---|:--:|:--:|:--:|:--:|:--:|
| **XNOBrain (goal)** | ✔ | ✔ | ✔ **(the moat)** | ✔ | ✔ |
| Viven | ✘ | ✔ | ✘ | ✘ | ◐ |
| IgniteTech MyPersonas | ✘ | ✔ | ✘ | ✘ | ✘ |
| Cloneable | ◐ | ✔ | ✘ | ✘ | ✔ |
| Interloom | ✘ | ◐ (tacit) | ✘ | ✘ | ✔ |
| Delphi | ✘ | ✔ | ✘ | ✔ (~20%) | ✘ |
| Coachvox | ✘ | ✔ | ✘ | ✔ (10%) | ✘ |
| Synthesia/HeyGen/Tavus | ✘ | ◐ (avatar) | ✘ | ✘ | ✘ |
| 11x / Wonderful / Sierra | ✘ | ✘ (role) | ✘ | ✘ | ◐ |
| Braintrust / Galileo | ◐ | ✘ | ◐ (app quality) | ✘ | ✔ |
| Salesforce AgentExchange | ✘ | ✘ | ◐ (partner-vet) | ◐ | ✔ |

**The "certify fidelity" column is empty for everyone.** That is the whole thesis in one
column.

## 9. The whitespace, re-stated (sharper, post-Viven)

> **No company measures whether an AI twin faithfully reproduces a *specific named
> expert's* judgment on held-out cases, issues an *expiring* trust badge, monitors
> drift, and settles *royalties* across organizations while the twin runs on the
> *customer's own data/runtime*.**

What changed since draft 1: **Viven, IgniteTech, Interloom, Twinnin** now occupy the
"clone a named person for the enterprise" square that used to be sparse. So "we clone
experts" is **no longer differentiating** — several funded teams say that. Your remaining
defensible ground narrows to the **hard, unbuilt parts**:

1. **Certification/fidelity measurement** (engine 07) — still nobody. The eval crowd
   measures app quality; the twin crowd measures nothing.
2. **Expiring badges + drift → re-certification** — a recurring-revenue mechanic none of
   them have.
3. **Cross-org royalty + lineage** on a certified twin (Delphi/Coachvox have rev-share,
   but only on hosted single-creator chatbots, not a certified cross-org registry).
4. **Runs on the customer's own runtime/data** (self-host) — the twin crowd is hosted;
   this is your + Hermes's structural advantage.

**Implication for strategy:** lead with **certification**, not "twins." If the pitch is
"we make expert twins," you're now one of a dozen. If it's "we're the *fidelity
certification + royalty rail* for expert twins — including the ones Viven/Cloneable/
Delphi make," you're a category of one. (Cf. the Certification Moat section.)

## 10. What to steal, and the risks

**Steal:**
- **Delphi/Coachvox rev-share numbers (10–20%)** — a validated pricing anchor for
  royalties.
- **Cloneable's retiring-expert wedge** — captures craft that's *about to walk out*.
- **Braintrust/Galileo's drift/scoring machinery** — build your fidelity scorer on top,
  don't reinvent eval plumbing.
- **Viven's enterprise-twin framing** — proves the enterprise buyer exists at $35M-seed
  conviction.

**Risks (sharper now):**
1. **The clone square is filling fast.** Viven ($35M) and the digital-twin avg round
   (~$56M) mean well-capitalized teams could bolt on a "trust score" before you ship a
   real certification standard. **Speed on engine 07 is the whole game.**
2. **A "good-enough" fake certification** (e.g. an LLM-judge "fidelity %") could
   commoditize the badge if you don't make yours defensible (blind expert re-rating +
   objective agreement, not just an LLM score — 06).
3. **Marketplace commodification** (GPT Store → $0.03/conv) — compete on *scarcity +
   verification + royalty*, never volume of listings.
4. **Supply consent** — mitigated by the retiring-expert angle (below).

## 11. The supply-side tailwind (use this in the pitch)

The "capture craft before it walks out the door" thesis has a hard demographic number:
**~10,000 Baby Boomers retire per day in the US**, making institutional-knowledge
preservation urgent — the explicit tailwind investors are backing across Interloom,
Tribal AI, Cloneable, and Viven
[[Yahoo/Fortune on Interloom](https://finance.yahoo.com/sectors/technology/articles/exclusive-interloom-startup-capturing-tacit-070000121.html)].
This directly answers the vision's "supply wall" (the Vision section §5):
retiring experts consent readily when the alternative is their expertise disappearing.


# Current State — What XNOBrain Is Today

> Scope: sourced from the repository itself (`AGENTS.md`, `README.md`,
> `docs/architecture.md`, `docs/enterprise-extension.md`,
> `docs/repository-ownership.md`, `docs/project-summary.md`). No external claims here —
> this is the ground truth of the code as it stands on 2026-07-24.

## 1. One-sentence definition

XNOBrain is a **self-hosted React + FastAPI workspace that wraps Nous Research's
open-source Hermes Agent** and adds a multi-provider LLM router (referred to
internally as **9router**), packaged as a downloadable product with an optional
proprietary enterprise control plane.

It is, today, an **agent workspace / "studio"** — not yet a marketplace, not yet a
certification terminal. The Twin Terminal vision (see
the Vision section) is the destination; this
file is the starting point.

## 2. Runtime architecture (as built)

```
browser → Traefik → React UI
                 → FastAPI :8642  (Hermes native routes + XNOBrain routes)
                        → services → repositories → atomic profile/config files
                        → integrations → Hermes CLI/core
                        → integrations → 9router :20128 → LLM providers
```

Key facts pulled from `docs/architecture.md`:

- The runtime container starts **exactly two processes**: FastAPI on `8642` and
  9router on `20128`. React is served through Traefik.
- It is a **Python modular monolith** layered onto the original Hermes CLI FastAPI
  app. Route assembly is centralized in `xnobrain/routes/setup.py`.
- **No application database.** State is atomic files under `DATA_DIR`. Hermes keeps
  its own profile-local `state.db` for native session history (an upstream file, not
  a XNOBrain schema).
- Clean layering: **handlers** own HTTP/SSE translation, **services** own rules,
  **repositories** own atomic files, **integrations** adapt the Hermes CLI and
  9router, **models** are Pydantic.
- **No managed control-plane dependency, login, or API proxy** in the OSS build.
  OpenTelemetry is local-only and off by default.

### Code map (`xnobrain/`)

| Layer | Files | Responsibility |
|---|---|---|
| `handlers/` | `api.py` | HTTP + SSE translation |
| `services/` | `kanban.py`, `platform.py` | Business rules |
| `repositories/` | `files.py` | Atomic file persistence |
| `integrations/` | `hermes.py`, `nine_router.py`, `runtime.py`, `kanban.py`, `config.py` | Adapters for Hermes CLI + 9router |
| `models/` | `api.py` | Pydantic contracts (drive `/docs`, `/openapi.json`) |
| `routes/` | `setup.py` | Single route-assembly point |

## 3. What the product does today (feature boundary)

From `docs/project-summary.md` and `README.md`, the **local/OSS edition is
unlimited** and provides:

- Unlimited local agents, named profiles, prompts/config
- Skills (`skills/<skill-id>/SKILL.md`), memory, MCP servers
- Conversations + SSE streaming runs, approval gate (Hermes' approval core)
- Local cron, providers, teams, workspace files
- Immutable snapshots + portable `.zip` profile bundles
- A React UI including a **Kanban board** for task scheduling (recent commits show
  DB-backed Kanban scheduling and recurring-task generation, an event-stream API,
  and a task editor with skills management)

**Persistence & safety rules** (`AGENTS.md`, `README.md`):

- Agent data lives under `DATA_DIR/profiles/<agent-id>/`.
- Skills only under `.../skills/<skill-id>/SKILL.md`.
- Every persistence-promising mutation writes an **immutable snapshot** first, then
  mutable state via temp-file → fsync → rename.
- Credentials/prompts/tool args are **never** logged, traced, or included in
  portable bundles or telemetry.

## 4. The open-core split (already designed, partly built)

This is the most strategically important thing already in the repo — the
**two-repository model** is specified in `docs/enterprise-extension.md` and
`docs/repository-ownership.md`.

| Repo | Builds | Owns |
|---|---|---|
| `xnobrain` (this, **public/OSS**) | React image + combined FastAPI/Hermes/9router image | Profile isolation, safe paths, snapshots, conversations, Hermes invocation, 9router delegation, quota middleware, service-level enforcement |
| `xnobrain-enterprise` (**private**) | Enterprise API + managed/Incus cloud packaging | Auth, tenant/plan resolution, billing entitlements, distributed quota, RBAC, audit, secret management, managed orchestration, telemetry retention |

Rules that protect the model:

- The OSS repo must stay a **complete downloadable product** — its Compose file
  pulls only public images and **never** requires a private image or cloud account.
- **Dependency is one-directional**: enterprise may pull the public runtime image;
  the OSS deployment never pulls the enterprise image.
- Enterprise is a **thin private composition**, not a fork. It pins a released
  XNOBrain module + runtime image and implements a `pkg/edition.Policy` contract
  (`-1` = unlimited).
- Editions: **self-hosted Free** (file-only), **Cloud Free / Personal Pro /
  Enterprise** (shared private PostgreSQL control plane). Enterprise adds orgs, RBAC,
  SSO, audit, contract entitlements.
- **Historical note:** an earlier Go/Fiber + PostgreSQL multi-server plan was
  **retired** (`docs/implementation/README.md`); current OSS work targets the single
  Python package. The Go story now lives on the **enterprise control-plane** side.

### ⚠ Language reality-check vs. the stated plan

The stated intention is to open-source part of the project (described as "Python + Go")
and keep an enterprise edition in Go. The repository's current reality is:

- **OSS = Python** (FastAPI monolith) + React/TypeScript frontend. The Go/Postgres
  path was explicitly retired in the OSS repo.
- **Enterprise = Go** (the `pkg/edition.Policy` control plane, `github.com/vn-fin/xnobrain/`
  module path, managed orchestration).

So "Python + Go open source" is **not** what the code does today. Decide
deliberately (see the roadmap file): either (a) keep OSS Python-only and Go strictly
enterprise, or (b) reintroduce a Go component into OSS (e.g. a lightweight local
daemon/CLI) if you truly want a Go OSS surface. The cleanest story is **(a)**.

## 5. Honest assessment of the gap to the vision

| Vision needs (Twin Terminal) | Exists today? |
|---|---|
| Agent runtime, memory, skills, MCP, sandboxes | ✔ via Hermes |
| Multi-provider routing | ✔ via 9router |
| Self-host + cloud + protected enterprise source | ✔ designed (open-core split) |
| Skill/twin **authoring workshop** | ◐ partial (skills + Kanban editor) |
| **Fidelity-certification gate** (the moat) | ✘ not started |
| Identity / anti-impersonation | ✘ not started |
| Registry / shelf with expiring badges | ✘ not started |
| Lineage, royalty, cross-org billing | ✘ enterprise billing exists; royalty/lineage do not |
| Drift monitoring → re-certification | ✘ not started |

**Takeaway:** the "commodity" engines (brain, execution, connection, and most of
orchestration) are largely **rented or already built**. Every engine the vision
marks as *the moat* — **certification, identity, improvement loop, commerce/royalty**
— is greenfield. That is the correct place to spend, and it is where the roadmap
should concentrate.


# Core Tech & Direct Competitors

> What you're building on (Hermes + a router), what it can and can't do, its license
> implications, and the open-source runtimes you'll be compared to. All external
> claims are linked and dated.

## 1. Hermes Agent (Nous Research) — your engine

**What it is.** An open-source, **MIT-licensed** self-improving AI agent, launched
**February 2026**. It is the exact thing XNOBrain wraps (the repo's "original Hermes
CLI dashboard application").

**Capabilities** (from the official
[user-stories page](https://hermes-agent.nousresearch.com/docs/user-stories) and
independent write-ups
[[awesomeagents.ai](https://awesomeagents.ai/news/nous-research-hermes-agent-open-source-memory/)],
[[aiengineerinsights.com](https://aiengineerinsights.com/blog/hermes-agent-nous-research-guide/)]):

- **Self-improving skills** — writes reusable `SKILL.md` files from solved problems;
  "the same task is faster and cheaper the second time."
- **3-layer persistent memory** — durable facts (`MEMORY.md`), session search, and
  procedural skills, with SQLite **FTS5** full-text search.
- **Five-stage learning loop** — execute → evaluate → extract reusable patterns as
  named skills → refine → retrieve for new tasks.
- **Tools** — code execution, file manipulation, web browsing/research, **MCP**
  (Model Context Protocol) integration.
- **Five sandbox backends** — local, Docker, SSH, Singularity, Modal.
- **Multi-platform messaging** — Telegram, Discord, Slack, WhatsApp, Signal, Email,
  CLI, and many more via adapters.
- **Natural-language cron** ("every weekday at 9am, summarize my inbox").
- **Approval gate** ("converse mode" requiring human sign-off before tool use) — this
  is the Hermes approval core XNOBrain preserves.

**Maturity.** By v0.18.2 (July 2026) the project reported hundreds of closed issues,
**370+ contributors, and zero open P0 defects**
[[awesomeagents.ai](https://awesomeagents.ai/news/nous-research-hermes-agent-open-source-memory/)].
Production-grade enough to build on; still fast-moving (0.x versioning).

**Ecosystem you inherit for free:**

- `agentskills.io` — a community **skill standard**; `awesome-hermes-agent` curates
  skills against it.
- **Hermes Atlas** (`hermesatlas.com`) — a scraped ecosystem map with star ratings.
- **Hermify** — a **paid managed-hosting** service ("bring API key + Telegram bot").
  Note: Nous already monetizes hosting — so "hosted Hermes" is a competitive lane,
  not open water. Your differentiation is the **certification/registry**, not hosting.

### MIT license implications (important, and favorable)

MIT is **maximally permissive**: you may use, modify, and distribute Hermes —
**including in a closed commercial product** — with essentially one obligation:
preserve the copyright + license notice
[[license overview](https://dev.to/juanisidoro/open-source-licenses-which-one-should-you-pick-mit-gpl-apache-agpl-and-more-2026-guide-p90)].
Consequences for XNOBrain:

- ✔ You can legally build a **proprietary enterprise/cloud layer** on top of Hermes
  and keep that layer closed. This is exactly what the open-core split assumes.
- ✔ You are **not** forced to open-source your own code (unlike AGPL/GPL).
- ⚠ The flip side: MIT gives *you* no protection either — anyone (including a cloud
  hyperscaler or Nous themselves) can also wrap Hermes. **Your moat cannot be "we
  wrapped Hermes."** It must be the certification gate, the craft data, identity, and
  royalty lineage (engines 05–07, 09). This is consistent with the vision doc.
- • **Action:** keep a clean attribution/NOTICE file for Hermes and the router, and
  a dependency-license inventory, before public distribution. Your own OSS repo still
  needs a license chosen — the README literally says *"Choose and add a license
  before public distribution."* See the Business Model section.

## 2. The router ("9router") and the routing landscape

XNOBrain's "9router" is an LLM router (one process on `:20128`) that routes to
multiple providers — the same job as **OpenRouter** (hosted) or **LiteLLM**
(self-hostable proxy). Whether "9router" is a distinct project or your alias, the
landscape it sits in:

- **OpenRouter** — hosted aggregator; monetizes by taking **~5% on top of inference
  spend**; **$500M valuation** after a **$28M Series A** (Menlo Ventures, April 2025),
  **$40M total** raised; processed **>$100M annualized inference** by May 2025 (up
  from ~$19M end-2024) [[Sacra](https://sacra.com/c/openrouter/)].
- **LiteLLM** — open-source, **self-hostable** proxy; **470k+ downloads**; production
  users include Netflix, Lemonade, RocketMoney
  [[xenoss.io](https://xenoss.io/blog/openrouter-vs-litellm)].

**Strategic read:** routing is a **commodity engine (01/04 in the vision)** — do not
try to out-build OpenRouter. Use a self-hostable router (LiteLLM-class) so the
enterprise edition can run **fully on-prem with the customer's own provider keys** —
that's a real enterprise requirement (data residency, key custody) and a reason firms
pick you over a hosted-only stack. The "smart routing tiers" pattern (cheap model for
mechanical work, premium for ambiguous) is already common in the Hermes ecosystem and
is a nice cost-story, not a moat.

## 3. OpenClaw — the closest philosophical competitor

**What it is.** An open-source, self-hosted **gateway connecting chat apps to AI
coding/agents**; **config-first** (write a `SOUL.md`, run one command, agent is live —
"no Python, no chains, no graphs"); **160,000+ GitHub stars**; persistent memory via
markdown + SQLite; multi-agent routing through one gateway
[[Milvus guide](https://milvus.io/blog/openclaw-formerly-clawdbot-moltbot-explained-a-complete-guide-to-the-autonomous-ai-agent.md)],
[[SFAI Labs](https://sfailabs.com/guides/openclaw-ai-agent-framework)],
[[freeCodeCamp](https://www.freecodecamp.org/news/how-to-build-and-secure-a-personal-ai-agent-with-openclaw/)].

**How it overlaps and differs from Hermes/XNOBrain:**

| | Hermes / XNOBrain | OpenClaw |
|---|---|---|
| Config model | Skills + profiles + UI | `SOUL.md` config-first |
| Memory | MEMORY.md + FTS5 + skills | markdown + SQLite |
| Distribution | messaging + web workspace | messaging gateway |
| Self-improvement | ✔ writes its own skills | limited |
| **Certification / twin fidelity** | your planned moat | ✘ none |

**Read:** OpenClaw proves there is *massive* demand for **self-hosted personal
agents** (160k stars is enormous). But like Hermes, it competes on **runtime and
convenience**, not on **verified expert twins**. Neither OpenClaw nor Hermes does
fidelity certification. That whitespace is your thesis — and it is still open.

## 4. The broader open-source agent-runtime field (who you're benchmarked against)

Buyers evaluating "build AI agents" platforms will name these. Position yourself as
*"the verified-expert-twin layer,"* not *"another agent framework."*

| Project | Shape | Monetization |
|---|---|---|
| **LangGraph / LangChain** | dev framework/orchestration | LangSmith (obs/eval) SaaS + enterprise |
| **CrewAI**, **AutoGen** | multi-agent frameworks | enterprise/cloud tiers |
| **OpenHands** | autonomous coding agent | cloud + enterprise |
| **Dify**, **Flowise**, **n8n** | low-code agent/workflow builders | **open-core** (n8n uses fair-code "internal use free, resale prohibited") |
| **Cline** | IDE coding agent | mostly BYO-key |

Two takeaways for strategy:

1. **The common commercial pattern is open-core** (free self-hosted core + paid
   cloud/enterprise) — exactly the intended plan. See
   the Business Model section.
2. **Everyone competes on "build agents." Nobody competes on "certify that this
   agent faithfully reproduces a specific named human expert."** That is the sentence
   that differentiates XNOBrain from this entire list.


# The Certification Moat — Making Engine 07 Real

> The vision names **Trust / Certification** as the one thing nobody does and the core
> of the terminal. A vision is not a moat until the metric exists. This file turns the
> promise into a concrete, buildable methodology proposal. It is analysis/opinion
> (clearly marked), grounded in the vision doc + the eval landscape in
> the Market section — not an external claim.

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

The eval landscape (04) shows LLM judges have **position,
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


# Business Model & Licensing

> The plan — open-source a personal edition, sell a proprietary enterprise/cloud
> edition firms can self-host or run in cloud with protected source — is a proven
> **open-core** pattern. This file benchmarks precedents, then gives concrete license
> recommendations. External claims linked; recommendations are marked as opinion.

## 1. Open-core precedents (the pattern is well-trodden)

The open-core model = **open-source core (MIT/Apache) + proprietary enterprise
features via paid subscription**
[[open-core overview](https://viprasol.com/blog/open-source-business-model/)]. Your
`xnobrain` (public) + `xnobrain-enterprise` (private) split is textbook.

| Company | Core license | Commercial model | Lesson for you |
|---|---|---|---|
| **GitLab** | started MIT, proprietary Enterprise Edition | open-core; free CE, paid EE for security/compliance/DevOps | the canonical open-core success; feature-tiering works |
| **HashiCorp** | moved MPL → **BSL/BUSL** (2023) | source-available; blocks competitors from reselling as managed service | [[InfoQ](https://www.infoq.com/news/2023/08/hashicorp-adopts-bsl/)] — BSL stops hyperscalers, but forked **OpenTofu** — community backlash is real |
| **Elastic** | Apache → **SSPL** → back to AGPL | source-available then re-opened | over-restricting can cost community goodwill |
| **n8n** | **fair-code** ("internal use free, resale prohibited") | open-core + cloud | closest to your shape; lets firms self-host free, blocks resale |
| **Sourcegraph** | went closed-source | enterprise sales | pure-enterprise is viable but loses the OSS funnel |

**Pattern that fits XNOBrain best:** **permissive/fair-code core + closed enterprise
control plane** (like n8n / GitLab), *not* a restrictive relicense of the core (you
don't own Hermes' license anyway — it's MIT and stays MIT).

## 2. The two licensing decisions you actually face

### Decision A — the license for *your own* OSS repo (`xnobrain`)
The README says it outright: *"Choose and add a license before public distribution."*
This is currently **unset** and blocks public launch. Options (opinion):

| Option | Effect | When to pick |
|---|---|---|
| **MIT / Apache-2.0** | maximally open; anyone (incl. competitors) can use/host it | if adoption/funnel > protection; matches Hermes' MIT; simplest |
| **AGPL-3.0** | copyleft; anyone offering it as a *network service* must open their modifications | deters a hyperscaler from running a closed hosted fork, while still "open source" |
| **BSL / fair-code (n8n-style)** | source-available; free to self-host, **resale/managed-service prohibited** for N years then converts | best *commercial* protection; **but not OSI "open source"** — manage messaging |

**Recommendation (opinion):** **Apache-2.0 or AGPL-3.0 for the OSS core**, and keep
**all the moat (certification, registry, identity, royalty, billing) in the closed
`xnobrain-enterprise` repo.** Rationale:

- The moat is **engines 05–07 & 09**, which are *already* private by design — so you
  don't need a restrictive core license to protect the valuable part. That lets the
  core stay genuinely open (better adoption, community, trust), while the money lives
  behind the enterprise boundary.
- **AGPL** adds a mild deterrent against a closed hosted clone of the core without
  scaring self-hosting firms (they use it internally, not redistribute).
- Avoid BSL/SSPL unless you specifically fear a hyperscaler reselling the *core* —
  and note HashiCorp/Elastic show the community cost.
- ⚠ **License compatibility:** if you pick AGPL for the core, confirm compatibility
  with Hermes (MIT — fine, MIT is AGPL-compatible) and every bundled dependency, and
  keep an attribution/NOTICE inventory.

### Decision B — the *personal (Python + Go)* vs *enterprise (Go)* framing
The stated intention describes open-source as "Python + Go" and enterprise as "Go." The
**repo today** is: **OSS = Python + React; Go = enterprise only** (the old Go/Postgres OSS path was
retired — see the Current State section §4). Reconcile deliberately:

- **Recommended (opinion): keep OSS Python + React; Go stays enterprise.** Simplest,
  matches the code, one clean boundary. Drop "Python + Go OSS" from the pitch unless
  you have a specific reason.
- **Alternative:** if you genuinely want a Go surface in OSS (e.g. a small local
  daemon/agent/CLI for edge/self-host installs), scope it tightly and treat it as a
  *new* OSS component — don't resurrect the retired Fiber/Postgres server.

## 3. Deployment & revenue model (matches your intent)

Your intent — *install on a firm's server, or deploy in cloud, source code
protected* — maps cleanly onto the existing edition contract
(`docs/enterprise-extension.md`):

| Edition | Where it runs | Data plane | Control plane | Revenue |
|---|---|---|---|---|
| **Self-hosted Free (OSS)** | firm's server / laptop | atomic files | none | $0 (funnel) |
| **Enterprise self-hosted** | firm's server | files + local enforcement | **private Go control plane** (RBAC, SSO, audit, quotas, billing) — source protected | license/subscription |
| **Cloud (Free / Pro / Enterprise)** | your cloud (Incus/managed) | shared private PostgreSQL | managed | seat + usage |

Twin-Terminal-era additions (Phase 3+): **per-installed-twin fees + royalty
settlement + certification fees** (the vision's "seat fee + per-twin fee + royalty").
The billing/entitlement plumbing already lives in enterprise; **lineage & royalty
(engine 09) is the net-new build.**

**Source-code protection when self-hosted on a firm's server:** the honest constraint
— software delivered to a customer's machine can be inspected. Real protection comes
from: (1) keeping the control plane/moat as a **service the firm calls**
(`ENTERPRISE_API_URL`) rather than code they run, where feasible; (2) license terms +
BSL-style resale prohibition; (3) shipping compiled Go binaries, not source. Don't
promise "unbreakable" source protection for on-prem — promise *licensed, supported,
and legally protected*.

## 4. Concrete next actions on this file's topic

1. **Pick and commit the OSS core license** (recommend Apache-2.0 or AGPL-3.0) — this
   is a launch blocker per the README.
2. **Add NOTICE / third-party-license inventory** for Hermes (MIT) + router + deps.
3. **Decide the Python-only-vs-Python+Go OSS question** and update the README/pitch so
   the story matches the code.
4. **Draft enterprise EULA + resale/BSL-style terms** for the enterprise binaries.
5. **Draft craft-ownership + fidelity-liability contract templates** (the vision's two
   red boxes — also gating for the Certification Moat section).


# Integration Architecture & Delivery Plan

This section documents how the current system integrates the Hermes runtime and the LLM
router, the performance characteristics of that integration, how profiles (per-agent
isolation) work, and the recommended target architecture for the commercial product.

## Current integration: one process extending Hermes

The open-source application does not run a separate server that calls Hermes over the
network. It imports Hermes's own web-server application and registers its compatibility
routes onto it, then serves the combined application on port 8642. In effect, the running
process is Hermes's web server plus the XNOBrain management surface, in a single Python
process sharing one virtual environment. The LLM router runs as a separate process on port
20128 and is reached over HTTP.

Because XNOBrain shares the process and environment, it integrates with Hermes through
several channels simultaneously rather than a single clean boundary.

| Backing mechanism | How it works | Which functions use it |
|---|---|---|
| CLI subprocess | Fork the `hermes` binary, read stdout | The agent chat/run (streaming and non-streaming); installing a skill from a remote source |
| Direct filesystem | Read and write profile files | Agent create/list/get/delete; configuration (global and per-agent); local skill install/enable/disable; memory; workspace; snapshots; profile registry; portable bundles |
| Direct SQLite | Read and write each profile's `state.db` | Conversation and message listing and usage; session create/rename/delete (with an in-process helper first) |
| In-process import | Call Hermes Python modules directly | Session bookkeeping (`hermes_state`), approvals (`tools.approval`), Kanban (`hermes_cli.kanban_db`), profile listing (`hermes_cli.profiles`) |
| HTTP to the router | JSON over localhost | All provider functions: connect, OAuth, test, disconnect, models, usage, combos |

Two observations follow. First, only two operations actually invoke the Hermes *agent*:
chat and remote skill-install; everything else is plumbing around Hermes's data. Second —
and importantly — the chat path spawns a fresh `hermes` process for every turn, even though
the warm Hermes runtime is already loaded in the same process. That per-turn spawn is the
main avoidable cost in the current design.

## Invocation modes and their performance

There are three ways to drive the runtime, in increasing order of coupling.

**CLI subprocess per call** (the current chat path). Each turn forks the `hermes` binary,
paying the full cost of interpreter startup, module import, and profile/skill/router
initialization before any model work begins. Measured on the reference machine, even a
trivial `hermes --version` costs about 0.26–0.28 seconds; a real chat turn that also loads
configuration, skills, and MCP servers is meaningfully higher — on the order of 0.5–2
seconds of fixed overhead per call. This is process-isolated and robust (a crash or hang is
contained by killing the process), and the CLI is Hermes's stable public contract.

**A warm long-lived server, called over a local API.** Hermes ships its own
OpenAI-compatible API server that runs the agent in-process and stays warm. It exposes
`POST /v1/chat/completions`, `POST /v1/responses`, and a run API (`POST /v1/runs` with
server-sent events, plus approval and stop endpoints), alongside models, skills, and
health endpoints. Calling this server removes the per-turn interpreter and import cost
entirely, while keeping the runtime in its own process for isolation. This is the best
balance of speed and safety.

**Importing the agent loop directly in-process.** The lowest overhead, but the tightest
coupling: a crash, hang, memory leak, or a blocking synchronous loop would take down the
serving process, and the integration is bound to fast-moving internal APIs. This is
appropriate only for cheap, stable internals (as the current system already does for
session bookkeeping), not for the agent loop.

For a single long agent turn, the spawn overhead is a small fraction of wall-clock. For
short calls, high request rates, or many concurrent tenants, it becomes a large fraction,
and subprocess-per-call also produces a process storm under load. Keeping the runtime warm
therefore matters most exactly where the commercial product is headed: multi-tenant scale.

## How profiles (per-agent isolation) work

Every named agent is a Hermes **profile** — a directory under the data root containing its
own configuration, persona, memory, skills, sessions database, and workspace. The default
agent is the root profile. New profiles are seeded by copying from the root profile and are
registered in a profiles manifest.

The current chat path achieves per-agent isolation by spawning the CLI with the profile's
home directory passed as an environment variable and the profile's workspace as the working
directory. This gives arbitrary, unlimited, dynamically created profiles, each fully
isolated, with no additional configuration. That isolation model is the reason the current
design uses subprocess-per-turn rather than the warm API server.

The trade-off is important for the target architecture. Hermes's warm API server serves
**only the default profile** unless profile multiplexing is enabled, in which case
secondary profiles are reachable via a URL prefix, and only for the specific set of
profiles the gateway is configured to serve. In other words, the warm server does not, by
default, provide the unlimited dynamic per-agent isolation that the subprocess model
provides for free. Reconciling warm-runtime performance with per-agent isolation is the
central design decision for the commercial architecture.

## Recommended target architecture

The recommended topology keeps the Hermes runtime warm behind its API, keeps the router as
a separate process, and introduces a Go control plane that owns the moat and orchestrates
both over HTTP.

```
  ┌─ Go control plane — the commercial IP (PostgreSQL) ───────────────────────────┐
  │   certification engine · registry / shelf · identity · royalty & lineage       │
  │   tenants · RBAC · SSO · billing · quotas                                       │
  │   calls over HTTP:                                                              │
  │     → Hermes API server   /v1/chat/completions · /v1/runs (SSE, approval, stop) │
  │                           /v1/models · /v1/skills · management endpoints        │
  │     → LLM router          provider connections · models · combos · usage        │
  └────────────────────────────────────────────────────────────────────────────────┘
```

This is the hybrid described in the build-vs-rewrite section: Go and PostgreSQL where the
moat lives, a warm Python runtime for the agent, and a stable HTTP boundary between them. It
removes the per-turn subprocess spawn, preserves process isolation, and gives the control
plane a versionable contract rather than fragile stdout parsing or internal imports.

Three problems must be solved to adopt it.

**Profiles.** A single default runtime serves a single profile's chat. To support per-agent
chat over the API, choose one of: enabling multiplexing and registering each agent profile
with the gateway (suitable for a fixed, modest set of agents); running one warm runtime per
agent or tenant, with the control plane spinning up and pooling runtimes and routing to the
correct one (the natural fit for multi-tenant cloud, and consistent with a managed-sandbox
model); or retaining the subprocess-with-profile-home model for chat where dynamic,
unlimited profiles are required at the cost of per-turn overhead. For the multi-tenant
commercial product, one warm runtime per active agent or tenant is the recommended default.

**Management surface.** Much of the current management functionality (agents,
configuration, skills, memory, workspace, snapshots) is implemented through direct file,
database, and in-process access, which a separate Go process cannot reuse. The Go control
plane should prefer Hermes's native HTTP endpoints where they exist, and touch on-disk
formats only where Hermes exposes no endpoint — avoiding duplication and coupling to file
layouts.

**Authentication.** The current in-process design sidesteps Hermes's dashboard
authentication by marking its own routes as pre-authenticated. A separate Go process must
authenticate to Hermes's API properly (internal port or API key) and to the router via its
token scheme.

Adopting this topology moves the integration boundary from "extend Hermes in-process" to
"a separate HTTP client of Hermes's API." That is a deliberate re-architecture rather than
an add-on, and it is the right direction for the enterprise control plane. It can be done
incrementally: stand up the Go control plane calling the warm Hermes API and the router,
own certification, registry, and billing in PostgreSQL, and migrate management calls from
direct file and database access to Hermes's HTTP API over time.

## Delivery steps

1. Enable and validate Hermes's warm API server as the chat backend, replacing
   subprocess-per-turn for the agent-run path; measure the latency improvement against a
   representative profile with real skills and MCP servers loaded.
2. Decide the profile strategy (recommended: one warm runtime per active agent or tenant,
   orchestrated and pooled by the control plane).
3. Stand up the Go control plane as an HTTP client of the Hermes API and the router,
   authenticating properly to both.
4. Implement the certification data model in PostgreSQL — badges, coverage maps, expiry,
   and drift — as the first control-plane capability.
5. Migrate management operations from direct file and database access to Hermes's native
   HTTP endpoints where available.
6. Add registry, lineage, and royalty settlement as the cross-organization phases begin.

None of these steps requires reimplementing the Hermes runtime. The path to both
performance and the commercial moat is a warm Hermes API, the LLM router, and a Go control
plane with PostgreSQL on top.


# Build vs. Rewrite — Reimplementing Hermes in Go

A recurring architectural question is whether to clone the Hermes agent runtime and
reimplement it in Go — keeping only the important features, skipping the CLI, and backing
it with PostgreSQL — in the way community projects have reimplemented OpenClaw in Go
(GoClaw, LiteClaw, and similar). This section sets out the decision and its reasoning.

## Recommendation

**A full Go reimplementation of Hermes is legal but strategically wrong.** Three reasons,
each sufficient on its own:

1. **The agent loop is bound by model latency — Go yields essentially no speedup there.**
   Wall-clock time is dominated by LLM inference and tool I/O, not Python execution.
2. **A fork forfeits Hermes's upstream velocity forever** (370+ contributors, frequent
   releases) **and breaks the Python skill/plugin ecosystem** (`SKILL.md` + `plugin.py`
   hooks + the `agentskills.io` standard), which is a large part of Hermes's value.
3. **Every month spent rewriting a commodity runtime is a month not spent on the
   certification engine** — the only defensible asset.

**The recommended alternative (hybrid):** keep the Python Hermes runtime as a vendored,
orchestrated dependency behind its own API. Build everything new — and everything that
genuinely benefits from Go and PostgreSQL — in a separate layer: the certification engine,
registry, identity, royalty and lineage, and the multi-tenant control plane. That is where
Go's concurrency and PostgreSQL's transactions earn their keep, and it is where the moat
lives. It is also, almost exactly, the two-repository architecture already specified for
the project.

The result: PostgreSQL, Go, a single-binary control plane, and multi-tenant concurrency —
placed at the layer that needs them — without rewriting the agent, and while keeping
Hermes's upstream and ecosystem for free.

## Legality

Hermes is MIT-licensed. It may be forked, reimplemented in any language, kept closed, and
commercialized, provided the copyright and license notice are preserved. There is no legal
blocker; the decision is purely engineering and strategy.

## The decision is about where to spend scarce effort

This is not "Go versus Python." It is "where does a small founding team spend its months?"
There is one certification moat to build and several fast-moving competitors. A rewrite is
a multi-month investment in a **commodity** engine that the product vision itself marks as
"never a moat." For each component the question is: does rewriting it in Go create
defensible value, or merely re-create something free that improves without any investment?

## Three technical truths

**The agent loop will not get faster in Go.** An agent turn is: assemble a prompt, call
the model (seconds), run a tool (network or disk I/O), and repeat. Python interpreter
overhead is negligible next to multi-second model calls. Rewriting the loop optimizes the
one part that is not the bottleneck; user-perceived speedup is approximately zero.

Where Go **does** win is real, but it is all deployment and orchestration, not
intelligence: I/O concurrency at scale (multiplexing many messaging gateways, cron jobs,
and parallel tenants), single static-binary distribution (far easier to ship to an
enterprise server than a Python environment), and low memory and fast startup. Those are
control-plane and gateway properties — which is exactly where a Go layer belongs.

**A fork forfeits upstream and breaks the plugin ecosystem.** Hermes ships features
continuously; a point-in-time Go fork freezes there and chases a moving target forever, as
new models, gateways, sandboxes, and security fixes land in the Python upstream. Moreover,
Hermes skills are `SKILL.md` plus `plugin.py` with Python hooks, and the entire
`agentskills.io` ecosystem is built on this. A Go runtime cannot run those plugins without
embedding a Python interpreter (which defeats the single-binary benefit) or defining a new
plugin ABI (which the ecosystem does not target) — so a fork would inherit Hermes's
name-recognition while losing its ecosystem.

**Opportunity cost is the real price.** The rewrite's true cost is not the code; it is the
certification engine not built during those months, while well-funded competitors advance.

## What the OpenClaw-in-Go precedent actually shows

Several Go reimplementations of OpenClaw exist. Their headline value is uniformly
**single-binary, low memory, fast startup, multi-tenant isolation, and native
concurrency** — never "a smarter agent." That confirms the point above: Go wins on
deployment and operations, not on intelligence, and those are precisely the properties an
enterprise control plane or cloud runtime needs. Furthermore, OpenClaw is a simpler,
config-first surface than Hermes (which adds a self-improvement loop, many gateways,
multiple sandboxes, a richer memory model, and Python plugins), so porting Hermes is
materially larger. These are mostly community projects optimizing their own deployment,
not companies whose moat depends on the runtime — a different goal.

## PostgreSQL without a rewrite

Wanting PostgreSQL is not a reason to rewrite the agent. It belongs where it earns its
keep — all of it new code written in Go regardless:

| Data | Store | Rationale |
|---|---|---|
| Per-agent session history, memory, skills | SQLite (Hermes-native) | Single-node, local; already works |
| Certification badges, coverage maps, expiry, drift | PostgreSQL (Go service) | The moat's data — queryable, multi-tenant |
| Registry, lineage, royalties | PostgreSQL (Go) | Cross-organization, transactional |
| Tenants, RBAC, SSO, billing, quotas | PostgreSQL (Go control plane) | The enterprise control plane |

Hermes keeps SQLite for what SQLite is good at; PostgreSQL sits at the certification,
registry, and control-plane layers.

## Options compared

| Option | Description | Speedup where it matters | Keeps upstream + plugins | PostgreSQL | Effort | Verdict |
|---|---|---|---|---|---|---|
| A. Full Go rewrite of Hermes | Reimplement the whole agent | ~none (model-bound) | No | Yes (but everything rebuilt) | Very high | Avoid |
| B. Extend Python Hermes only | The open-source design as-is | n/a | Yes | Enterprise only | Low | Viable but limited |
| C. Hybrid: Hermes runtime + Go/PostgreSQL moat and control plane | Python agent, Go for certification/registry/identity/royalty/control plane | Yes, where Go helps | Yes | Yes, where it matters | Medium | **Recommended** |
| D. Selective Go services | Additionally reimplement only measured hot paths (gateway multiplexer, router) as Go services calling Hermes | Yes, targeted | Yes | Yes | Medium-high | Only when a real bottleneck is measured |

The recommendation is Option C now, adding Option D surgically only when a genuine
bottleneck is measured.

## Time estimates for a full rewrite

These are ranges, not commitments, assuming a small strong team with AI assistance. A
Python-to-Go port is a paradigm shift (dynamic to static typing, goroutines, explicit
errors, different SDKs per gateway and sandbox), and the real cost is parity testing, not
first-draft code.

| Scope | Included | Estimate | Note |
|---|---|---|---|
| Thin MVP | Agent loop, memory (Postgres), skills-as-data, one gateway, core tools, one provider; single binary | ~1–2.5 months | Demos work; no plugin compatibility |
| Broad parity | Most of MCP both ways, several gateways, multiple sandboxes, cron, approval gate, self-improvement loop, hardening | ~6–12+ months | And a moving target |
| True full parity | Everything Hermes does today including the Python plugin/skill ecosystem | Impractical | Requires embedding Python or a new ABI |
| Ongoing maintenance | Tracking upstream Hermes features and security | ~1–2 FTE, permanent | The cost the first estimate omits |

Even an optimistic six-week MVP produces a worse agent than the current dependency — fewer
gateways, no plugins, behind on models — while consuming the very weeks needed for
certification.

## When a rewrite would be justified

Only if all of the following become true, none of which hold today: a real non-model
bottleneck is measured in the runtime (and even then, reimplement that one service, not the
agent); Hermes's upstream stalls or its license changes; the certification moat has already
shipped and the runtime is now the growth constraint; or maintaining a vendored Python
dependency proves a recurring drag after the hybrid has been tried. Until then, the
principle is: rent the runtime, own the moat.


# Product Roadmap — From Studio to Terminal

> Ties the current build (01), the vision
> (02), the moat (06), and
> the business model (07) into a sequenced plan.
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
      (07 §2).
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
      the Certification Moat section.
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
      (06 §5, 07 §4).
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
  (05 §4).
- **Legal/contract** — craft ownership, liability, consent, anti-impersonation — gate
  every phase; don't let them lag the product.
- **Keep commodity engines rented** — do **not** out-build OpenRouter/LiteLLM or fork
  Hermes' core; extend from `xnobrain`, per `AGENTS.md`.

## The one-line sequencing rule

> Prove the badge (Phase 1) → make it matter (Phase 2) → let it travel with royalties
> (Phase 3) → make it the standard (Phase 4). Everything commodity, rent. Everything
> moat (05/06/07/09), build — starting with the fidelity scorer.


# Hermes Agent — Full Use-Case Catalogue & Read-Across

> Source: [hermes-agent.nousresearch.com/docs/user-stories](https://hermes-agent.nousresearch.com/docs/user-stories),
> fetched 2026-07-24. The page lists **262 stories** across **15 categories** from
> **11 source channels**. **220** were captured in full detail here; the final ~42
> were cut by the page's content-length limit (noted at the end). Each entry below is
> a compressed-but-faithful record: title, what the agent does, and the named
> tools/models/numbers. **Sections C–E carry the analysis** — which stories are nearest
> to Twin Terminal, where Hermes and OpenClaw appear together, and what it means for
> XNOBrain.

## Page structure (verbatim)

- **262 stories · 15 categories · 11 sources.**
- **Categories (with counts):** Dev Workflow (65) · Personal Assistant (44) ·
  Integrations (26) · Meta & Ecosystem (21) · Creative (19) · Business Ops (16) ·
  Cost Optimization (13) · Content Creation (11) · Research (9) · Enterprise (9) ·
  Messaging (8) · Privacy & Self-Hosted (8) · General (6) · Trading & Markets (5) ·
  Marketing (2).
- **Sources (with counts):** Discord (116) · X/Twitter (42) · GitHub (38) · Blog (20) ·
  YouTube (17) · Reddit (15) · GitHub Gist (4) · Hacker News (4) · LinkedIn (3) ·
  Podcast (2) · Product Hunt (1).

---

## A. The catalogue, by category

> Format: **#N — Title** · what it does; *tools/models/numbers* (source).

### Dev Workflow (self-improvement, memory kernels, multi-agent, tooling)

- **#3** — Local-first cognition layer; building a memory/cognition layer for Hermes (Discord).
- **#10** — Codex watches Hermes agent-to-agent workflows live; runtime monitor catches breaks, live fixes; *Codex, GPT-5.4 extra-high* (X).
- **#12** — "Converse mode"; plugin makes agent talk before executing tools (approval-first) (Discord).
- **#13** — Token profiling; dashboard finds *73% of every API call is fixed overhead* across 6 request dumps; *Hermes v0.6.0* (GitHub).
- **#25** — LaTeX→Unicode math rendering in the TUI (GitHub PR).
- **#29** — "Hadn't coded in 20 years"; vibe-coding via *Claude Code + Hermes* (Discord).
- **#31** — 200–400h memory kernel; 3-layer *L1 Hindsight / L2 Graphiti / L3 MemPalace*; project **BRAINSTACK** (Discord).
- **#41** — `hermes mcp-server` exposes 9 Hermes tools to *Claude Desktop, Cursor* (Discord).
- **#43** — Nous runs **12 Hermes instances in parallel daily** to build Hermes; *900k+ compute-seconds, 5B+ tokens*; now a top-100 GitHub repo (X, @Teknium).
- **#50** — Hooks that swap in better tools at every agent run (Discord).
- **#51** — RCA script audited **129 sessions/23 days → 112 had ≥1 approval-gate violation** (GitHub).
- **#52** — CCD multi-agent pod on M2 Ultra; *Mem0 + Qdrant*, per-agent profiles (GitHub).
- **#53** — Custom kernel: "the LLM never touches the disk"; Python compiles semantic signals into *SQLite FTS5 graph* (Discord).
- **#57** — Agent editing its own internals; worry about updates overwriting (Discord).
- **#58** — Independently built a stack, converged on Hermes (same self-improve/memory/skills design); **300 PRs in a week** (X).
- **#64** — Nightly config+DB backup to GitHub; multi-agent management (Discord).
- **#67** — Skill-audit skill that improves itself on cron (sandboxed self-improvement loop) (Discord).
- **#69** — Hermes on VPS, "phones home" over *Tailscale*; scoped tags; "yolo mode, human owns boundaries" (Discord).
- **#74** — "Day 10: it knows my codebase better than I do"; internalized prefs by 5th iteration (X).
- **#75** — 22k-line memory kernel; *temporal context graph in SQLite* with decay/promotion/supersession (Discord).
- **#78** — Local network **Kanban** so agents see task state; project **Goban** (Discord).
- **#84** — Agent auto-acts on file-change events (X).
- **#86** — **Cartographer** (memory: semantic wiring, emotional topology → temporal graph in SQLite) + **Agent IRC** (real-time chat between Hermes, Claude, Codex, OpenCode, Gemini) (Discord).
- **#88** — Multi-agent auto-build: *GPT-5.4 plans → MiniMax M2.7 codes → local Qwen 35B QA → repair → ship* (X).
- **#92** — 5 apps built & launched in a single day (LinkedIn).
- **#102** — Agent ships micro-apps via *Val Town* (Discord).
- **#104** — Long-running instance accumulates codebase knowledge (commit style, legacy API sequences) (Medium).
- **#106** — **Recall**: Hermes-native inspectable (non-black-box) memory provider (Discord).
- **#109** — Non-coder had *Codex* build a full VPN service (Xray/Wireguard, admin panel) (Discord).
- **#116** — Local *Gitea + watchtower* auto-restarts Hermes within 10 min of push (Discord).
- **#119** — Memory kernel that "compiles thoughts, not vectors"; auto contradiction resolution (Discord).
- **#126** — Hermes more stable; used to troubleshoot **OpenClaw** (Reddit).
- **#127** — Native SwiftUI Mac app (SSH to host/files); **Hermes Desktop v0.4.0** (Discord).
- **#128** — Session-compression plugin preserving work thread; **Hermes Operational Checkpoint** (Discord).
- **#136** — Persistent structured memory because compression drops constraints after ~30 turns; **Hermes Memory** (Discord).
- **#140** — Compile skills into code, invoke AI only at needed steps (model-agnostic reliability) (Discord).
- **#143** — Built-in **Kanban multi-agent**: parent posts cards → child sub-agents pull → parallel → report; "GAME CHANGING" (Reddit).
- **#149** — Vectorless RAG via *PageIndex* + tool-based reasoning (Discord).
- **#156** — Custom TUI so Hermes "feels like OpenCode"; project **Herm** (Discord).
- **#158** — **Rookery**: local llama-server process manager (Discord).
- **#160** — UI for the memory system they loved after trying *Mem0/QMD/Mempalace/Honcho*; **OpenConcho** (Discord).
- **#162** — Telegram → *Modal* serverless; *~40% faster* on research vs fresh agent; TokenMix benchmark (Medium).
- **#163** — "Every OpenClaw update breaks something — Hermes just runs" (Reddit).
- **#173** — Hermes in *NixOS + container* (Discord).
- **#178** — **Dream Auto**: idle *MCTS background reasoning*, injects insights into context (Discord).
- **#181** — Hermes as a **watchdog** over OpenClaw; "saves countless hours and credits" (X).
- **#184** — Competitor-analysis swarm ported from *Codex* in ~2h; custom memory routing (Discord).
- **#187** — "Hermes is OpenClaw set up + 1 week debug + RAG + memory + better tool calling"; *Qwen3.5-9b on 16GB VRAM, 10/10* (Reddit).
- **#190** — **STANDING.md** plugin injects standing instructions via `pre_llm_call` so the agent stops guessing (Discord).
- **#197** — `SKILL.md` as a Notion/Outlook/SharePoint tool router (Discord).
- **#198** — 3,000+ self-improvement logs on a custom NL harness; *MiniMax m2.7* (Discord).
- **#207** — **Skill Factory**: silently watches workflows → writes `SKILL.md` + `plugin.py` (GitHub).
- **#213** — 8h/day email pipeline; *DBOS + PostgreSQL + S3 + Gmail API + Claude Opus*, 3-actor (GitHub).
- **#217** — Hermes orchestrates *Claude Code/Codex over SSH*; Hermes writes prompts & reviews (Discord).
- **#218** — All llama.cpp run/optimize knowledge packaged as a skill (Discord).
- *(plus further Dev Workflow entries among the ~42 uncaptured tail).*

### Personal Assistant (proactive, memory, family, health)

- **#1** — "Every weekday 9am summarize inbox → Slack"; NL cron; writes its own skills (Blog).
- **#2** — Self-hosted Google Drive via *Nextcloud + LibreOffice* (Discord).
- **#5** — "Google me and ship a landing page to my VPS"; search → build → *SSH* deploy → text me (X).
- **#19** — Google Tasks create/update/list (GitHub).
- **#21** — Bedtime stories with consistent protagonist across sessions (GitHub).
- **#22** — Daily Obsidian journaling; testing *Kimi 2.5* (OSS models weaker at skill-triggering vs Sonnet 4.6) (Discord).
- **#33** — Raspberry Pi 5 running Hermes 24/7; memory not synced across devices (Discord).
- **#36** — Tasks across *Obsidian + Apple Calendar + Signal*, Turkish, cron (Discord).
- **#38** — "Claude (Opus 4.7) for chat, Hermes 24/7 on a mini PC for real-world stuff" (email, forms, calendar) (Discord).
- **#44** — Two-tier email: Python detects, LLM fires only when needed; *himalaya IMAP* (Discord).
- **#60** — Pi 4 home-server "central brain"; persistent memory (GitHub).
- **#62** — *Qwen3.5:4b on a 5060Ti*; Telegram assistant; 4B "snappy, alive" (Reddit).
- **#63** — Discord assistant on *GPT-5.5 / DeepSeek v4*; "life changing" (X).
- **#72** — Voice-first fitness coach learning body patterns (training→nutrition→recovery); Telegram (Discord).
- **#73** — Meal planner; **Meal Manager** plugin; weighted score *60% availability/40% recency* (Discord).
- **#82** — Apple Health + Threads + Gmail + Calendar in one CLI; "Hermes = CEO, OpenClaw = Senior Engineer" both on Obsidian (Substack).
- **#95** — 3-layer memory doctrine: durable facts / session search / skills; "save facts, not task progress" (Discord).
- **#96** — "9am check HN → DM Telegram"; MEMORY.md + USER.md, FTS5 search, NL cron (dev.to).
- **#101** — Proactive check-ins ("anything to watch this afternoon?") (GitHub).
- **#105** — "5 things Hermes does ChatGPT won't": persistent memory, runs code, acts in apps, messages first (Reddit).
- **#107** — **Obsidian vault as long-term memory backbone**; 794 upvotes (Reddit).
- **#115** — Personal assistant on *Qwen3.5 27B* (VERY good) (Reddit).
- **#122** — Semantic knowledge substrate over Obsidian/vimwiki/Hermes sessions; **Cartographer + mapsOS** (Discord).
- **#124** — Cron nudges via Discord/Signal for executive function; ~14k tokens (Discord).
- **#135** — One Hermes for a family of 3 on WhatsApp; replaced a $200 ChatGPT sub (X).
- **#147** — Hermes over iMessage on always-on Mac Studio; in group chats (GitHub).
- **#174** — Reads HackerNews → daily email summary (Discord).
- **#176** — Obsidian + home automation + server mgmt on a cheap locked-down VPS (HN).
- **#179** — PM agent runs morning/evening standups for ADHD; Manager + Paperclip sub-agents (X).
- **#180** — Memory lets user jump between projects (vs OpenClaw "one-track"); + Paperclip (Reddit).
- **#188** — iOS sensors (health/location/voice) → contextual answers (Discord).
- **#195/#205** — Health Connect / Whoop biometric data pulled in locally (Discord).
- **#202** — "Replaced everything with a single Hermes"; autoresearch + LLM-wiki second brain (X).
- *(#45, #55/#56 mapsOS, others).*

### Integrations (MCP, connectors, hardware, commerce)

- **#4** — *Hindsight Cloud* memory connector; *Vectorize.io* (LinkedIn).
- **#14** — Team agent: *SourceDev* repo index, *Tenderly MCP* onchain debug, LLM-Wiki (Discord).
- **#15** — Hermes + Browser Harness on Hostinger VPS; *claude-opus-4.7 via OpenRouter* (Gist).
- **#17** — **jMunch MCP**: *52 tools* via tree-sitter for code intelligence (GitHub).
- **#47** — **Vercel Sandbox** backend (microVMs, snapshot FS); backends now local/Docker/Modal/SSH/Daytona/Singularity/Vercel (GitHub PR).
- **#48** — Full *Feishu (Lark)* coverage (Docs/Sheets/Bitable/Calendar/Wiki/Drive/Email) (GitHub).
- **#93** — *Firecrawl* scrape/search/browse (LinkedIn).
- **#97** — **Onchain identity + proof-of-work** attestations via *Ethereum Attestation Service* on Base mainnet (Discord).
- **#103** — *Hunter.io* email lookup via *Composio MCP* for sales (GitHub).
- **#108** — `hermes mcp serve`: "fat agent → thin tool provider"; exposes 15+ platforms, FTS5, 73-skill surface (Gist).
- **#117** — AdGuard Home plugin (Discord).
- **#118** — Themed browser **Webchat** UI on MEMORY.md + USER.md (GitHub).
- **#132** — Watches homelab validators (*0G, FortyTwo*), pings Telegram on state change; ex-OpenClaw (Discord).
- **#137** — Cross-agent memory across *Hermes + Claude Code + Cursor*; BM25 + vector + KG (GitHub).
- **#138** — Remote-start car via *OnStar* skill (Discord).
- **#139** — Desktop computer-use module (noVNC, screenshots, mouse/keyboard) (GitHub).
- **#142** — *JMAP* email for Fastmail (GitHub).
- **#151** — Discord-read plugin (missed from OpenClaw) (Discord).
- **#159** — Bundled many API keys into single endpoints (finance via one call) (Discord).
- **#170** — *BoltAI v2* gateway plugin (markdown + slash commands) (Discord).
- **#186/#209** — **Home Assistant** add-on; "zero to agent in under 5 min" (Discord/X).
- **#189** — **agentbox.id**: agent-optimized email service (Discord).
- **#200** — *M5 Cardputer* embedded device via API (OTA, TTS/STT) (Discord).
- **#204** — **Merxex**: agent-to-agent **commerce**/monetization layer (buy/sell services) (GitHub).
- **#214** — Agent's own inbox via *AgentMail MCP* (no SMTP/OAuth) (X).
- **#219** — Hermes in *Zed* via **ACP Registry** (auto discovery/install) (GitHub).

### Meta & Ecosystem (dashboards, installers, ecosystem maps, hosting)

- **#20** — **hermes-for-win**: one-click Windows installer, auto-start (GitHub).
- **#24** — Podcast: "Hermes has won" — self-improving skills, 3-layer memory (Spotify).
- **#28** — TUI dashboard watching the agent think; **Hermes HUD** (Discord).
- **#49** — Show HN independent install guide (macOS/Linux/WSL2/Termux) (HN).
- **#80** — Browser dashboard: PTY terminal, file editor, gateway control, token analytics (Discord).
- **#94** — Every tool call → per-profile SQLite + *5 Grafana dashboards* (Discord).
- **#111** — "One month with Hermes: don't build the whole machine on day one" (Reddit).
- **#145** — "Switched from OpenClaw to Hermes, not looking back" (X).
- **#146** — **awesome-hermes-agent**; tied to **agentskills.io** standard (GitHub).
- **#153** — 4 custom skins for HermelinChat GUI (Discord).
- **#164** — Product Hunt: competitor (Clawdi) calls it "the best self-improving agent we've used" (Product Hunt).
- **#167** — Native Windows app wrapper (Reddit).
- **#171** — macOS control center for local models on 2 machines (Discord).
- **#182** — **H-OPS**: operator dashboard for multi-agent on Hermes Kanban (Discord).
- **#183** — **hermesatlas.com**: scraped whole ecosystem, star-rated by category (X).
- **#192** — Mini-documentary with hackathon finalists + Nous co-founder (Discord).
- **#196** — 4 agents (PM/Dev/Ops/Content) 24/7 on 32GB Ubuntu; *5 MCP servers, 34 tools*, daily auto-distillation (Discord).
- **#201** — **Hermify**: managed hosting (bring API key + Telegram bot) (Reddit).
- **#211** — Shadow-to-live **migration path from OpenClaw** (GitHub).

### Business Ops

- **#42** — Roofing lead-gen/CRM app (Discord).
- **#59** — 24/7 assistant on *Supabase CRM*; agent proposed a "Supabase MCP scripts" skill itself (YouTube).
- **#100** — Triages & works tickets in *Plane.so*; documents to Obsidian; + Claude Code (Discord).
- **#103** — Hunter.io sales outreach (see Integrations).
- **#113** — Create/edit *Google Slides* decks (GitHub).
- **#125** — "Day 297 streak: **$100K of client work automated**"; 900k+ compute-sec, 5B+ tokens (X).
- **#134** — **Hermes as Chief of Staff**: main agent w/ cross-project memory + per-project sub-agents (one per Slack channel); daily WhatsApp report (Discord).
- **#168** — Task-centric memory for a printing factory; auto-categorize (Printing/Stocks), compress done tasks to cards (GitHub).
- **#184** — Competitor-analysis swarm (see Dev Workflow).
- **#194** — Auto-transcribe Meet, control from Teams, local models for client data (Substack).

### Cost Optimization

- **#27** — Switch Hermes/OpenClaw with free models on *primeclaws.com* (Reddit).
- **#30** — Replaced Perplexity (~$10/few days) with **Gigaxity** (7 MCPs + SearXNG) (Discord).
- **#61** — Multi-agent, weeks continuous, Telegram; "what I use it for & how I keep it cheap" (X).
- **#70** — **ZeroID**: *RFC 8693 token exchange* for sub-agent scope delegation & context cost (Discord).
- **#99** — **90% token cut** (~$130/5d → ~$10/5d); Android via *Termux + OpenRouter*; "customization is a trap; output is the skill" (Podcast).
- **#112** — Under **$20/mo** (Minimax M2.7 VPS) vs OpenClaw Mac-Mini-M4 + Opus 4.6 ~$80–150/mo (Medium).
- **#114** — Free GPT-4.1 via Copilot Pro ($10/mo); Hermes delegates coding to *OpenCode* (Discord).
- **#148** — $10/mo Hetzner VPS; *Claude Opus via OpenRouter* (YouTube).
- **#165** — Smart-routing tiers (*Gemini 3.1 Flash Lite* mechanical / *Sonnet* delicate / *Minimax* low-overhead); saved ~10h + $40 (Reddit).
- **#177** — Multi-agent on *Ollama* to cut cost (Discord).
- **#185** — **RTK** integration rewrites terminal commands; **60–90% context-token cut** (Discord).

### Content Creation / Marketing

- **#6** — Turkish locale skill pack (TRY data, Turkish news, daily PNG cards, Telegram cron); zero API keys (Discord).
- **#8** — Skill pack on *Meta CLI/MCP* (Marketing) (Discord).
- **#16** — Weekly cron: top-3 trending AI tools → makes a reusable skill (YouTube).
- **#40** — **UGC ad studio**: URL → scrape → *Meta Ads Library + TikTok Creative Center* hooks → brief in *~4 min*, zero prompt-eng; *Higgsfield* (X).
- **#77** — X roast-poster without a $100 API sub (Discord).
- **#123** — Writes in the user's voice (reads their articles first); Mac Mini running OpenClaw + Hermes (X).
- **#129** — Cron triages tech news into Discord channels by urgency, 3×/day (X).
- **#141** — Tweets in the creator's voice from past scripts; recalls preferred emojis in a new session (YouTube).
- **#155** — LinkedIn posts that remember the user's style (YouTube).

### Research

- **#30** — Custom research stack (see Cost).
- **#65** — Daily research brief → Discord/Slack/Notion/Obsidian/email; tracks ignored items, self-improves (X).
- **#66** — **Hermes-lab**: autonomous experiment bookkeeper (Karpathy/Sakana/AIDE-inspired) (Discord).
- **#76** — Ported the Python weather stack (MetPy/Herbie/cfgrib/WRF) to Rust for plugins (Discord).
- **#79** — Self-improving **LLM Wiki second brain** (Karpathy pattern); public site (Medium).
- **#206** — **AI-assisted drug discovery for Africa** (pharmacy undergrad); *ChEMBL, AlphaFold, OpenFDA, QSAR* (Discord).

### Enterprise

- **#32** — Daily cybersec+AI briefing on a **local k8s cluster** (Discord).
- **#37** — Native **Vertex AI** provider for GCP-standard orgs (GitHub).
- **#54** — **EU AI Act compliance via Ombre**: tamper-proof audit, prompt-injection blocking, memory encryption, hallucination detection, cost tracking, compliance exports (GitHub).
- **#90** — Azure-compliant prompt patch to avoid content-filter trips (Gist).
- **#130** — CLI/gateway-first: **13 messaging platforms under one process** (Substack).
- **#175** — AWS VPS + Google Workspace automation; setup non-trivial (Discord).
- **#193** — "95% of AI users see no results" VC deep-dive on Hermes swarms/experiment loops (X).
- **#208** — **Kubernetes pod-hop handoff** across restarts on shared PVC (GitHub).

### Trading & Markets

- **#7** — Self-learning weather-trading bot: scans every 60 min, compares 3 forecasts, buys undervalued buckets; **$100 → $216 in 48h** (X).
- **#81** — Polymarket: reads 4 layers in parallel (order book, on-chain addresses, news-lag, positions); *Polymarket module + News Skill* (X).

### Messaging

- **#23** — **QQ Bot** adapter for China (822 lines; 95M+ users) (GitHub).
- **#26** — DM-based **approval gate** for kid-facing Discord bots (GitHub).
- **#34** — **LINE** integration ask (95M+ MAU Japan) (GitHub).
- **#157** — Native Android client **Hermes Relay** (streaming, slash cmds, tool viz) (Discord).
- **#191/#203/#210** — Remote phone control / home-server + Telegram / web-proxy session hand-off to mobile (Discord).

### Privacy & Self-Hosted

- **#9** — Shared local *SearXNG* container across agents (Discord).
- **#85** — **Legal work on an edge GPU, 4B Gemma, no cloud APIs**; "self-hosting the main loop is non-negotiable" (GitHub).
- **#91** — "Sandbox it — don't give it free reign" (HN).
- **#131** — *Tailscale serve* — secure remote access, no exposed ports (GitHub).
- **#154** — Independent security eval: 5 defensive patterns (OSV malware check for MCP packages, credential stripping) (Gist).
- **#212** — Skill that hardens the agent against common LLM threats (Discord).

### Creative

- **#35** TouchDesigner generative visuals · **#39** B1 droid skin · **#46** X→NotebookLM podcast workflow (agent-designed) · **#71** chess blunder finder (blunder-lens.com) · **#87** agent "dreams" nightly, 5 REM cycles 23:00–06:00, **~$0.014/night on Haiku** · **#89** shadcn finance dashboard + Manim explainers · **#98** Matrix skin · **#110** auto-play Minecraft skill (20+ min thinking) · **#120** **Hermes Inc.** Telegram startup-sim (AI teammates argue/remember/evolve) · **#121** personal web-dev style as a skill · **#133** speech-to-speech + generated ambient music · **#150** agent tone examples co-written with a sibling (agent "Reina") · **#166** long voice-call timeout plugin · **#169** twice-daily Tidal curation · **#192** documentary · **#215** spare-laptop Hermes autonomously builds a **RenPy visual novel (10 images) in ~10 min** via LM Studio + ComfyUI · **#216** browser translate/summarize extension (Hermes-4-70B).

### General

- **#11** Voice-from-terminal for accessibility (*Whisper.cpp*) · **#68** "AI employee for my hardest tasks" (Hermes + ChatGPT 5.5) · **#144** local community agent on a 16GB Mac mini · **#152** teaching a Linux user group to build agents · **#161** **blind-since-birth user built an NVDA screen-reader translator addon** · **#199** Spanish Hermes guide built with Hermes.

*(#220 and ~41 further stories in the page's tail were cut by the fetch content-limit;
the captured 220 already cover all 15 categories and every named integration pattern.)*

---

## B. The hard numbers, pulled out

| Metric | Story |
|---|---|
| **$100 → $216 in 48h** (weather-trading bot) | #7 |
| **$100K of client work automated** (day-297 streak) | #125 |
| **90% token-spend cut** ($130/5d → $10/5d, Android/Termux) | #99 |
| **60–90% context-token cut** (RTK) | #185 |
| **73% of each API call is fixed overhead** (measured) | #13 |
| **$0.014/night** dream cycles on Haiku | #87 |
| **Under $20/mo** full setup (Minimax VPS) vs OpenClaw $80–150/mo | #112 |
| **~40% faster** research vs a fresh agent | #162 |
| **112/129 sessions** violated the approval gate (audit) | #51 |
| **12 parallel Hermes instances/day; 5B+ tokens; top-100 GitHub repo** | #43 |
| **794 upvotes** for the Obsidian-as-memory pattern | #107 |
| **52 tools** (jMunch MCP) · **73-skill** surface (mcp serve) | #17, #108 |

---

## C. Stories NEAREST to the Twin Terminal thesis (the read-across)

These are the ones to study — each is a fragment of an engine you plan to own.
See the Vision section for the engine numbers.

**Encoding a named person's craft / voice (Engine 02 + 06 — the "twin" itself).**
The single most on-thesis cluster: Hermes users are *already cloning individual
judgment and voice.*
- **#123** writes in the user's voice (reads their articles first); **#141/#155**
  learn and *remember* a creator's style + emojis across sessions; **#150** co-writes
  the agent's tone with a sibling. → This is a **consumer-grade expert twin** with **no
  fidelity certification**. Your gate is exactly the missing layer.
- **#82** "Hermes = CEO, OpenClaw = Senior Engineer" — role-specialized twins already
  collaborating.

**Capturing scarce/operational expertise (your supply thesis).**
- **#168** printing-factory task memory; **#14** team protocol/onchain knowledge;
  **#206** drug-discovery workflows; **#218** "all my model-running knowledge as a
  skill." → Same instinct as **Cloneable** (the Startup Landscape section):
  turn tacit craft into reusable agents. None certify fidelity.

**Multi-agent councils / orchestration (Engine 04).**
- **#134** Chief-of-Staff + per-project sub-agents; **#143/#78** Kanban parent→child
  fan-out; **#88** plan→code→QA→ship pipeline; **#181** watchdog agent; **#196** 4-agent
  org with auto-distillation. → Your "cross-org twin councils / disagreement maps" have
  working single-org precedents here.

**Verification, approval, audit, drift (Engine 07 — the moat's neighbors).**
- **#12/#26** approval gates; **#51** *session audit that measured 112/129 gate
  violations*; **#54** Ombre EU-AI-Act compliance (tamper-proof audit, hallucination
  detection); **#190** STANDING.md so the agent stops guessing; **#154** security eval.
  → The ecosystem is *reaching for trust/audit primitives* but **nobody measures
  fidelity-to-a-named-expert**. This is the clearest confirmation that Engine 07 is
  open whitespace **even inside the Hermes community.**

**Memory as a durable, inspectable asset (Engine 02).**
- **#31/#53/#75/#86/#106/#119/#136/#160** — an entire cottage industry of memory
  kernels (SQLite graphs, temporal decay, contradiction resolution, "inspectable, not
  black-box"). → Your "craft ledger travels with the twin" is *the same need*, one
  level up. You can likely **adopt/partner** rather than build memory from scratch.

**Commerce / identity / attestation (Engines 05 & 09).**
- **#204 Merxex** = agent-to-agent **commerce** layer; **#97** = **onchain identity +
  proof-of-work attestation** (Ethereum Attestation Service). → Primitive versions of
  your identity + royalty engines already exist to build on or learn from. Attestation
  on-chain is one candidate mechanism for tamper-evident provenance/badges.

**Skill auto-generation & a skill standard (Engine 06 + the registry).**
- **#207 Skill Factory** and **#67** self-improving skill-audit; **#146 agentskills.io**
  standard + **#183 Hermes Atlas** registry/ratings. → The "workshop that fills the
  shelf" and a **skills standard + rated registry already exist** in the Hermes world.
  Your registry should **interoperate with agentskills.io**, not reinvent it — and add
  the one thing Atlas's star-ratings lack: *measured fidelity + expiry.*

## D. Hermes and OpenClaw deployed together

OpenClaw appears constantly as Hermes's reference point — often **running side by
side**:
- **#82** Hermes(CEO)+OpenClaw(Sr Eng) on one Obsidian vault; **#123** both on one Mac
  Mini; **#181** Hermes as a *watchdog over OpenClaw*; **#126** Hermes troubleshoots
  OpenClaw; **#132/#151/#180** users migrating from OpenClaw but porting its features.
- **Migration/switching:** **#27** primeclaws.com switches between them; **#145**
  "switched, not looking back"; **#187** "Hermes is OpenClaw + 1 week debug + RAG +
  memory"; **#163** "every OpenClaw update breaks something"; **#211** shadow-to-live
  migration tool; **#211/#151** feature-parity ports.

**Implication:** the two ecosystems are **interoperable and adjacent**, and users
already run multi-runtime setups. A platform that is **runtime-agnostic at the twin
layer** (certify/host twins regardless of whether the underlying runtime is Hermes or
OpenClaw) is a credible positioning — it rides the fact that users already mix them.
The startups leveraging *both* today are mostly **individual power users and small
tools** (primeclaws.com, watchdog setups, migration utilities), not funded companies —
i.e. the "twin-over-many-runtimes" company slot is **still open.**

## E. What this catalogue means for XNOBrain

1. **Demand is proven and broad.** 262 real, sourced stories across 15 domains, most
   from individuals and small teams — the *self-hosted personal/expert agent* market is
   live, not speculative.
2. **Every commodity engine is already saturated** by the community (memory kernels,
   dashboards, connectors, routing, sandboxes). **Do not build these** — adopt,
   partner, or ride the standard (agentskills.io, Atlas, Merxex, EAS). Confirms
   03/08: rent the commodity.
3. **The moat is confirmed empty from the inside.** The community is *actively building
   audit, approval, compliance, and trust primitives* (#51, #54, #154, #190) — but
   **not one measures whether a twin faithfully reproduces a named human.** Engine 07
   is whitespace even among the people closest to the tech.
4. **Cloning individual voice/judgment is already the killer use case** (#123, #141,
   #155, #150) — it just lacks certification, provenance, and royalties. That is
   precisely the Twin Terminal wedge, and it means **you're extending a proven behavior,
   not inventing demand.**
5. **Interoperate, don't reinvent the registry.** agentskills.io + Hermes Atlas are the
   incumbents of the "shelf." Your differentiation is **measured fidelity + expiry +
   royalty lineage** layered on top of (or bridging) that standard.
6. **Nous already monetizes hosting (Hermify).** Reinforces 03:
   don't compete on hosting; compete on certification.

