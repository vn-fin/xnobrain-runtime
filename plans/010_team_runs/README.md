# 010 — Team runs v2: persistent, observable, cancellable multi-agent team runs

Priority: P2. Depends on the Hermes pin proven in plan 001's Phase 0 pattern.
Ships independently of plans 005–009.

Read the sibling documents in order:

- [findings.md](findings.md) — what teams already do, the four gaps, and the
  subprocess-cancellation finding in the Hermes adapter.
- [architecture.md](architecture.md) — layering, run-record file format, state
  machine, route table, SSE design, restart semantics.
- [approaches.md](approaches.md) — options considered and the chosen ones.
- [implementation.md](implementation.md) — ordered, file-by-file steps.
- [validation.md](validation.md) — how completion is proven.

Also read before starting: [`AGENTS.md`](../../AGENTS.md),
[`plans/LOCAL_FEATURES_CHECKLIST.md`](../LOCAL_FEATURES_CHECKLIST.md) (program
principles), [`brain4all/services/platform.py`](../../brain4all/services/platform.py)
(`run_team`, lines ~476–575), [`brain4all/integrations/hermes.py`](../../brain4all/integrations/hermes.py)
(`chat`, `_run_hermes_command`, `_chat_stream_events`, `stop_run`),
[`brain4all/routes/setup.py`](../../brain4all/routes/setup.py), and
[`docs/implementation/06-agent-teams.md`](../../docs/implementation/06-agent-teams.md)
(the Hermes-issue-#344 execution contract).

## Mandatory constraints (restated, obeyed throughout)

- One FastAPI/Hermes process on `:8642` and one 9router on `:20128`. No Go, no
  PostgreSQL, no ORM, no second API process.
- Extend from the `brain4all` package; never copy, fork, or re-implement Hermes
  internals. `.tools/hermes-agent/` is a pinned read-only reference.
- `brain4all/routes/setup.py` is the only route-assembly point. Handlers own
  HTTP/SSE translation, services own rules, repositories own atomic files,
  integrations adapt Hermes, models are Pydantic.
- Atomic file persistence (temp → fsync → rename) for all new state; run records
  are Brain4All product metadata under `DATA_DIR` and are allowed.
- Never log, return, or persist credentials, provider keys, composed prompts, or
  agent internal reasoning. User-authored task text and the returned final
  summaries are product data and are stored deliberately.
- Phase 0 gate: confirm the Hermes pin and add a compatibility test asserting
  the exact symbols this plan's execution path uses.
- No mock or demo data in production paths.

## Goal

Brain4All already has working teams: file-backed team CRUD
(`DATA_DIR/teams/<id>.yaml`), a DAG/convoy execution engine with per-agent
serialization, team-level parallelism, dependency-summary injection, cycle
detection, and a coordinator synthesis step (`PlatformService.run_team`). What
it does **not** have is any run lifecycle around that engine. This plan adds:

1. **Persistent run records** — every team run (sync or async) writes an atomic
   JSON record to `DATA_DIR/teams/runs/<team_id>/<run_id>.json`: status,
   timestamps, per-step status/summary/error, and the orchestrator synthesis.
   Run history survives process restarts and dropped connections.
2. **Asynchronous execution** — `POST /api/brain/v1/teams/{team_id}/runs` starts the
   run in a background `asyncio.Task` and returns `202` with the run record
   immediately. The existing synchronous `POST /api/brain/v1/teams/{team_id}/run`
   keeps its exact contract for backward compatibility.
3. **Live progress** — `GET .../runs/{run_id}/events` streams step transitions
   over SSE, following the established `kanban_event_stream` pattern
   (record polling ~1s plus an in-memory nudge for immediacy).
4. **Cancellation** — `POST .../runs/{run_id}/cancel` cancels the background
   task, marks unfinished steps cancelled, persists the final state, **and
   kills the child `hermes` subprocesses**. This requires a small, precisely
   scoped fix in `brain4all/integrations/hermes.py::_run_hermes_command`,
   which today leaks a running subprocess when the awaiting task is cancelled
   (see [findings.md](findings.md) — this is a verified bug, not a guess).
5. **Frontend** — a Runs panel in `src/src/components/TeamsView.tsx`: run
   history, a live run view with per-step status chips updated via SSE, and a
   cancel button.

## Non-goals

- **No replacement of chat-per-step with Hermes `delegate_tool` /
  `async_delegation` for cross-profile teams.** Those primitives spawn children
  inside ONE agent's runtime and profile; they cannot give each team member its
  own `HERMES_HOME` profile isolation, which is the core property of the current
  engine. [approaches.md](approaches.md) Decision B weighs this honestly and
  rejects it for cross-profile teams.
- **No distributed execution, no dispatcher revival of interrupted runs.** A run
  interrupted by a process restart is marked `failed` with code
  `interrupted_by_restart` on first read; resuming it is future work.
- **No enterprise quotas, budgets, or run RBAC.** Local OSS runs are unlimited;
  the only local guard is "one active run per team" plus a small process-wide
  concurrency cap for background runs.
- **No new database.** Run records are atomic JSON files with a simple retention
  policy, nothing more.
- **No change to the team YAML format or team CRUD routes.**

## Phase overview

- **Phase 0 — Pin and prove.** Confirm the Hermes pin; add compatibility
  assertions for the symbols the team path uses (`AgentManager.chat` coroutine
  signature, the `hermes` CLI flags the adapter composes, and — after Phase 3 —
  the `CancelledError` subprocess-kill behavior of `_run_hermes_command`).
- **Phase 1 — Models.** `TeamRunRecord`, `TeamRunStepRecord` (and reuse of the
  existing `TeamRun` body) in `brain4all/models/api.py`.
- **Phase 2 — Repository.** `FileRepository` helpers: list/get/put run files
  under `DATA_DIR/teams/runs/<team_id>/`, atomic writes, pruning.
- **Phase 3 — Service.** New `brain4all/services/team_runs.py`: in-process run
  registry, the background engine (a shared `_execute_workflow` refactored out
  of `run_team` and used by both the sync and async paths), cancellation, the
  staleness rule, and the `_run_hermes_command` `CancelledError` kill fix in
  `brain4all/integrations/hermes.py`.
- **Phase 4 — Handlers + routes.** New operations in
  `brain4all/handlers/api.py` and the exact `Route(...)` lines (including the
  SSE special route) in `brain4all/routes/setup.py`.
- **Phase 5 — Frontend.** `src/src/api/teams.ts` run endpoints + SSE watcher,
  `src/src/hooks/useTeams.ts` run state, `TeamRunsPanel` in
  `src/src/components/TeamsView.tsx`.
- **Phase 6 — Tests.** `brain4all/tests/test_team_runs.py` mirroring the
  `test_fastapi.py` / `test_analytics.py` patterns: async lifecycle,
  cancellation kills the subprocess, SSE event shape, restart staleness, run
  file schema and sanitization.

File-by-file steps are in [implementation.md](implementation.md).

## Definition of done

- Starting an async run on a real 2-worker team returns `202` immediately; the
  run completes in the background; `GET .../runs` and `GET .../runs/{run_id}`
  return the persisted record with correct per-step statuses, summaries, and
  the orchestrator synthesis — including after a server restart.
- The SSE stream emits a `connected` event and then a `run` event for every
  step transition until the terminal event, matching the documented shape.
- Cancelling a mid-flight run terminates the in-flight child `hermes`
  subprocesses (verified with `pgrep` before/after), marks running and pending
  steps `cancelled`, and persists the final `cancelled` record.
- The legacy `POST /api/brain/v1/teams/{team_id}/run` response shape is byte-for-byte
  compatible with today's, and sync runs also leave a persisted run record.
- A run record never contains composed prompts, injected upstream text beyond
  the stored step summaries themselves, stderr, credentials, or reasoning.
- A record left in `running` by a killed process is returned as `failed` with
  `error: "interrupted_by_restart"` on first read after restart.
- Compatibility test, the new unit/integration tests, and `make check` pass.
