# Brain4All — OSS (Open-Core) System Specification

**Edition:** Open-source, self-hosted, single user
**License:** Open-core (wraps Hermes Agent — MIT, and 9router — MIT)
**Status:** shipping

---

## 1. System description

Brain4All OSS is a **self-hosted, single-user** application for building and running
AI agents on your own machine. It is a thin, product-grade layer over two open-source
engines:

- **Hermes Agent** (Nous Research, MIT) — the agent runtime. Each assistant is a
  Hermes *profile* with its own isolated state in a local SQLite `state.db`.
- **9router** (MIT) — the LLM router. It holds provider credentials locally and routes
  every model call; Brain4All forces the provider to `nine-router` so keys never leak
  into agent config.

The frontend is a React + TypeScript (Vite) single-page app; the backend is a Python
FastAPI service that mounts onto Hermes' own web server. **Everything runs locally** —
on the user's PC or a self-managed container. No managed/cloud service is contacted to
operate the product, and chat works offline once a provider is configured.

### Design principles
- **Local-first.** All data (profiles, conversations, tasks, usage) lives on the
  user's machine in per-profile SQLite. Nothing is sent to a Brain4All service.
- **Credentials stay put.** Provider keys live only in the local 9router store; the app
  never logs or returns them.
- **Open-core.** The OSS edition is fully functional standalone; enterprise capabilities
  are additive and live in a separate control plane.

---

## 2. Functional specification

### 2.1 Assistants (agents / profiles)
- Create, rename, and delete assistants; each is an isolated Hermes profile with its
  own `state.db`, skills, and configuration.
- Configure per-assistant model, blend, approval mode, and skill/memory write approvals.
- Test an assistant's configuration (connectivity + model reachability).
- Search and switch between assistants from the sidebar.

### 2.2 Chat & conversations
- Stream responses turn-by-turn from the local Hermes runtime.
- Maintain multiple conversations per assistant as tabs; create, rename, delete, and
  switch conversations.
- Fold intermediate tool-call steps into a collapsible "run" view; show only final
  answers as chat bubbles.
- Queue follow-up messages while a turn is streaming; reorder, edit, or remove queued
  messages.
- Copy, rate, and retry assistant responses.
- Reference workspace files inline with `@mention` autocomplete.

### 2.3 Model selection & blends
- Pick any model exposed by a connected provider from the in-composer model picker.
- Define **blends** — named model strategies in 9router (fallback / round-robin /
  fusion) — and select a blend as an assistant's "model".
- Blends are managed in Settings (create, rename, set strategy, delete).

### 2.4 Provider connections (9router)
- Connect **multiple** upstream providers/accounts (OAuth or API key) through 9router.
- Add multiple accounts per provider with priority ordering and active/inactive toggles.
- Test a provider/account's current availability from Settings.
- View per-connection quota/usage where the provider reports it.
- Keys are stored locally in 9router only.

### 2.5 Skills
- Browse a skill library; install skills into the local library.
- Enable/disable skills per assistant; apply skills across multiple assistants.
- Search, filter, and page through skills; view descriptions inline.

### 2.6 Kanban (local task board)
- A per-installation task board with four columns: **Backlog, Todo, In Progress, Done**
  (plus an Archived view).
- Create tasks, assign them to an assistant, attach the assistant's enabled skills,
  set priority, tags, and dependencies.
- Track live task activity, progress on running tasks, and blocked/summary signals.
- Schedule recurring or one-off task runs (local cron).

### 2.7 Agent teams (multi-agent workflows)
- Compose a team with an orchestrator and worker assistants.
- Run a task in parallel or as a dependency-aware workflow (DAG).
- View run history, per-run chips (workers, tokens, cost, duration), and the
  orchestrator's synthesized summary.

### 2.8 Usage analytics (local, advisory)
- Grafana-style view: select all or a subset of assistants, pick a time range and
  bucket (hour/day/week/month).
- See totals (tokens in/out, cost, sessions, API calls), usage over time, and a
  per-model breakdown.
- Set **advisory** per-assistant monthly budgets — soft warnings only; spend is **never
  blocked** locally.
- Read-only and computed on demand from each profile's local `state.db`.

### 2.9 Workspace & files
- Per-assistant workspace directory browsable in the app.
- Upload files or whole folders (drag-and-drop) into the workspace.
- Open workspace files referenced by the assistant.

### 2.10 Portable profiles
- Export one or more assistants as a portable `.zip` bundle (credentials, logs, host
  paths, and device identity are excluded).
- Preview an import (ID collisions, paused crons, quarantined code) before applying.
- Import a bundle into the local installation.

### 2.11 Advanced integrations (MCP)
- Configure MCP servers per assistant, isolated in the assistant's profile
  (`profiles/<id>/mcp.json`).

### 2.12 Runtime
- Local runtime/sandbox status and provisioning surfaced in Settings.
- The installation reports itself as **Local**; no managed service is contacted for
  chat.

### 2.13 Localization & theming
- UI in multiple languages (e.g. English, Tiếng Việt).
- Light / dark / auto theme.

---

## 3. Non-functional requirements

- **Deployment:** single self-hosted instance (PC or a container the user runs).
- **Tenancy:** single user; one local identity, no accounts or sign-in.
- **Storage:** local SQLite per profile; no external database.
- **Connectivity:** operates without any Brain4All service; only reaches the configured
  LLM providers.
- **Privacy:** conversation content, prompts, responses, and files never leave the
  machine; no product telemetry.
- **Security:** provider credentials confined to 9router; never logged or returned by
  the API.
- **Extensibility:** capabilities added via skills and MCP; provider set extended via
  9router.

---

## 4. OSS vs Enterprise (summary)

The OSS edition is complete for one user on one machine. The Enterprise edition adds a
separate **control plane** (multi-user orgs, central usage, fleet, governance, voice,
cross-account boards) **without changing how the local runtime works** — see
[`enterprise.md`](enterprise.md) and the full checklist in
[`compare-features.md`](compare-features.md).

| Area | OSS | Enterprise adds |
|---|---|---|
| Users | Single, no sign-in | Orgs, accounts, SSO, RBAC |
| Usage data | Local, advisory | Central DB, enforced budgets, dashboards, export |
| Fleet | One local runtime | Managed Incus/PC fleet, versions, rollout |
| Voice I/O | Not surfaced | Central voice gateway, org keys, metered |
| Kanban | Local, per-user | + Cross-account **Enterprise boards** |
| Governance | — | Model policy, audit log, retention, backup/DR |
| Hosting | Self-hosted only | Self-hosted **or** managed cloud (incl. air-gapped) |
