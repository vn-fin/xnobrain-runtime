# 03 · Core Tech & Direct Competitors

> What you're building on (Hermes + a router), what it can and can't do, its license
> implications, and the open-source runtimes you'll be compared to. All external
> claims are linked and dated.

## 1. Hermes Agent (Nous Research) — your engine

**What it is.** An open-source, **MIT-licensed** self-improving AI agent, launched
**February 2026**. It is the exact thing Brain4All wraps (the repo's "original Hermes
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
  is the Hermes approval core Brain4All preserves.

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
Consequences for Brain4All:

- ✅ You can legally build a **proprietary enterprise/cloud layer** on top of Hermes
  and keep that layer closed. This is exactly what the open-core split assumes.
- ✅ You are **not** forced to open-source your own code (unlike AGPL/GPL).
- ⚠️ The flip side: MIT gives *you* no protection either — anyone (including a cloud
  hyperscaler or Nous themselves) can also wrap Hermes. **Your moat cannot be "we
  wrapped Hermes."** It must be the certification gate, the craft data, identity, and
  royalty lineage (engines 05–07, 09). This is consistent with the vision doc.
- 📌 **Action:** keep a clean attribution/NOTICE file for Hermes and the router, and
  a dependency-license inventory, before public distribution. Your own OSS repo still
  needs a license chosen — the README literally says *"Choose and add a license
  before public distribution."* See [07-business-model-and-licensing.md](07-business-model-and-licensing.md).

## 2. The router ("9router") and the routing landscape

Brain4All's "9router" is an LLM router (one process on `:20128`) that routes to
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

**How it overlaps and differs from Hermes/Brain4All:**

| | Hermes / Brain4All | OpenClaw |
|---|---|---|
| Config model | Skills + profiles + UI | `SOUL.md` config-first |
| Memory | MEMORY.md + FTS5 + skills | markdown + SQLite |
| Distribution | messaging + web workspace | messaging gateway |
| Self-improvement | ✅ writes its own skills | limited |
| **Certification / twin fidelity** | your planned moat | ❌ none |

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
   cloud/enterprise) — exactly your plan. See
   [07-business-model-and-licensing.md](07-business-model-and-licensing.md).
2. **Everyone competes on "build agents." Nobody competes on "certify that this
   agent faithfully reproduces a specific named human expert."** That is the sentence
   that differentiates Brain4All from this entire list.
