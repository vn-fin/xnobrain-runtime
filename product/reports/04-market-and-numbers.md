# 04 · Market & Numbers

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
precisely engine 07. See [05-startup-landscape.md](05-startup-landscape.md) and
[06-certification-moat.md](06-certification-moat.md).

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
> ([multiple firms](#1-the-agentic-ai-market-your-category)). Every major platform has
> launched an agent marketplace — Salesforce's reached **18,500 customers in under a
> year** — but marketplaces without verification commodify (GPT Store: ~**$0.03 per
> conversation**). Expert-cloning is being funded fast (**Delphi $16M/Sequoia**,
> **Cloneable 100× ARR**) yet **nobody certifies that a clone faithfully reproduces
> the named human** — even as AI-evaluation adoption is set to triple to **60% of eng
> teams by 2028**. Brain4All owns that gap: the certification gate for verified expert
> twins.
