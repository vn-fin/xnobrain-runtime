# 010 — Implementation

Ordered, file-by-file steps. Cross-links: [README.md](README.md),
[findings.md](findings.md), [architecture.md](architecture.md),
[approaches.md](approaches.md), [validation.md](validation.md).

Line numbers below were verified on 2026-07-25 and will drift — re-locate each
anchor by symbol name, not by number.

## Phase 0 — Pin and compatibility test

No behavior change; produces the safety net for everything after.

1. Confirm the Hermes pin. The pinned reference checkout is
   `.tools/hermes-agent/` at commit
   `a7a696ba59e0838a81351859abb39fb8484d4973` (`git -C .tools/hermes-agent
   rev-parse HEAD`); the runtime is installed by `scripts/install-linux.sh` /
   `Dockerfile.backend`. Verify during Phase 0 that both install paths resolve
   to the same Hermes version as the reference checkout; if a pin mechanism
   from plan 001 already exists, reuse it — do not invent a second one.
2. Create `xnobrain/tests/test_team_runs.py` (this file grows through
   Phase 6). Add a `CompatibilityTests(unittest.TestCase)` class asserting the
   exact symbols the team execution path uses:
   - `from xnobrain.integrations import AgentManager`;
     `inspect.iscoroutinefunction(AgentManager.chat)` is true and
     `list(inspect.signature(AgentManager.chat).parameters)` ==
     `["self", "raw_name", "body"]`.
   - `AgentManager.stop_run`, `AgentManager._run_hermes_command`,
     `AgentManager._chat_stream_events` exist (the adapter surface this plan
     touches).
   - `from hermes_cli.profiles import list_profiles` imports (the profile
     inventory the platform already rides).
   - The `hermes` CLI flags the adapter composes are still accepted: run
     `[hermes, "--help"]` via subprocess and assert `-z`, `--resume`,
     `--toolsets`, `--skills`, `--model` appear in the help text. Guard with
     `unittest.skipUnless(shutil.which(os.environ.get("HERMES_CLI",
     "hermes")), ...)` so CI without the binary skips, mirroring how other
     tests avoid hard binary deps — verify during Phase 0 whether an existing
     skip helper exists.
   - After Phase 3 lands, extend with: the source of
     `AgentManager._run_hermes_command` contains an
     `except asyncio.CancelledError` handler
     (`"CancelledError" in inspect.getsource(AgentManager._run_hermes_command)`)
     — a tripwire so a future upstream-sync cannot silently drop the kill fix.
3. Run: `make test` (or `python -m unittest xnobrain.tests.test_team_runs`).

## Phase 1 — Models

File: `xnobrain/models/api.py` (append near `TeamCreate`, line ~233).

1. Add:

```python
TeamRunStatus = Literal["pending", "running", "completed", "failed", "cancelled"]


class TeamRunStepRecord(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    agent_id: str
    role: str
    task: str = Field(default="", max_length=20_000)
    needs: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    status: TeamRunStatus = "pending"
    summary: str = ""
    error: str | None = None
    conversation_id: str | None = None
    started_at: str | None = None
    ended_at: str | None = None


class TeamRunRecord(BaseModel):
    id: str
    team_id: str
    status: TeamRunStatus = "pending"
    error: str | None = None
    mode: Literal["async", "sync"] = "async"
    task: str = ""
    synthesis_instruction: str = ""
    orchestrator_id: str = ""
    orchestrator_summary: str = ""
    created_at: str
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str
    revision: int = 0
    steps: list[TeamRunStepRecord] = Field(default_factory=list)
```

2. Export both from `xnobrain/models/__init__.py` (extend the existing
   `from .api import (...)` list) and import them in
   `xnobrain/routes/setup.py`'s model import block (lines 14–23) — the start
   route reuses the existing `TeamRun` body model; the record models are for
   service-side shaping and OpenAPI documentation of the envelope `data`.
3. The start body stays the existing `TeamRun` (models/api.py 177–180) —
   no change.

## Phase 2 — Repository helpers

File: `xnobrain/repositories/files.py`.

1. In `__init__` (lines 38–46): add
   `self.team_runs_root = self.teams_root / "runs"` and include it in the
   mkdir loop.
2. Add methods (mirror the notifications trio at 211–239 and the team trio at
   176–209; every write goes through `atomic_json`):

```python
TEAM_RUN_RETENTION = 100  # module-level constant next to _SAFE_ID

def _team_run_dir(self, team_id: Any) -> Path:
    return self.team_runs_root / self._id(team_id, "team id")

def list_team_runs(self, team_id: Any, limit: int = 20) -> list[dict[str, Any]]:
    # glob "*.json", parse, skip corrupt files (OSError/JSONDecodeError),
    # sort by created_at desc, clamp limit 1..100, return the slice.

def get_team_run(self, team_id: Any, run_id: Any) -> dict[str, Any]:
    # path = _team_run_dir(team_id) / f"{self._id(run_id, 'run id')}.json"
    # missing -> StoreError("team run not found", status=404, code="run_not_found")
    # corrupt -> StoreError(..., status=500, code="invalid_team_run")

def put_team_run(self, record: Mapping[str, Any]) -> dict[str, Any]:
    # validate record["team_id"] and record["id"] with self._id,
    # atomic_json to the path, then prune_team_runs(team_id), return dict(record).

def prune_team_runs(self, team_id: Any, keep: int = TEAM_RUN_RETENTION) -> int:
    # under self._lock: list files sorted newest-first by parsed created_at
    # (fallback: file mtime), unlink the tail, _sync_dir, return removed count.

def delete_team_runs(self, team_id: Any) -> bool:
    # under self._lock: shutil.rmtree(_team_run_dir(team_id), ignore_errors=False)
    # if it exists; _sync_dir(self.team_runs_root); return whether it existed.
```

   Note `run_id` values are `tr_<32hex>` which matches `_SAFE_ID`.
3. Wire team deletion: in `xnobrain/services/platform.py::delete_team`
   (471–474), call `self.repository.delete_team_runs(team_id)` after a
   successful `delete_team` (service-level rule; the repository methods stay
   single-purpose).

## Phase 3 — Service: engine refactor, registry, cancellation, subprocess-kill fix

### 3a. `xnobrain/integrations/hermes.py` — the CancelledError kill fix

In `_run_hermes_command` (1608–1643), change the try/except around
`proc.communicate()` (lines 1625–1634) to:

```python
try:
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
except asyncio.CancelledError:
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    raise
except asyncio.TimeoutError:
    proc.kill()
    await proc.communicate()
    raise AgentAPIError(
        "agent command timed out",
        code="agent_command_timeout",
        status=504,
    )
```

This mirrors `_chat_stream_events`' existing handler (716–725) exactly
(terminate → 5 s grace → kill), re-raises so cancellation semantics are
preserved, and changes nothing on the success or timeout paths. It benefits
every `chat()`/`install_skill` caller, not just teams. (Rationale and the
verified bug: findings.md §4.)

### 3b. New file `xnobrain/services/team_runs.py`

```python
"""Persistent, observable, cancellable team runs over the existing DAG engine."""
```

Contents (rules only; no HTTP, no route strings):

- Module constants: `MAX_ACTIVE_TEAM_RUNS = 4`, `SUMMARY_CAP = 100_000`.
- `_ActiveRun` dataclass and `TeamRunService` per architecture.md
  ("In-process run registry").
- `TeamRunService.__init__(self, repository, agents, platform)` — `platform`
  is the `PlatformService`, used only for `get_team`, `_team_workflow`,
  `_validate_team_workflow`, and `SAFE_TOOLSETS` context; do not move those
  helpers.
- `start_run(team_id, body) -> dict`:
  1. `team = self.platform.get_team(team_id)`; disabled → 409 `team_disabled`.
  2. Reject if `_by_team.get(team_id)` exists → 409 `team_run_active`;
     reject if `len(self._active) >= MAX_ACTIVE_TEAM_RUNS` → 409
     `too_many_team_runs`.
  3. Build `configured` + workflow exactly as `run_team` does today
     (platform.py 480–516) — extract that block into a shared
     `PlatformService._build_team_workflow(team, body) -> list[dict]` so both
     services call one implementation. Validation errors surface as today's
     400s, synchronously.
  4. `record = self._new_record(team, task, synthesis, workflow, mode="async")`
     (status `pending`, `revision=0`), `repository.put_team_run(record)`.
  5. Register `_ActiveRun`, `task = asyncio.create_task(self._drive(...))`,
     `task.add_done_callback(<deregister>)`, return the record.
- `_drive(record, team, workflow, body)` — the background wrapper:
  - transition run → `running` (persist+notify), then run the engine
    `await self._execute_workflow(record, team, workflow, body)`;
  - on success: synthesis result → `orchestrator_summary`, run →
    `completed`;
  - `except asyncio.CancelledError`: `_finalize(record, "cancelled",
    error="cancelled")` then `raise`;
  - `except EXPECTED_ERRORS as error`: `_finalize(record, "failed",
    error=getattr(error, "code", "worker_failed"))`;
  - `except Exception`: `_finalize(record, "failed", error="internal_error")`
    and log the type only (never the message content of provider output).
- `_execute_workflow(record, team, workflow, body)` — the engine moved out of
  `run_team` (platform.py 518–567) with three surgical additions:
  - before the chat in `run_step`: mark that step `running`
    (`_transition_step`), persist + notify;
  - after the chat / on exception: mark `completed`/`failed`, store the
    capped `summary` (or `error` code) and `conversation_id` from the chat
    result, persist + notify;
  - after the scheduler loop: mark a synthetic transition before/after the
    synthesis chat; synthesis failure → raise `ServiceError(...,
    code="synthesis_failed")` so `_drive` records `failed` (member results
    remain persisted).
  Everything else — locks, semaphore, prompt composition, upstream injection,
  gather scheduling, failure injection — is moved verbatim, not rewritten.
- `_transition_step` / `_finalize` / `_persist(record)`: bump `revision`,
  set `updated_at`, `repository.put_team_run(record)`, `changed.set()` then
  `changed.clear()` on the registry entry (event-pulse; subscribers re-read
  the file).
- `_finalize` also stamps `ended_at` and flips every non-terminal step to
  `cancelled` when the run ends `cancelled`/`failed`.
- `get_run(team_id, run_id)` / `list_runs(team_id, limit)`: repository read +
  the staleness rule (architecture.md "Restart / staleness semantics") +
  list-view summary stripping (architecture.md route table).
- `cancel_run(team_id, run_id) -> dict`: active → `task.cancel()`, await
  completion bounded by `asyncio.wait_for(asyncio.shield(entry.task), 30)`
  (swallow `CancelledError` from the awaited task), return the persisted
  record; not active + terminal → 409 `run_already_finished`; not active +
  non-terminal file → apply staleness rule and return it.
- `shutdown()`: cancel all active tasks (used by the lifespan).

### 3c. `xnobrain/services/platform.py`

1. In `__init__` (74–77), after analytics wiring:
   `from .team_runs import TeamRunService` /
   `self.team_runs = TeamRunService(repository, agents, self)`.
2. Extract `_build_team_workflow(team, body)` from `run_team` (480–516) as
   described above.
3. Rewrite `run_team` (476–575) as the thin sync path: guard team/enabled,
   build workflow, create a `mode:"sync"` record, register (same
   one-active-run + cap guards), `await self.team_runs._drive_sync(...)`
   (a variant that runs `_drive` inline without `create_task`), then return
   the legacy dict shaped from the record:
   `member_results` = steps in workflow order mapped to today's
   `{id, agent_id, role, needs, status, summary|error}` shape,
   `workflow_results` alias, `orchestrator_summary`, `started_at`,
   `completed_at` (= record `ended_at`). A test pins this shape
   (validation.md).
4. `delete_team` (471–474): add the `delete_team_runs` call (Phase 2 step 3).
5. Export nothing new from `xnobrain/services/__init__.py` unless handlers
   need the class — they reach it via `service.team_runs`, mirroring
   `service.kanban` / `service.analytics`.

### 3d. `xnobrain/app.py` — lifespan teardown

In `register`'s lifespan (32–50), alongside the kanban dispatcher cancel:
`self.service.team_runs.shutdown()` inside the `finally` block before the
dispatcher cancellation (order does not matter; both are idempotent). The
engine persists `cancelled` records during teardown (findings.md §7).

## Phase 4 — Handlers and routes

### 4a. `xnobrain/handlers/api.py`

1. In the `operations` dict (after the `teams_run` line, 167–169), add:

```python
"team_runs_start": (lambda: s.team_runs.start_run(p["team_id"], body), "team run started", 202),
"team_runs_list": (lambda: s.team_runs.list_runs(p["team_id"], q.get("limit")), "team runs retrieved successfully", 200),
"team_runs_get": (lambda: s.team_runs.get_run(p["team_id"], p["run_id"]), "team run retrieved successfully", 200),
"team_runs_cancel": (lambda: s.team_runs.cancel_run(p["team_id"], p["run_id"]), "team run cancelled", 200),
```

2. Add `async def team_run_event_stream(self, request)` next to
   `kanban_event_stream` (324–356), implementing architecture.md "SSE design":
   `connected` event, poll+`changed`-nudge loop emitting `event: run` with the
   sanitized record when `revision` advances, terminal `event: done`, the same
   `StreamingResponse` headers, `EXPECTED_ERRORS` → `event: error` then
   return.

### 4b. `xnobrain/routes/setup.py`

1. After line 124 (`teams_run`), add exactly:

```python
Route("POST", "/api/brain/v1/teams/{team_id}/runs", "team_runs_start", TeamRun, tags=("Teams",)),
Route("GET", "/api/brain/v1/teams/{team_id}/runs", "team_runs_list", tags=("Teams",)),
Route("GET", "/api/brain/v1/teams/{team_id}/runs/{run_id}", "team_runs_get", tags=("Teams",)),
Route("POST", "/api/brain/v1/teams/{team_id}/runs/{run_id}/cancel", "team_runs_cancel", tags=("Teams",)),
Route("GET", "/api/brain/v1/teams/{team_id}/runs/{run_id}/events", "team_run_event_stream", special="team_run_stream", tags=("Teams",)),
```

2. In `_endpoint` (163–199), add the branch:

```python
elif route.special == "team_run_stream":
    async def endpoint(request: Request) -> Response:
        return await handlers.team_run_event_stream(request)
```

3. Add `"team_run_stream"` to the `raw_response` special set in
   `setup_routes` (line 204).

Note: FastAPI route ordering — `/runs/{run_id}` vs the literal `/run` and
`/runs` paths do not conflict (distinct segments), but keep the new lines
grouped after `teams_run` for readability. Starlette matches in registration
order; `/api/brain/v1/teams/{team_id}/runs` (POST/GET) must be registered — as
written — before nothing in particular; no shadowing exists. Verify with the
Swagger page during Phase 4.

## Phase 5 — Frontend

### 5a. `src/api/teams.ts`

1. Add types `TeamRunStep` and `TeamRunRecord` mirroring the Pydantic models
   (status unions included).
2. Extend `teamsApi`:

```ts
startRun: (teamId, task, workflow = [], synthesis?) =>
  request<TeamRunRecord>(`/api/brain/v1/teams/${encodeURIComponent(teamId)}/runs`, { method: 'POST', body: JSON.stringify({ task, workflow, synthesis }) }),
listRuns: (teamId) => request<TeamRunRecord[]>(`/api/brain/v1/teams/${encodeURIComponent(teamId)}/runs`),
getRun: (teamId, runId) => request<TeamRunRecord>(`/api/brain/v1/teams/${encodeURIComponent(teamId)}/runs/${encodeURIComponent(runId)}`),
cancelRun: (teamId, runId) => request<TeamRunRecord>(`/api/brain/v1/teams/${encodeURIComponent(teamId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
watchRun: async (teamId, runId, onEvent, signal) => { /* requestRaw + readSSE, mirroring src/api/kanban.ts lines 235–239 */ },
```

   Import `readSSE` from `./stream` and the raw-request helper from
   `./client` exactly as `kanban.ts` does (verify the helper name —
   `requestRaw` — during Phase 5).

### 5b. `src/hooks/useTeams.ts`

Extend the hook (keep the existing surface untouched — `lastRun` and `run`
stay for the legacy sync button until the view drops them):

- New state: `runs: TeamRunRecord[]`, `activeRun?: TeamRunRecord`,
  `runsStatus`.
- `loadRuns(teamId)`, `startRun(teamId, task, workflow)` (sets `activeRun`
  from the 202 record and prepends to `runs`), `cancelRun(teamId, runId)`.
- A `useEffect` that, while `activeRun` is non-terminal, opens
  `teamsApi.watchRun` with an `AbortController`, replaces `activeRun` (and
  the matching entry in `runs`) on each `run` event, and closes on `done` —
  mirror the reconnect/cleanup structure of `src/hooks/useKanban.ts`
  lines 228–300.

### 5c. `src/components/TeamsView.tsx`

- New component `TeamRunsPanel` (same file, following the existing single-file
  card style): rendered when a team is selected, replacing/augmenting the
  current `team-result` card.
  - **History list**: rows of `run.id` short-hash, status badge, started_at,
    step count; click loads detail (`getRun`).
  - **Live run view**: per-step chips `StepChip` (id · role · status; colors
    keyed by status: pending/running/completed/failed/cancelled), the
    orchestrator summary when terminal, and error code when failed.
  - **Buttons**: "Run (async)" → `startRun`; "Cancel" (visible while
    `activeRun` is `pending|running`) → `cancelRun`.
- Keep the existing "Run workflow" (sync) button working during migration;
  switch its handler to `startRun` and delete `lastRun` usage as the final
  step of the phase.
- Styles: reuse the existing `teams-card` / `team-policy` / badge classes
  (check `src/` styles for kanban status chips to reuse; verify class
  names during Phase 5).
- Wiring: `TeamsView` receives the extended `useTeams` state — no new props
  from the app shell.

Run `npm run build` for type verification.

## Phase 6 — Tests

File: `xnobrain/tests/test_team_runs.py` (extends the Phase 0 class file).
Mirror the harness of `xnobrain/tests/test_fastapi.py` (temp `HERMES_HOME` /
`HERMES_PROFILES_ROOT` / `DATA_DIR`, `XNOBrainApplication` with `FakeRouter`,
`AsyncClient(transport=ASGITransport(app=...))`) and the unit style of
`test_analytics.py`. Patch `AgentManager.chat` with `unittest.mock.AsyncMock`
(the same boundary the engine calls) — no live model, no mock *data* in
production paths.

Test list (each maps to a validation.md acceptance line):

1. `test_async_run_lifecycle` — create two agents + a team via the API; mock
   `chat` to return `{"response": "<step summary>", "conversation_id": "c1"}`;
   `POST /runs` → 202 with `status in {"pending","running"}`; poll
   `GET /runs/{id}` until `completed`; assert per-step `completed`, summaries,
   `orchestrator_summary`, and that the JSON file exists under
   `DATA_DIR/teams/runs/<team>/` with the full architecture.md schema.
2. `test_sync_run_persists_and_keeps_legacy_shape` — `POST /run` returns
   exactly the legacy keys (`team_id`, `member_results`, `workflow_results`,
   `orchestrator_summary`, `started_at`, `completed_at`) and leaves a
   `mode:"sync"` record on disk.
3. `test_cancel_marks_steps_and_record` — mock `chat` to await an
   `asyncio.Event` that never sets; start async run; `POST .../cancel`;
   assert 200, record `cancelled`, all non-terminal steps `cancelled`,
   `ended_at` set, registry empty.
4. `test_cancel_kills_subprocess` — the `_run_hermes_command` fix, tested
   without mocks: call
   `AgentManager._run_hermes_command(tmp, tmp, ["/bin/sleep", "60"], timeout_seconds=120)`
   inside an `asyncio.Task`, cancel it, then assert the spawned pid is gone
   (`os.kill(pid, 0)` raises `ProcessLookupError`; capture the pid by
   patching `asyncio.create_subprocess_exec` with a recording wrapper).
5. `test_run_active_conflict` — second `POST /runs` while one is in flight →
   409 `team_run_active`.
6. `test_sse_event_shape` — open `GET .../events` with the test client's
   streaming API while a mocked run progresses; assert the first frame is
   `event: connected`, subsequent `event: run` frames carry increasing
   `revision` and the full record, and the final frame is `event: done`.
7. `test_restart_staleness` — write a `running` record file directly via
   `FileRepository.put_team_run`, build a fresh service (registry empty),
   `GET /runs/{id}` → `failed` + `error == "interrupted_by_restart"`, steps
   `cancelled`, and the file on disk was rewritten.
8. `test_record_sanitization` — after a completed run whose mocked chat
   received a composed prompt containing a sentinel (`"Upstream results"` /
   `"Role:"`), assert the stored file contains no `"Role:"` preamble, no
   `stderr` key, no `stdout`, and only the whitelisted keys of the schema.
9. `test_retention_prunes` — write `TEAM_RUN_RETENTION + 5` records; assert
   only the newest `TEAM_RUN_RETENTION` files remain.
10. `test_compat_*` — the Phase 0 assertions, including the
    `CancelledError`-in-source tripwire.

Finish with `make test` and `make check`.
