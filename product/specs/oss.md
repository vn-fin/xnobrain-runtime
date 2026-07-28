# Brain4All — OSS (Open-Core) System Specification

**Edition:** Open-source, self-hosted, single user — tiers **Free** and **Pro**
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
- **No metering on your own hardware.** Nothing in §2 is quota-limited, counted, or gated
  by an account on a self-hosted install. A signed-out install has the complete
  single-user product. *(Brain4All Cloud runs the same build on our servers and does size
  these resources by plan — a hosting cost, not a feature paywall. See
  [`plans.md`](plans.md) §7.)*

### Free and Pro are the same build

There is one OSS runtime. **Free** and **Pro** are the same application on the same
machine; a Pro subscription unlocks the three capabilities that require a Brain4All
service (§3):

| | Free | Pro |
|---|---|---|
| Everything in §2 | ✅ unlimited | ✅ unlimited |
| Skills | Built-in system library only | **+ marketplace: install & publish** |
| Speech-to-text | ⛔ | **✅** |
| Skill & memory snapshot versions | Local only, no history | **+ hosted version history & restore** |
| Sign-in | Not required | Required (subscription) |

No other capability, and no resource count — agents, conversations, providers, accounts
per provider, blends, teams, boards, channels, cron jobs, MCP servers — differs between
the two on a self-hosted install.

**Deployment is a separate axis.** This spec describes the self-hosted runtime. The same
build also runs as **Brain4All Cloud** (Cloud Free / Cloud Pro / Cloud Pro Max) on our
servers, where CPU, RAM, agent count, run-minutes, provider connections, and retention are
sized by plan because Brain4All pays for the hardware. Cloud Free has the Free capability
set; Cloud Pro and Pro Max have the Pro capability set. Packaging, limits, and
enforcement: [`plans.md`](plans.md).

---

## 2. Functional specification — the standalone product

Everything in this section is available to **every** standalone user, Free and Pro alike,
without limit and without an account.

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

### 2.3 Model selection & blends (combos)
- Pick any model exposed by a connected provider from the in-composer model picker.
- Define **blends** (model combos) — named model strategies in 9router (fallback /
  round-robin / fusion) — and select a blend as an assistant's "model".
- Blends are managed in Settings (create, rename, set strategy, delete).
- **No tier limits** the number of blends or the strategies available.

### 2.4 Provider connections (9router)
- Connect **every supported** upstream provider (OAuth or API key) through 9router.
- Add **multiple accounts per provider** with priority ordering and active/inactive
  toggles; requests rotate across active accounts by priority.
- Test a provider/account's current availability from Settings.
- View per-connection quota/usage where the provider reports it.
- Keys are stored locally in 9router only.
- **No tier limits** the number of providers, connection types, or accounts per provider.
  This is identical in Free, Pro, and Enterprise.

### 2.5 Skills (built-in system library)
- Use the **built-in system skill library** bundled with the runtime.
- Enable/disable skills per assistant; apply a skill across multiple assistants.
- Search, filter, and page through the library; view descriptions inline.
- Browse the public marketplace catalog read-only. *Installing from the marketplace and
  publishing to it are Pro capabilities — see §3.1.*

### 2.6 Message channels (per agent)
- Connect an assistant to messaging platforms — **Telegram, Discord, Slack, WhatsApp,
  Signal**, and the other channels the Hermes gateway supports — so the user can talk to
  their agent from a phone or an existing chat app.
- Channel enablement and credentials are **per assistant** (per Hermes profile).
- Enable/disable a channel, set/rotate/clear its credentials, run guided onboarding
  (QR or bot-token pairing), and see live connection status.
- Manage the local gateway lifecycle: start, stop, restart, drain.
- Bot tokens live in the assistant's profile only; they never appear in API responses,
  logs, traces, or portable bundles.
- *Voice notes arriving on a channel are transcribed only with Pro (§3.2).*

### 2.7 Kanban (local task board)
- A per-installation task board with four columns: **Backlog, Todo, In Progress, Done**
  (plus an Archived view).
- Create tasks, assign them to an assistant, attach the assistant's enabled skills,
  set priority, tags, and dependencies.
- Track live task activity, progress on running tasks, and blocked/summary signals.

### 2.8 Scheduling & cron
- Schedule recurring or one-off assistant runs with a local cron scheduler.
- Create, edit, pause, trigger, and inspect run history for each job.
- Instantiate jobs from blueprints, and attach **delivery targets** — send the result to
  a message channel (§2.6), an email, the Kanban board, or a workspace file.
- Scheduling is local: it keeps running with no network and no account.

### 2.9 Agent teams (multi-agent workflows)
- Compose a team with an orchestrator and worker assistants.
- Run a task in parallel or as a dependency-aware workflow (DAG).
- View run history, per-run chips (workers, tokens, cost, duration), and the
  orchestrator's synthesized summary.
- **Available in Free.** Agent teams are agents cooperating on one machine — they are a
  standalone capability, not a business feature. Enterprise adds teams that span
  *accounts* (different people's runtimes), which is the business capability.

### 2.10 Usage analytics (local, advisory)
- Grafana-style view: select all or a subset of assistants, pick a time range and
  bucket (hour/day/week/month).
- See totals (tokens in/out, cost, sessions, API calls), usage over time, and a
  per-model breakdown.
- Set **advisory** per-assistant monthly budgets — soft warnings only; spend is **never
  blocked** locally.
- Read-only and computed on demand from each profile's local `state.db`.

### 2.11 Workspace & files
- Per-assistant workspace directory browsable in the app.
- Upload files or whole folders (drag-and-drop) into the workspace.
- Open workspace files referenced by the assistant.

### 2.12 Portable profiles
- Export one or more assistants as a portable `.zip` bundle (credentials, logs, host
  paths, and device identity are excluded).
- Preview an import (ID collisions, paused crons, quarantined code) before applying.
- Import a bundle into the local installation.

### 2.13 Advanced integrations (MCP)
- Configure MCP servers per assistant, isolated in the assistant's profile
  (`profiles/<id>/mcp.json`).

### 2.14 Runtime
- Local runtime/sandbox status and provisioning surfaced in Settings.
- The installation reports itself as **Local**; no managed service is contacted for
  chat.

### 2.15 Localization & theming
- UI in multiple languages (e.g. English, Tiếng Việt).
- Light / dark / auto theme.

---

## 3. Pro capabilities *(paid, still single-user)*

The only capabilities gated behind a subscription. Each requires a signed-in account
because each depends on a Brain4All-operated service — a marketplace, a transcription
service, and hosted storage — and each therefore carries a marginal cost we must cover.
Everything in §2 keeps working signed-out and offline; a Pro user who signs out loses
only these three.

### 3.1 Skill marketplace
- **Install** skills from the public marketplace into the local library — free and
  premium listings — alongside the built-in system skills.
- **Publish** skills the user built, with versioning, description, and listing
  management; earn a **creator revenue share** on premium sales.
- Installed marketplace code passes the same safety gate as imported portable profiles:
  dependency/malware scan → explicit content-hash-bound approval → only then enable.
- Free users can browse the same catalog but cannot install or publish.
- Evaluation, scorecards, and badging: [`skill-evaluation.md`](skill-evaluation.md).

### 3.2 Speech-to-text
- Dictate to an assistant from the composer (microphone capture → transcript → message).
- Transcribe **voice notes arriving over a message channel** (§2.6) into text messages.
- Per-assistant voice settings (enabled, provider, language).
- Transcription runs either against the Brain4All-hosted STT service under a monthly Pro
  allowance, or against the user's own provider key (unmetered).
- Provider keys stay server-side in the local runtime and are never returned or logged.
- ⛔ Not available in Free — the UI surface is present but entitlement-gated (403).

### 3.3 Skill & memory snapshot versions
Brain4All keeps a **version history** of what makes an assistant *itself* — its skills and
its memory — stored on Brain4All's servers so it survives a lost disk, a bad edit, or a
new machine.

- **What is versioned, per assistant:**
  - **Skills** — the installed set, their versions, per-assistant enable/disable state,
    and skill configuration.
  - **Memory** — the assistant's memory store as of the snapshot.
- **Snapshots:** automatic on a schedule and before any risky mutation (the runtime
  already snapshots locally before persistence-promising writes); plus manual, named
  snapshots ("before I rewrote its instructions").
- **History:** browse versions with timestamps and origin, **diff** two versions (skills
  added/removed/upgraded; memory entries added/changed/removed), and **restore** an
  assistant to any retained version — skills only, memory only, or both.
- **Portability:** restore a snapshot onto a **different machine or a fresh install**,
  which is how a Pro user moves between devices or recovers from hardware loss.
- **Encryption (non-negotiable):** snapshots are encrypted **client-side with a key the
  user holds** before upload. Brain4All stores ciphertext and cannot read memory content.
  This preserves the privacy floor — losing the key means losing the snapshots, and the
  UI must say so at setup.
- **Free:** local only. The runtime still snapshots before risky writes and the user can
  still export a portable bundle by hand (§2.12) — but no retained version history, no
  diff/restore UI, and nothing stored off the device.
- **Quota:** retained versions and total snapshot storage are capped per plan
  ([`plans.md`](plans.md) §7.2); the oldest versions age out first, and a manually named
  snapshot is never aged out ahead of an automatic one.

---

## 4. Non-functional requirements

- **Deployment:** single self-hosted instance (PC or a container the user runs).
- **Tenancy:** single user; one local identity. Sign-in is optional and unlocks only §3.
- **Storage:** local SQLite per profile; no external database.
- **Connectivity:** operates without any Brain4All service; only reaches the configured
  LLM providers. A Pro install that loses connectivity keeps every §2 capability and
  loses only marketplace access, hosted transcription, and snapshot upload (snapshots
  spool locally and upload on reconnect).
- **Metering:** nothing in §2 is counted, capped, or plan-gated; local entitlement checks
  always resolve unlimited.
- **Privacy:** conversation content, prompts, responses, and files never leave the
  machine; no product telemetry. The one thing that may leave, on Pro and only by explicit
  opt-in, is a **client-side-encrypted** skill/memory snapshot (§3.3) — stored as
  ciphertext we cannot read.
- **Security:** provider credentials confined to 9router; channel bot tokens confined to
  the assistant's profile; never logged or returned by the API.
- **Extensibility:** capabilities added via skills, marketplace skills (Pro), message
  channels, and MCP; provider set extended via 9router.

---

## 5. OSS vs Enterprise (summary)

The OSS edition is complete for one person on one machine. The Enterprise edition adds a
separate **control plane** for *businesses* — multiple people, central visibility, and
governance — **without changing how the local runtime works**. See
[`enterprise.md`](enterprise.md), the tier packaging in [`plans.md`](plans.md), and the
full checklist in [`compare-features.md`](compare-features.md).

| Area | OSS (Free & Pro) | Enterprise adds |
|---|---|---|
| Users | One person, personal tenant | Orgs, member accounts, SSO, RBAC, SCIM |
| Usage data | Local, advisory budgets | Central DB, **enforced** budgets, dashboards, export |
| Agent teams | ✅ within one install | + teams spanning **accounts** |
| Kanban | ✅ local, per-user | + cross-account **Enterprise boards** |
| Skills | Built-in (Free) / marketplace (Pro) | + **private org catalog** & approval workflow |
| Speech-to-text | Pro, per user | + org **voice gateway**, org-held keys, metered |
| Skill/memory snapshots | Pro, user-held key | + org retention policy & **key escrow** for recovery |
| Message channels | ✅ per agent, user-owned tokens | + org channel policy & approval |
| Provider keys | User-owned, local | Org-managed, read-only to the member |
| Fleet | One local runtime | Managed Incus/PC fleet, versions, staged rollout |
| Governance | — | Model/tool policy, audit log, retention, backup/DR |
| Hosting | Self-hosted only | Self-hosted **or** managed cloud (incl. air-gapped) |
