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
