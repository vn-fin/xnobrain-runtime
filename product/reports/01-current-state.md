# 01 · Current State — What Brain4All Is Today

> Scope: sourced from the repository itself (`AGENTS.md`, `README.md`,
> `docs/architecture.md`, `docs/enterprise-extension.md`,
> `docs/repository-ownership.md`, `docs/project-summary.md`). No external claims here —
> this is the ground truth of the code as it stands on 2026-07-24.

## 1. One-sentence definition

Brain4All is a **self-hosted React + FastAPI workspace that wraps Nous Research's
open-source Hermes Agent** and adds a multi-provider LLM router (referred to
internally as **9router**), packaged as a downloadable product with an optional
proprietary enterprise control plane.

It is, today, an **agent workspace / "studio"** — not yet a marketplace, not yet a
certification terminal. The Twin Terminal vision (see
[02-vision-twin-terminal.md](02-vision-twin-terminal.md)) is the destination; this
file is the starting point.

## 2. Runtime architecture (as built)

```
browser → Traefik → React UI
                 → FastAPI :8642  (Hermes native routes + Brain4All routes)
                        → services → repositories → atomic profile/config files
                        → integrations → Hermes CLI/core
                        → integrations → 9router :20128 → LLM providers
```

Key facts pulled from `docs/architecture.md`:

- The runtime container starts **exactly two processes**: FastAPI on `8642` and
  9router on `20128`. React is served through Traefik.
- It is a **Python modular monolith** layered onto the original Hermes CLI FastAPI
  app. Route assembly is centralized in `brain4all/routes/setup.py`.
- **No application database.** State is atomic files under `DATA_DIR`. Hermes keeps
  its own profile-local `state.db` for native session history (an upstream file, not
  a Brain4All schema).
- Clean layering: **handlers** own HTTP/SSE translation, **services** own rules,
  **repositories** own atomic files, **integrations** adapt the Hermes CLI and
  9router, **models** are Pydantic.
- **No managed control-plane dependency, login, or API proxy** in the OSS build.
  OpenTelemetry is local-only and off by default.

### Code map (`brain4all/`)

| Layer | Files | Responsibility |
|---|---|---|
| `handlers/` | `api.py` | HTTP + SSE translation |
| `services/` | `kanban.py`, `platform.py` | Business rules |
| `repositories/` | `files.py` | Atomic file persistence |
| `integrations/` | `hermes.py`, `nine_router.py`, `runtime.py`, `kanban.py`, `config.py` | Adapters for Hermes CLI + 9router |
| `models/` | `api.py` | Pydantic contracts (drive `/api/brain/swagger_docs`, `/api/brain/openapi.json`) |
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
| `brain4all` (this, **public/OSS**) | React image + combined FastAPI/Hermes/9router image | Profile isolation, safe paths, snapshots, conversations, Hermes invocation, 9router delegation, quota middleware, service-level enforcement |
| `brain4all-enterprise` (**private**) | Enterprise API + managed/Incus cloud packaging | Auth, tenant/plan resolution, billing entitlements, distributed quota, RBAC, audit, secret management, managed orchestration, telemetry retention |

Rules that protect the model:

- The OSS repo must stay a **complete downloadable product** — its Compose file
  pulls only public images and **never** requires a private image or cloud account.
- **Dependency is one-directional**: enterprise may pull the public runtime image;
  the OSS deployment never pulls the enterprise image.
- Enterprise is a **thin private composition**, not a fork. It pins a released
  Brain4All module + runtime image and implements a `pkg/edition.Policy` contract
  (`-1` = unlimited).
- Editions: **self-hosted Free** (file-only), **Cloud Free / Personal Pro /
  Enterprise** (shared private PostgreSQL control plane). Enterprise adds orgs, RBAC,
  SSO, audit, contract entitlements.
- **Historical note:** an earlier Go/Fiber + PostgreSQL multi-server plan was
  **retired** (`docs/implementation/README.md`); current OSS work targets the single
  Python package. The Go story now lives on the **enterprise control-plane** side.

### ⚠️ Language reality-check vs. the stated plan

The stated intention is to open-source part of the project (described as "Python + Go")
and keep an enterprise edition in Go. The repository's current reality is:

- **OSS = Python** (FastAPI monolith) + React/TypeScript frontend. The Go/Postgres
  path was explicitly retired in the OSS repo.
- **Enterprise = Go** (the `pkg/edition.Policy` control plane, `github.com/vn-fin/brain4all/`
  module path, managed orchestration).

So "Python + Go open source" is **not** what the code does today. Decide
deliberately (see the roadmap file): either (a) keep OSS Python-only and Go strictly
enterprise, or (b) reintroduce a Go component into OSS (e.g. a lightweight local
daemon/CLI) if you truly want a Go OSS surface. The cleanest story is **(a)**.

## 5. Honest assessment of the gap to the vision

| Vision needs (Twin Terminal) | Exists today? |
|---|---|
| Agent runtime, memory, skills, MCP, sandboxes | ✅ via Hermes |
| Multi-provider routing | ✅ via 9router |
| Self-host + cloud + protected enterprise source | ✅ designed (open-core split) |
| Skill/twin **authoring workshop** | 🟡 partial (skills + Kanban editor) |
| **Fidelity-certification gate** (the moat) | ❌ not started |
| Identity / anti-impersonation | ❌ not started |
| Registry / shelf with expiring badges | ❌ not started |
| Lineage, royalty, cross-org billing | ❌ enterprise billing exists; royalty/lineage do not |
| Drift monitoring → re-certification | ❌ not started |

**Takeaway:** the "commodity" engines (brain, execution, connection, and most of
orchestration) are largely **rented or already built**. Every engine the vision
marks as *the moat* — **certification, identity, improvement loop, commerce/royalty**
— is greenfield. That is the correct place to spend, and it is where the roadmap
should concentrate.
