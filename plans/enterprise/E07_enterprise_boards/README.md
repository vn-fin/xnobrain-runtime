# E07 — Enterprise boards (cross-account Kanban)

**Priority:** P2 (after E01 control plane, E02 usage collection, E05 orgs/auth) ·
**Depends on:** E01 (device identity + command envelope), E02 (central DB + snapshot
reporting), E05 (orgs, RBAC), E03 (fleet — for dispatch to containers) ·
**Status:** design

Mockup: [`mockups/admin/enterprise-kanban.html`](../mockups/admin/enterprise-kanban.html)

## Goal

Give an organization a **single Kanban board it owns**, whose tasks can be assigned
across **accounts, agents, and agent-teams**, and coordinated centrally by an admin —
while every task still *executes on the assignee's own runtime* (Incus container or
PC) and no conversation content ever leaves that machine.

This is distinct from two things that already exist:
- **OSS local Kanban** (per profile, plans/007-era) — one member's own board, on their
  machine. Unchanged.
- **Admin › Workspaces › Kanban** (E-program, view-only) — a read-only *mirror* of one
  member's local board. Enterprise boards are **managed** (create/assign/move), not a
  mirror.

## Why it's an enterprise feature

Cross-account coordination inherently needs a party that sits above individual
machines: a board that Mai Anh, Duy Khanh and the Risk Team all contribute to cannot
live in any one person's local SQLite. The control plane already built for E01/E02 is
exactly that party — it has device identity, an outbound command channel, and a central
Postgres. Enterprise boards are a thin coordination layer on top; they do not weaken
the OSS "runs locally" guarantee, because **the board dispatches work to the local
runtime rather than executing anything centrally.**

## The core model

```
                      central control plane (Go + Postgres)
                      ┌─────────────────────────────────────────────┐
   admin creates ───► │ enterprise_boards / board_tasks             │
   & assigns tasks    │   assignee = {account | agent | team}       │
                      │   status, priority, deps, progress           │
                      └───────┬──────────────────────────▲──────────┘
              dispatch (device-command-v1)               │ status/progress
              outbound-only, per assignee's device       │ snapshot upserts (E02 style)
                      ▼                                   │
       member runtime (Incus container / PC)             │
         creates a LOCAL kanban task in the target       │
         agent's board → agent runs it locally ──────────┘
         (conversation content NEVER leaves the machine)
```

Four properties, each inherited from an existing frozen contract:

1. **Assign across accounts/agents/teams.** A `board_task` carries an `assignee_ref`
   that is one of: a member account (routed to their default agent), a specific agent
   (owner resolved via E05 membership + E03 device inventory), or an agent-team
   (fan-out to the team's workers on their owners' runtimes). RBAC (`boards.assign`)
   gates who may assign across members.
2. **Dispatch, don't execute.** Assignment emits a `board.task.dispatch` command on the
   **device-command-v1** envelope (outbound-only, at-least-once, idempotent by
   `task_id`). The member's runtime receives it, creates/updates a task in the target
   agent's **local** Kanban, and runs it with the local agent. The center never sees
   the prompt or the output.
3. **Status flows back as snapshots.** The runtime reports task status/progress the
   same way E02 reports usage: cumulative **snapshot upserts** keyed by `task_id`, so
   retries and offline catch-up are exactly-once. The board's columns
   (Backlog/Todo/In Progress/Done, matching the OSS `KANBAN_COLUMNS`) reflect the
   latest snapshot per task.
4. **Offline-safe.** A dispatch to an offline device spools in the command outbox
   (E01) and a status report spools in the usage/status outbox (E02); a laptop offline
   for a day reconciles correctly on reconnect. No double-execution (idempotent
   `task_id`), no double-count (idempotent snapshot).

## Privacy boundary (unchanged, restated)

What crosses the wire for a board task: `task_id`, title, description, tags, priority,
assignee ref, column/status, progress %, timestamps, token/cost counters. What NEVER
crosses: the conversation the agent has while doing the task — prompts, responses,
tool arguments, file contents, workspace. Titles/descriptions are admin-authored board
metadata (the admin typed them), so unlike E02's exclusion of the user's own
`sessions.title`, board task titles are legitimately central. Make this distinction
explicit in the contract doc.

## Scope

**In scope (this plan):**
- Postgres schema: `enterprise_boards`, `board_tasks` (with `assignee_ref` jsonb,
  `status`, `priority`, `deps`, `progress`, snapshot columns), `board_task_events`.
- New contract **`boards-v1`**: the `board.task.dispatch` command shape (on top of
  device-command-v1) and the `board.task.status` snapshot shape (on top of the E02
  ingest envelope). Assignee resolution rules (account→agent, agent→device, team→N).
- Go control-plane endpoints: `boards/v1/*` (CRUD boards/tasks, assign, move) gated by
  new RBAC verbs `boards.read` / `boards.write` / `boards.assign`.
- OSS-side reporter/executor slice: a **dormant-unless-enrolled** handler that accepts
  a dispatched board task, materializes it into the target agent's local Kanban, runs
  it, and emits status snapshots. Reuses the E02 outbox + E01 command cursor. Never
  activates without `ENTERPRISE_API_URL`.
- Admin UI: the Enterprise boards page (board picker, cross-account summary, drag
  board, assignee = person/agent/team with "runs on <member>·<device>" provenance),
  gated by `boards.read`.

**Out of scope (later):**
- Real-time collaborative drag between admins (v1 is optimistic + refresh).
- Board templates / automations / SLAs.
- Surfacing board tasks *inside* the member's own Kanban UI as "from org" (v1: the
  dispatched task simply appears as a normal local task; a later pass can badge it).
- Cross-org boards (a board is always within one org tenant).

## Definition of done

An org admin on the control plane creates the board "Q3 Vendor Consolidation", adds a
task, and assigns it to (a) Mai Anh's Data Analyst agent, (b) Duy Khanh's account, and
(c) the Risk Team. Each assignee's runtime receives the dispatch over its outbound
channel, creates the task in the right local agent's board, runs it locally, and
reports progress back; the admin watches all three move Backlog → In Progress → Done on
one board, with correct per-assignee "runs on" provenance — and at no point does any
conversation content appear on the control plane. A member offline during dispatch
still picks the task up and reports correctly on reconnect.

## Open questions

1. **Account→agent routing.** When a task is assigned to an *account* (not a specific
   agent), which of the member's agents runs it? Options: their default agent; an
   admin-named agent per board; or the member chooses on receipt. Recommend: default
   agent, overridable by the member — least surprise, keeps member agency.
2. **Team fan-out ownership.** A team's workers may belong to different members on
   different devices. Confirm the team execution model (E-program teams) supports
   cross-device workers, or restrict v1 teams to single-owner teams.
3. **Member consent / visibility.** Should a dispatched task require member
   acknowledgement, or run automatically? Recommend: runs automatically for
   org-managed containers (E03), but PC-installed runtimes get a setting
   "auto-accept org tasks" (default on, member can pause) — respects the AGENTS.md
   principle that enterprise never silently seizes a personal machine.
4. **Deletion semantics.** If an admin deletes a board task mid-run, send a
   `board.task.cancel` command; the local run stops but local artifacts remain on the
   member's machine (never remotely wiped).

## Full plan package

This README is the design frame. The six-file package (findings / architecture /
approaches / implementation / validation) follows the same structure as E02 and E05 and
should be written before implementation, pinning `boards-v1` in Phase 0.
