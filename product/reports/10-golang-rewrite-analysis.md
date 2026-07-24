# 10 · Build-vs-Rewrite — Should We Reimplement Hermes in Go?

> Decision analysis for: "clone Hermes and reimplement it in Golang (skip the CLI,
> keep the important features, use PostgreSQL), like goclaw did for OpenClaw." Honest,
> opinionated. TL;DR up top, reasoning below, time estimates at the end.

## TL;DR (the recommendation)

**Don't full-rewrite Hermes in Go. It's legal but strategically wrong.** Three reasons,
each sufficient on its own:

1. **The agent loop is LLM-bound — Go gives ~0 user-perceived speedup there.** Wall-clock
   is dominated by model inference and tool I/O, not Python execution.
2. **A fork forfeits Hermes's upstream velocity forever** (370+ contributors, weekly
   releases) — and **breaks the Python skill/plugin ecosystem** (SKILL.md + `plugin.py`
   hooks + agentskills.io), which is a big part of Hermes's value.
3. **Every month spent rewriting a commodity runtime is a month not spent on Engine 07
   (certification)** — the only thing that's actually defensible.

**Do this instead (hybrid):** keep Python Hermes as the **agent runtime** (vendored,
orchestrated as a dependency). Build **everything new and everything that benefits from
Go/Postgres** — the certification engine, registry, identity, royalty/lineage, the
multi-tenant control plane, and if needed a Go gateway/router service — **in Go +
PostgreSQL**. That is exactly where Go and Postgres genuinely help, and it's where your
moat lives. It's also, almost exactly, the architecture your repo already specifies
(see [01-current-state.md](01-current-state.md) §4).

You get **Postgres, Go, single-binary control plane, and multi-tenant concurrency**
without rewriting the agent — because you put them at the layer that needs them.

---

## 1. Is it legal? Yes.

Hermes is **MIT** ([03-core-tech-and-competitors.md](03-core-tech-and-competitors.md) §1).
You may fork it, reimplement it in any language, keep your version closed, and
commercialize it. Preserve the copyright/notice. **There is no legal blocker** — so the
decision is purely strategic/engineering.

## 2. Reframe the question

This is not "Go vs Python." It's **"where do we spend a scarce founding team's
months?"** You have one certification moat to build and fast-moving competitors
([05-startup-landscape.md](05-startup-landscape.md)). A rewrite is a multi-month
investment in a **commodity engine** (the vision itself marks the runtime grey — "never
a moat"). The right question for each component is: *does rewriting it in Go create
defensible value, or just re-create something free that improves without me?*

## 3. Three technical truths that decide it

### 3a. The agent loop won't get faster in Go
An agent turn = build prompt → **call the LLM (seconds)** → run a tool (**network/disk
I/O**) → repeat. The Python interpreter overhead is a rounding error next to
multi-second model calls and tool latency. Rewriting the *loop* in Go optimizes the 1%
that isn't the bottleneck. **User-perceived speedup ≈ 0.** (Story #13 measured "73% of
every API call is fixed overhead" — that overhead is prompt/context assembly and
network, not language runtime.)

**Where Go *does* win** — and it's real:
- **I/O concurrency at scale**: multiplexing 13+ messaging gateways, many cron jobs,
  many parallel sub-agents/tenants. Goroutines beat Python async on footprint here.
- **Single static binary**: far nicer to ship to an enterprise's server than a Python
  env (this is a genuine on-prem deployment win).
- **Memory footprint / startup**: the goclaw pitch — sub-50MB, <100ms start,
  multi-tenant isolation.

Notice: **all of those are deployment / gateway / control-plane properties, not
agent-intelligence properties.** That's the tell for *where* Go belongs.

### 3b. A fork forfeits upstream — and breaks the plugin ecosystem
- Hermes ships features continuously (**370+ contributors, hundreds of closed issues,
  frequent 0.x releases**, per [03](03-core-tech-and-competitors.md)). A point-in-time
  Go fork **freezes there and chases a moving target forever** — new models, new
  gateways, new sandboxes, security fixes all land in Python upstream, not your fork.
- **The skill/plugin ecosystem is Python.** Hermes skills are `SKILL.md` **plus
  `plugin.py`** and Python hooks (`pre_llm_call`, etc. — see stories #50, #190, #207).
  The whole **agentskills.io** ecosystem, `awesome-hermes-agent`, and Hermes Atlas are
  built on this. A Go runtime **cannot run those plugins** without embedding a Python
  interpreter (killing the single-binary benefit) or defining a new plugin ABI (and
  then the ecosystem doesn't target it). **You'd inherit Hermes's name-recognition but
  lose its ecosystem** — the worst of both.

### 3c. Opportunity cost is the real price
The rewrite's true cost isn't the code — it's **the certification engine you didn't
build during those months.** Neighbors are funded and fast (Delphi/Sequoia, Cloneable
100× ARR). First-mover on the *fidelity standard* is your defensibility, not runtime
ownership.

## 4. What the goclaw precedent actually proves

There are **multiple** Go rewrites of OpenClaw — [sausheong/goclaw](https://github.com/sausheong/goclaw)
(single binary, sub-50MB, <100ms start), [nextlevelbuilder/GoClaw](https://github.com/nextlevelbuilder/goclaw)
(multi-tenant isolation, 5-layer security, native concurrency), [LiteClaw](https://github.com/liteclaw/liteclaw),
[a3tai/openclaw-go](https://github.com/a3tai/openclaw-go) (Go client lib). Read them
carefully:

- **Their headline value is footprint + single-binary + multi-tenant + concurrency —
  never "smarter agent."** That confirms §3a: Go wins on *deployment/ops*, not
  intelligence. These are precisely the properties an **enterprise control plane / cloud
  runtime** needs — i.e. your Go layer, not your agent core.
- **OpenClaw is TypeScript and config-first** — a *simpler, smaller* surface than
  Hermes (which adds a self-improvement loop, 13 gateways, 5+ sandboxes, a richer
  3-layer memory, Python plugins). Porting OpenClaw ≠ porting Hermes. **A Hermes rewrite
  is materially bigger.**
- These are mostly **community/solo projects** optimizing *their own* deployment — not
  companies whose moat depends on the runtime. Different goal from yours.

So goclaw is evidence that **"rebuild the gateway/deployment layer in Go" is a good
idea** — which supports the hybrid, not a full Hermes rewrite.

## 5. You can have PostgreSQL *without* rewriting Hermes

Wanting Postgres is not a reason to rewrite the agent. Put Postgres where it earns its
keep:

| Data | Store | Why |
|---|---|---|
| Per-agent session history, memory, skills | **SQLite FTS5 (Hermes native)** | single-node, local, already works; Postgres adds nothing here |
| **Certification badges, coverage maps, expiry, drift** | **PostgreSQL (your Go service)** | the moat's data — queryable, multi-tenant |
| **Registry / shelf, lineage, royalties** | **PostgreSQL (Go)** | cross-org, transactional, concurrent |
| Tenants, RBAC, SSO, billing, quota reservations | **PostgreSQL (Go control plane)** | already the enterprise design ([01](01-current-state.md) §4) |

Postgres belongs at the **control-plane + registry + certification** layer — all
**new** code you're writing in Go anyway. Hermes keeps SQLite for what SQLite is good
at. No rewrite required to "use Postgres."

## 6. The options, compared

| Option | What | Speedup where it matters | Keeps upstream + plugins | Postgres | Effort | Verdict |
|---|---|---|---|---|---|---|
| **A. Full Go rewrite of Hermes** | reimplement the whole agent | ~none (LLM-bound) | ❌ loses both | ✅ (but you rebuilt everything for it) | **very high** | ❌ Don't |
| **B. Extend Python Hermes only** | current OSS design | n/a | ✅ | via enterprise only | low | ✅ but limited — control plane still needs Go |
| **C. Hybrid: Hermes runtime + Go/Postgres moat & control plane** | Python agent, Go for certification/registry/identity/royalty/control plane | ✅ where Go helps | ✅ | ✅ where it matters | medium | ✅✅ **Recommended** |
| **D. Selective Go services** | + reimplement only hot paths (gateway mux, router) in Go calling Hermes | ✅ targeted | ✅ (Hermes still core) | ✅ | medium-high | ✅ if/when a gateway or router bottleneck is *measured* |

**Recommendation: C now, add D surgically only when you measure a real bottleneck.**

## 7. Recommended architecture (the hybrid)

```
                    ┌──────────────────────────────────────────────┐
   customer /       │  GO CONTROL PLANE + MOAT  (Postgres)          │  ← your IP, Go, single binary
   enterprise ─────▶│  certification engine (07) · registry/shelf   │     multi-tenant, concurrent
                    │  identity (05) · royalty & lineage (09)        │
                    │  RBAC/SSO/billing/quota · (opt) gateway mux    │
                    └───────────────┬──────────────────────────────┘
                                    │ orchestrates / calls (HTTP/CLI/embedded)
                    ┌───────────────▼──────────────────────────────┐
                    │  HERMES AGENT RUNTIME (Python, vendored)      │  ← rented commodity
                    │  agent loop · memory (SQLite) · skills/plugins │     keep upstream + ecosystem
                    │  MCP · sandboxes · gateways · provider/router  │
                    └──────────────────────────────────────────────┘
```

- **Treat Hermes as a vendored dependency**, pinned to a release, orchestrated by your
  Go layer (via its API/CLI or a thin service boundary). You still get upstream by
  bumping the pin.
- **Build the moat in Go + Postgres** — that's the code that's yours, defensible, and
  genuinely benefits from Go's concurrency and Postgres's transactions.
- This is **your existing enterprise split, made explicit** ([01](01-current-state.md)
  §4): OSS Python runtime + private Go `brain4all-enterprise` control plane. The only
  new idea here is: *also put certification/registry/royalty (the moat) in that Go
  layer, backed by Postgres* — which you were going to build new anyway.

> ⚠️ Note: your `AGENTS.md` currently **forbids adding Go/Postgres to the OSS repo** and
> says "extend from brain4all instead of copying or forking Hermes." That rule is
> correct for the **OSS** repo. The hybrid keeps that rule intact — Go/Postgres live in
> the **enterprise** repo, not the OSS one. If you ever want a Go OSS surface, scope it
> as a *small new component* (e.g. a single-binary installer/daemon), not a Hermes fork.

## 8. Time estimates (if you insist on a Go rewrite)

**Heavily caveated — these are ranges, not promises. Assumes a small strong team + AI
assistance. "AI copies it" is not a transpile: Python→Go is a paradigm shift (dynamic→
static types, goroutines, explicit errors, different SDKs per gateway/sandbox), and the
real cost is *parity testing*, not first-draft code.**

| Scope | What's included | Estimate | Notes |
|---|---|---|---|
| **Thin MVP** | agent loop + 3-layer memory (Postgres) + skills-as-data + 1 gateway (e.g. Telegram) + core tools (shell/file/web) + one provider via router; single binary | **~1–2.5 months** | "demos work," not robust; no plugin compat |
| **Broad parity** | most of MCP (both ways), several gateways, ≥2 sandboxes, cron, approval gate, self-improvement loop, hardening | **~6–12+ months** | and it's a *moving target* — upstream keeps shipping |
| **True full parity** | everything Hermes does today **including the Python plugin/skill ecosystem** | **impractical** | requires embedding Python or an ABI; you'd be chasing forever |
| **Maintenance tax (ongoing)** | tracking upstream Hermes features/security | **~1–2 FTE, permanent** | the cost that never shows up in the first estimate |

**The honest framing:** even the optimistic "thin MVP in ~6 weeks" produces a *worse*
agent than `pip install`-ing today's Hermes (no plugins, fewer gateways, behind on
models) — and consumes the exact weeks you need for certification. The full version is
never "done."

## 9. When a Go rewrite *would* be justified (the honest counter-case)

Reconsider only if **all** of these become true — none are today:
- You've **measured** a real bottleneck in the runtime that isn't the LLM (e.g. the
  gateway multiplexer melts under thousands of concurrent tenants) — and even then,
  reimplement *that service* (Option D), not the agent.
- Hermes's **upstream stalls or its license changes** (MIT → restrictive), removing the
  "free improvements" argument.
- You've **already shipped the certification moat** and the runtime is now your
  constraint on growth.
- Your team is Go-only and maintaining vendored Python is a proven, recurring drag
  *after* you've tried the hybrid.

Until then: **rent the runtime, own the moat.**

## 10. One-paragraph answer to your exact question

Cloning Hermes into Go is legal (MIT) but rewrites a **commodity** whose speed is
capped by the LLM, not the language — so you'd spend 6–12+ months (and a permanent
maintenance tax) to get a *slower-to-improve, plugin-incompatible* copy while your
competitors build the moat. The goclaw examples you cited actually prove the smart move:
their wins are **single-binary, low-memory, multi-tenant** — i.e. **deployment/control-
plane** wins. So do exactly that: keep Python Hermes as the agent runtime, and build the
**certification engine, registry, identity, royalty, and multi-tenant control plane in
Go + PostgreSQL** (Option C). You get Go, Postgres, single-binary ops, and concurrency
where they matter — and you keep Hermes's upstream and ecosystem for free.
