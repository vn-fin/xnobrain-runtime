# 010 — Architecture

How persistent team runs fit the XNOBrain layering. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[approaches.md](approaches.md), [implementation.md](implementation.md),
[validation.md](validation.md).

## Layering fit

The feature respects the standard boundaries (`AGENTS.md`): handlers translate
HTTP/SSE, services own rules, repositories own atomic files, integrations
adapt Hermes, models are Pydantic, and `xnobrain/routes/setup.py` is the only
route-assembly point. One FastAPI/Hermes process, one 9router; no new process,
DB, or ORM.

```
routes/setup.py            5 new Route(...) lines, tag "Teams"
   |                       (one special="team_run_stream" SSE route)
handlers/api.py            new operations team_runs_* + team_run_event_stream
   |                       (HTTP <-> envelope / SSE framing only)
services/team_runs.py      NEW TeamRunService: run registry, background engine
   |                       (_execute_workflow shared by sync + async paths),
   |                       cancellation, staleness rule, record shaping
   |---> services/platform.py       run_team delegates to the shared engine;
   |                                 workflow build/validation helpers stay here
   |---> repositories/files.py      NEW run-file helpers (atomic JSON + prune)
   |---> integrations/hermes.py     AgentManager.chat unchanged in signature;
   |                                 _run_hermes_command gains the
   |                                 CancelledError subprocess-kill branch
models/api.py              NEW TeamRunRecord, TeamRunStepRecord
```

Local layers call each other in-process, exactly like `KanbanService` and
`AnalyticsService` are wired today (`PlatformService.__init__`,
platform.py lines 74–77): `PlatformService` constructs
`self.team_runs = TeamRunService(self.repository, self.agents, self)` (the
back-reference is for the workflow-building helpers; see implementation.md
Phase 3 for the exact split).

## Run-record file format

Path: `DATA_DIR/teams/runs/<team_id>/<run_id>.json`, written with
`FileRepository.atomic_json` (temp → fsync → rename → dir fsync) on **every
state transition**. `run_id` is `"tr_" + uuid.uuid4().hex`.

Full schema (all keys always present):

```json
{
  "id": "tr_9f2c4e...32hex",
  "team_id": "0a1b2c...",
  "status": "pending | running | completed | failed | cancelled",
  "error": null,
  "mode": "async | sync",
  "task": "user-entered task text",
  "synthesis_instruction": "user-entered synthesis text or the default sentence",
  "orchestrator_id": "agent-id",
  "orchestrator_summary": "",
  "created_at": "2026-07-25T03:04:05Z",
  "started_at": "2026-07-25T03:04:05Z",
  "ended_at": null,
  "updated_at": "2026-07-25T03:04:06Z",
  "revision": 3,
  "steps": [
    {
      "id": "research",
      "agent_id": "worker-a",
      "role": "researcher",
      "needs": [],
      "allowed_tools": ["web"],
      "status": "pending | running | completed | failed | cancelled",
      "summary": "",
      "error": null,
      "started_at": null,
      "ended_at": null
    }
  ]
}
```

Content rules (enforced by the service, asserted by tests):

- `task`, per-step `task` (not stored — the step's task text is user-authored
  workflow input and IS stored as part of `steps[].task`? No: see below),
  `synthesis_instruction`: user-authored input — storing them is fine.
  Decision: store `steps[].task` too (add `"task"` to the step object above);
  it is user-entered workflow text (or the run task), never model output.
- `summary` and `orchestrator_summary`: the final returned summaries — product
  output; storing them is the point. Cap each at 100 000 chars with a
  `"\n…[truncated]"` suffix.
- **Never stored:** the composed per-step prompt (role preamble + injected
  upstream block), stdout/stderr, provider errors beyond the short `error`
  code, credentials, tokens, model reasoning, tool arguments/output.
- `error` values are short machine codes only: `worker_failed`,
  `provider_request_failed`, `agent_command_timeout`, `workflow_cycle`,
  `interrupted_by_restart`, `cancelled`, `synthesis_failed`.
- `revision` increments on every persisted transition; the SSE stream uses it
  as its cursor.

Retention: on every `put_team_run`, prune the team's run directory to the
newest `TEAM_RUN_RETENTION = 100` files (by `created_at` in the filename-free
record; sort by file mtime as tiebreak). `delete_team` also removes
`DATA_DIR/teams/runs/<team_id>/` (see approaches.md Decision D).

## State machine

Run states:

```
            start_run / run_team
 pending ───────────────────────► running ──► completed   (synthesis chat ok)
    │                                │────► failed        (engine exception,
    │ (cancel before start)          │       synthesis failure, or
    └──────────────► cancelled ◄─────┘       interrupted_by_restart on re-read)
                        (task.cancel() → CancelledError handled by engine)
```

- `pending` exists only between record creation and the first scheduler tick;
  the async path persists `pending` before `asyncio.create_task` so a crash
  window never loses the run.
- Terminal states: `completed`, `failed`, `cancelled`. Terminal records are
  immutable (the service never rewrites them except the staleness rule, which
  only ever converts a non-terminal `running`/`pending` to `failed`).
- A run is `completed` even if individual steps failed — matching today's
  engine semantics where step failures are injected downstream and synthesis
  still runs. `failed` means the engine itself did not finish (unexpected
  exception, synthesis chat failure → `synthesis_failed`, restart).

Step states: `pending → running → completed | failed`; `cancelled` is applied
by the cancel/interrupt paths to any step not yet terminal. Step transitions:

- `running` when `run_step` acquires the lock+semaphore and is about to chat.
- `completed`/`failed` exactly as the current engine decides (findings.md §1
  step 7).
- `cancelled` for every non-terminal step when the run is cancelled or marked
  `interrupted_by_restart`.

## New route table

All added to `ROUTES` in `xnobrain/routes/setup.py`, tag `Teams`, immediately
after the existing line 124 (`teams_run`). Envelope = `APIEnvelope` except the
raw SSE route.

| Method | Path | operation | Body model | Response `data` | Status |
|---|---|---|---|---|---|
| POST | `/api/brain/v1/teams/{team_id}/runs` | `team_runs_start` | `TeamRun` (existing) | `TeamRunRecord` | 202 |
| GET | `/api/brain/v1/teams/{team_id}/runs` | `team_runs_list` | — | `list[TeamRunRecord]` (summaries omitted; see below) | 200 |
| GET | `/api/brain/v1/teams/{team_id}/runs/{run_id}` | `team_runs_get` | — | `TeamRunRecord` | 200 |
| POST | `/api/brain/v1/teams/{team_id}/runs/{run_id}/cancel` | `team_runs_cancel` | — | `TeamRunRecord` (status `cancelled`, or current if already terminal → 409) | 200 |
| GET | `/api/brain/v1/teams/{team_id}/runs/{run_id}/events` | `team_run_event_stream` | — | SSE (`special="team_run_stream"`) | 200 |

The existing `POST /api/brain/v1/teams/{team_id}/run` (sync) is unchanged in path,
body, and response shape.

List behavior: `team_runs_list` returns records with `steps[].summary` and
`orchestrator_summary` replaced by empty strings (a `summary_chars` int is
included per step) to keep the history payload small; the detail route returns
everything. Query params: `limit` (default 20, clamp 1–100).

Error codes: `team_not_found`/`not_found` 404, `run_not_found` 404,
`team_disabled` 409, `team_run_active` 409 (a non-terminal run already exists
for the team), `run_already_finished` 409 (cancel on a terminal run),
`too_many_team_runs` 409 (process-wide cap, see registry), plus the existing
workflow validation codes surfaced at start time with 400.

Pydantic models (Phase 1, `xnobrain/models/api.py`):

- `TeamRunStepRecord` — fields exactly as the step object above.
- `TeamRunRecord` — fields exactly as the run object above,
  `steps: list[TeamRunStepRecord]`.
- Request body for start reuses the existing `TeamRun` model unchanged.

## In-process run registry

`xnobrain/services/team_runs.py`:

```python
@dataclass
class _ActiveRun:
    run_id: str
    team_id: str
    task: asyncio.Task        # the background engine task
    changed: asyncio.Event    # set on every persisted transition, then cleared
```

- `TeamRunService._active: dict[str, _ActiveRun]` keyed by `run_id`, plus
  `_by_team: dict[str, str]` (team_id → active run_id) for the
  one-active-run-per-team guard. Mutated only on the event loop (no extra
  locking needed; verify single-loop assumption during Phase 0 — uvicorn runs
  one loop).
- Process-wide cap `MAX_ACTIVE_TEAM_RUNS = 4` background runs; exceeding it →
  409 `too_many_team_runs`.
- `start_run` flow: validate team + body (same checks as `run_team`), build
  the workflow eagerly so invalid workflows fail synchronously with 400,
  create + persist the `pending` record, register, then
  `asyncio.create_task(self._drive(record, workflow, body))`. The task's
  `done_callback` deregisters from `_active`/`_by_team`.
- `cancel_run` flow: look up `_active[run_id]` → `task.cancel()`; await the
  task (with a bounded `asyncio.wait_for(..., 30)`) so the response reflects
  the persisted `cancelled` record; if the run_id is not active but the file
  says non-terminal, apply the staleness rule instead.
- Lifespan integration: `XNOBrainApplication.register` (xnobrain/app.py,
  lifespan at lines 32–50) additionally cancels all `_active` tasks on
  shutdown, mirroring the kanban dispatcher teardown; the engine's
  `CancelledError` handler persists terminal state first (so a clean shutdown
  leaves `cancelled` records, and only a hard kill leaves `running` files for
  the staleness rule).

## SSE design (follows `kanban_event_stream`)

Handler `APIHandlers.team_run_event_stream` (new, alongside
`kanban_event_stream` at handlers/api.py 324–356), wired with
`special="team_run_stream"` in `routes/setup.py` (added to both the
`_endpoint` chain and the `raw_response` set at line 204).

- Cursor: `revision`, taken from `Last-Event-ID` header or `?after=` query,
  default `0` (replay from the current record).
- Stream loop (async generator):
  1. Yield `id: <revision>\nevent: connected\ndata: {"run_id", "team_id",
     "revision"}`.
  2. Loop while `not await request.is_disconnected()`:
     read the record via the service; if `record["revision"] > cursor`, yield
     `id: <revision>\nevent: run\ndata: <sanitized full record JSON>` and
     advance the cursor. If the record is terminal, yield a final
     `event: done\ndata: {"status": ...}` and return.
  3. Immediacy: if the run is active, wait on
     `registry.changed.wait()` with `asyncio.wait_for(..., timeout=1.0)`
     instead of a bare `sleep(1)`; on timeout just re-poll. The file re-read
     stays the correctness backbone (approaches.md Decision C) — the event is
     only a latency optimization, and a stream attached to a finished run
     works with no registry entry at all.
- Event payload = the same sanitized record the GET detail route returns
  (full record, not deltas — records are small; the UI replaces state
  wholesale, avoiding delta-merge bugs).
- Headers copied from the kanban stream: `Cache-Control: no-cache,
  no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no`.

## Restart / staleness semantics

There is no dispatcher revival in this plan. Rule, applied in the service on
**every read** of a run record (`get`, `list`, SSE poll, cancel):

> If `record["status"]` is `pending` or `running` **and** `record["id"]` is
> not in the in-process registry, the run was interrupted by a process
> restart. Rewrite it once, atomically: `status="failed"`,
> `error="interrupted_by_restart"`, `ended_at=now`, every non-terminal step →
> `status="cancelled"`, `revision += 1`; persist; return the rewritten record.

This is lazy (first read heals), idempotent, and needs no startup scan.
Optional future work (not this plan): a lifespan startup sweep that heals all
teams' run dirs eagerly, and true resumable runs.

## Sequence diagram — start → progress → cancel

```
UI                    FastAPI handler        TeamRunService            AgentManager / hermes
|                          |                      |                          |
|-- POST /teams/T/runs --->|                      |                          |
|                          |-- start_run(T,body)->|                          |
|                          |                      | validate team+workflow   |
|                          |                      | put run file (pending)   |
|                          |                      | create_task(_drive)      |
|<---- 202 TeamRunRecord --|<-- record (pending) -|                          |
|                          |                      |== background task ==     |
|-- GET /runs/R/events --->|                      | status=running, persist  |
|<== connected =============|                     | step research: running   |
|                          |                      |-- agents.chat(worker) -->| spawn hermes -z (HERMES_HOME=worker)
|<== run (rev 2) ==========|  (poll/changed)      |                          |
|                          |                      |<------ summary ----------|
|                          |                      | step research: completed |
|<== run (rev 3) ==========|                      | step review: running     |
|                          |                      |-- agents.chat(worker2) ->| spawn hermes -z
|-- POST /runs/R/cancel -->|                      |                          |
|                          |-- cancel_run(R) ---->| task.cancel()            |
|                          |                      | CancelledError reaches   |
|                          |                      | gather -> run_step ->    |
|                          |                      | chat -> _run_hermes_command
|                          |                      |                          | except CancelledError:
|                          |                      |                          |   proc.terminate()/kill()  <-- Phase 3 fix
|                          |                      | mark steps cancelled,    |
|                          |                      | status=cancelled, persist|
|<---- 200 cancelled ------|<-- record ----------|                          |
|<== run (rev 4) + done ===|                      | deregister               |
```

## Interaction with the legacy sync route

`run_team` keeps its signature and response dict. Internally it becomes:
build record (`mode:"sync"`), register (same one-active-run guard), await
`_drive(...)` directly (no `create_task`), then shape the legacy response
`{team_id, member_results, workflow_results, orchestrator_summary,
started_at, completed_at}` **from the persisted record** so both paths share
one engine and sync runs also appear in history. Field-for-field response
compatibility is an explicit test (validation.md).

## Future work (recorded, out of scope)

- Run-level wall-clock timeout and per-run `max_parallel` override.
- Startup healing sweep and resumable runs.
- Surfacing per-step token/cost usage by joining plan 009 analytics on the
  step's conversation id (chat() already returns `conversation_id`; storing it
  in the step record is cheap — include `"conversation_id": str | null` in
  `TeamRunStepRecord` now so the join is possible later).
