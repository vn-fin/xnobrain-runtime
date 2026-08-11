# 010 — Findings

What exists today, verified against the code on 2026-07-25. Cross-links:
[README.md](README.md), [architecture.md](architecture.md),
[approaches.md](approaches.md), [implementation.md](implementation.md),
[validation.md](validation.md).

Line numbers refer to the current files; re-verify them during Phase 0 before
editing (they will drift).

## 1. The current teams implementation

### Storage and CRUD

- `brain4all/repositories/files.py` — `FileRepository.teams_root =
  DATA_DIR/teams` (line 41, created in `__init__` lines 45–46).
  `list_teams` (176–185) globs `*.yaml`, `get_team` (187–194) raises a 404
  `StoreError`, `put_team` (196–200) writes atomically via `atomic_yaml`
  (244–245 → `atomic_write` 247–266: mkstemp → fchmod → fsync → `os.replace`
  → directory fsync), `delete_team` (202–209) unlinks under the repository
  lock. Teams live as `DATA_DIR/teams/<team_id>.yaml`.
- `brain4all/services/platform.py` — `list_teams`/`get_team`/`create_team`/
  `update_team`/`delete_team` (458–474). `_put_team` (767–787) validates the
  orchestrator and members via `AgentManager.describe_agent`, dedupes agent
  ids, and restricts member toolsets to `SAFE_TOOLSETS` (33–36). Team ids are
  `uuid.uuid4().hex` (line 465).
- `brain4all/models/api.py` — `TeamWorkflowStep` (168–174), `TeamRun`
  (177–180: `task`, `workflow`, `synthesis`), `TeamMember` (226–231),
  `TeamCreate` (233–239).
- `brain4all/routes/setup.py` — teams routes at lines 117–124 under tag
  `Teams`; the run route is
  `Route("POST", "/api/brain/v1/teams/{team_id}/run", "teams_run", TeamRun, tags=("Teams",))`
  (line 124). Operations are mapped in `brain4all/handlers/api.py` lines
  167–169; `teams_run` calls `s.run_team(p["team_id"], body)` and returns the
  result in the standard envelope with status 200.
- Frontend: `src/api/teams.ts` (48 lines, `teamsApi.run` posts to
  `/api/brain/v1/teams/{id}/run` and awaits the full result),
  `src/hooks/useTeams.ts` (65 lines, holds `lastRun` in memory only),
  `src/components/TeamsView.tsx` (94 lines, renders `state.lastRun` —
  the "Final orchestrator summary" card).

### What `run_team` actually does, end to end

`brain4all/services/platform.py::run_team` (476–575), read in full:

1. Loads the team; rejects a disabled team with 409 `team_disabled` (477–479).
2. Requires `task` or `workflow` (480–483). Records `started = iso()` (484).
3. Builds `members` = enabled members; a team-level
   `asyncio.Semaphore(max(1, max_parallel))`; an empty per-agent lock map
   (485–487).
4. Builds `configured` — per-agent `{agent_id, role, allowed_tools ∩
   SAFE_TOOLSETS}` for members, plus the orchestrator as role `coordinator`
   with `allowed_tools=["todo"]` (488–500).
5. Workflow construction: an explicit workflow goes through `_team_workflow`
   (690–743: 1–64 steps, step-id regex, task/goal fallback to the parent task,
   role→agent resolution, round-robin assignment when unspecified, out-of-team
   agents rejected with `invalid_workflow_agent`, per-step `allowed_tools` must
   be a subset of the member policy). No workflow → a flat convoy: one step
   per enabled member, all with the same task and empty `needs` (506–515).
6. `_validate_team_workflow` (745–765) checks unique ids, no self/duplicate
   needs, no dangling needs, and simulates readiness to detect cycles
   (`workflow_cycle`).
7. `run_step` closure (518–548): acquires the step agent's lock **and** the
   team semaphore; formats upstream dependency results as
   `[step-id] summary-or-error` lines; composes a prompt
   (`Role: …` + "Do not ask for clarification or write memory…" + `Task: …` +
   optional `Upstream results:` block); calls
   `await self.agents.chat(agent_id, {"message": prompt, "toolsets": [...]})`
   (534–537) — **each step is a fresh `hermes … -z` subprocess** (see §3);
   returns `{id, agent_id, role, needs, status: "completed", summary}` or, on
   any exception, `{…, status: "failed", error: <error.code or
   "worker_failed">}` — step failures do NOT abort the run; dependents receive
   `"Failed with <code>"` as the injected upstream value (523–525).
8. Scheduler loop (550–559): repeatedly gathers all `ready` steps (all needs
   completed) with `asyncio.gather`, records results in `completed`. A second
   in-loop cycle guard exists at 553–555 (unreachable after step 6, kept as a
   belt).
9. Synthesis (561–567): orders results by workflow order, composes
   `synthesis_instruction + "\n\n" + "[id] role: summary-or-error"` lines and
   runs **one more chat on the orchestrator** with `toolsets=["todo"]`.
10. Returns `{team_id, member_results, workflow_results (alias),
    orchestrator_summary, started_at, completed_at}` (568–575). Nothing is
    persisted; the dict exists only in the HTTP response.

## 2. The four gaps

1. **Runs are one blocking HTTP request.** `teams_run` (handlers/api.py 169)
   holds the connection for the entire run — potentially many minutes across
   up to 64 steps, each with a default chat timeout of 900 s
   (`DEFAULT_CHAT_TIMEOUT_SECONDS`, hermes.py line 40). A dropped connection
   loses the result forever; the run itself keeps executing server-side with
   no way to observe it.
2. **No run persistence.** The result dict is returned once. There is no run
   id, no history, no status endpoint, no record on disk. `FileRepository`
   has no run-related helper.
3. **No live progress.** The UI shows a spinner ("Running…",
   TeamsView.tsx `runTeam`) until the whole run resolves. There is no SSE for
   team runs; the only SSE producers are `kanban_event_stream`
   (handlers/api.py 324–356), `sandbox_detail_stream` (303–322), and the chat
   stream (196–205).
4. **No cancellation.** `stop_run` (hermes.py 755–768) only works for run ids
   registered in `AgentManager._active_runs` — and **only `_chat_stream_events`
   registers there** (line 674). `AgentManager.chat()` (539–577), the method
   every team step uses, never registers its subprocess anywhere, so team
   steps are invisible to the stop path. There is no team-level grouping of
   step subprocesses at all.

## 3. How a team step becomes a subprocess

`AgentManager.chat` (hermes.py 539–577) → `_prepare_chat_command` (583–646)
builds `[hermes, (--resume …), (--model …), (--skills …), (--toolsets …),
(-z, message)]` → `_run_profile_command` (1592–1606) → `_run_hermes_command`
(1608–1643) spawns the process with `asyncio.create_subprocess_exec`, with
`env["HERMES_HOME"] = <that agent's profile dir>` (`_command_env`, 1645–1651)
and `cwd = <that agent's workspace>`. **This per-step `HERMES_HOME` is what
gives each team member its own profile: own config.yaml, own model default,
own skills, own memories, own state.db.** Any replacement execution mechanism
must preserve this property (see approaches.md Decision B).

## 4. Verified finding: task cancellation leaks the `hermes` subprocess

`_run_hermes_command` (hermes.py 1608–1643):

```python
proc = await asyncio.create_subprocess_exec(...)          # line 1618
try:
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)   # 1626
except asyncio.TimeoutError:                              # 1627
    proc.kill()                                           # 1628
    await proc.communicate()                              # 1629
    raise AgentAPIError("agent command timed out", ...)   # 1630–1634
```

The **only** kill path is `asyncio.TimeoutError`. If the task awaiting
`chat()` is cancelled (`task.cancel()` — exactly what run cancellation does,
and what `asyncio.gather` does to siblings on failure), `asyncio.wait_for`
cancels the inner `proc.communicate()` coroutine and re-raises
`CancelledError` out of `_run_hermes_command` — **without ever touching the
process**. The `hermes` child keeps running to completion (or its own internal
timeout) as an orphan: it still burns provider tokens, still writes the
agent's `state.db`, and is no longer referenced by anything (`chat()` runs are
not in `_active_runs`).

The correct pattern already exists ~900 lines up in the same file:
`_chat_stream_events` (648–753) handles exactly this case at lines 716–725:

```python
except asyncio.CancelledError:
    self._stopped_runs.add(run_id)
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    raise
```

**Required fix (Phase 3):** add an `except asyncio.CancelledError:` branch to
`_run_hermes_command` that mirrors this terminate→wait(5 s)→kill escalation
and re-raises. Without it, run cancellation in this plan would "cancel" only
the awaiting coroutines while every in-flight step subprocess survives — the
acceptance item "no orphan hermes processes after cancel" would fail. This is
an edit to Brain4All's own adapter (`brain4all/integrations/hermes.py`), not
to pinned Hermes code, so it is within the program constraints. The exact edit
is specified in [implementation.md](implementation.md) Phase 3.

Also note for the engine design: on `CancelledError` the cleanup itself awaits
(`proc.wait()`); the run engine must perform its own cancel handling
(persisting the cancelled record) after awaiting the gathered steps, and must
not assume cancellation is instantaneous.

## 5. Hermes delegation primitives (pinned reference, read-only)

Pinned checkout: `.tools/hermes-agent/` at commit
`a7a696ba59e0838a81351859abb39fb8484d4973` (verify during Phase 0).

- `.tools/hermes-agent/tools/delegate_tool.py` — module docstring: "Spawns
  child AIAgent instances with isolated context, inherited toolsets, and their
  own terminal sessions. Supports single-task and batch (parallel) modes. …
  Each child gets: a fresh conversation (no parent history), its own task_id,
  the parent's toolsets with child-only blocked tools stripped, a focused
  system prompt". Children run on a `ThreadPoolExecutor` **inside the parent
  agent's process and profile**; `DELEGATE_BLOCKED_TOOLS` (≈lines 44–53)
  strips `delegate_task`, `clarify`, `memory`, `send_message`, `cronjob` from
  children.
- `.tools/hermes-agent/tools/async_delegation.py` — module docstring:
  "Backs `delegate_task(background=true)`: the parent agent dispatches a
  subagent that runs on a module-level daemon executor and returns a handle
  immediately… When the child finishes, a completion event is pushed onto the
  SHARED `process_registry.completion_queue`" which the CLI/gateway drain and
  surface as a new turn.
- `.tools/hermes-agent/tools/process_registry.py` — the shared completion
  queue those completions ride.
- `.tools/hermes-agent/tools/kanban_tools.py` — agent-facing kanban tools
  (relevant only to approaches.md Decision B option 3).

**Why they do not fit cross-profile teams:** both primitives execute children
*within one agent's runtime* — same process, same `HERMES_HOME`, same
credentials, toolsets inherited from the parent. Brain4All teams are
cross-profile by design: each member is a distinct profile with its own
config, model default, skills, and memory, achieved through the per-step
`HERMES_HOME` env (see §3). Delegation cannot express "run this step as agent
B's profile". Additionally, these are in-process Hermes modules reachable only
from inside a running agent loop, not from Brain4All's FastAPI process;
driving them would require either forking Hermes internals (forbidden) or
prompting an agent to delegate (non-deterministic, no per-step contract).
Full trade-off analysis: [approaches.md](approaches.md) Decision B.

## 6. Patterns to reuse

- **SSE:** `kanban_event_stream` (handlers/api.py 324–356) — cursor from
  `Last-Event-ID`/`after`, `connected` event first, ~1 s poll loop guarded by
  `await request.is_disconnected()`, `StreamingResponse` with
  `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`. The
  special-route wiring is `special="kanban_stream"` (routes/setup.py 112,
  187–189, and the `raw_response` set at 204).
- **Background task lifecycle:** `Brain4AllApplication.register`
  (brain4all/app.py 32–50) already wraps the lifespan to start/cancel the
  kanban dispatcher task — the same place to cancel still-running team-run
  tasks on shutdown.
- **Atomic JSON:** `FileRepository.atomic_json` (files.py 241–242) and the
  notifications store (211–239) as the JSON-per-file listing pattern.
- **Frontend SSE consumption:** `src/api/stream.ts` (`readSSE`) consumed
  as in `src/api/kanban.ts` lines 235–239 (`requestRaw` + `readSSE`) and
  the reconnect/abort loop in `src/hooks/useKanban.ts` lines 228–300.
- **Tests:** `brain4all/tests/test_fastapi.py` setUp (temp `HERMES_HOME`,
  `HERMES_PROFILES_ROOT`, `DATA_DIR`, `Brain4AllApplication` + `FakeRouter`,
  `AsyncClient(ASGITransport)`), and `brain4all/tests/test_analytics.py` for
  the service-level unit style.

## 7. Risks

- **Cancellation during interpreter shutdown:** background tasks cancelled by
  the lifespan teardown must still persist a terminal record; the engine's
  `CancelledError` handler must write the record *before* re-raising, and the
  write is synchronous file I/O (safe in teardown).
- **`asyncio.gather` semantics:** the scheduler uses plain `gather`; when the
  engine task is cancelled, gather cancels all in-flight `run_step` coroutines
  — which is what propagates cancellation into `chat()` and (post-fix) kills
  the subprocesses. Do not switch to `return_exceptions=True` without keeping
  cancellation propagation.
- **Concurrent runs of one team:** the per-agent `asyncio.Lock` map is local
  to a single `run_team` invocation, so two concurrent runs of the same team
  would run one profile concurrently (two `hermes` processes sharing one
  `state.db`/config). Mitigation: one active run per team (409
  `team_run_active`); see approaches.md Decision A.
- **Record size:** step summaries are model output and can be large (the
  engine caps nothing today; `TeamRun.task` caps at 20 000 chars but responses
  are unbounded). Cap stored summaries (e.g. 100 000 chars, truncated with a
  marker) so a run file stays small enough for atomic rewrite-per-transition.
- **Path collision:** runs live under `DATA_DIR/teams/runs/<team_id>/` while
  teams are `DATA_DIR/teams/<id>.yaml`. A team literally named `runs` would
  produce `teams/runs.yaml` (file) next to `teams/runs/` (dir) — no actual
  collision, and `create_team` generates hex ids anyway. Harmless; note only.
- **Line-number drift:** every hermes.py/platform.py line cited here must be
  re-verified during Phase 0.

## 8. Open questions

- Should sync `POST /run` gain an opt-in `?wait=false`? Decided no — a clean
  new `POST /runs` resource is clearer (approaches.md Decision A); revisit
  only if a client cannot migrate.
- Run-level wall-clock timeout (beyond the per-chat 900 s default)? Deferred;
  each step already times out individually, so a run cannot hang forever.
  Noted as future work in architecture.md.
- Retention: 100 runs per team chosen (approaches.md Decision D); make the
  constant easy to change, no config surface yet.
- Whether `delete_team` should also delete its run history — chosen yes
  (records of a deleted team are unreachable via the API anyway); verify no
  product requirement to keep them during Phase 2 review.
