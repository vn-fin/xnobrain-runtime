# Integration Architecture & Delivery Plan

This section documents how the current system integrates the Hermes runtime and the LLM
router, the performance characteristics of that integration, how profiles (per-agent
isolation) work, and the recommended target architecture for the commercial product.

## Current integration: one process extending Hermes

The open-source application does not run a separate server that calls Hermes over the
network. It imports Hermes's own web-server application and registers its compatibility
routes onto it, then serves the combined application on port 8642. In effect, the running
process is Hermes's web server plus the Brain4All management surface, in a single Python
process sharing one virtual environment. The LLM router runs as a separate process on port
20128 and is reached over HTTP.

Because Brain4All shares the process and environment, it integrates with Hermes through
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
