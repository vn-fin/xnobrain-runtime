# 010 — Approaches

Options considered for each decision, then the chosen path. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [implementation.md](implementation.md),
[validation.md](validation.md).

## Decision A — New async routes vs converting the existing sync route

### A1. Convert `POST /api/brain/v1/teams/{team_id}/run` to async (202 + record)

- Pros: one run endpoint; no duplicate surface.
- Cons (decisive): breaks the existing contract. `src/src/api/teams.ts`
  (`teamsApi.run`) and `src/src/hooks/useTeams.ts` (`lastRun`) — and any
  external client — expect the full result synchronously. The program rule is
  a stable compatibility surface (`routes/setup.py` docstring: "the stable
  Brain4All compatibility surface"). A silent semantic change from
  result-in-response to record-in-response is the worst kind of break.

### A2. Keep `/run` sync but add `?wait=false` to flip it async

- Pros: one path.
- Cons: two response shapes behind one route and one operation name; the
  handler's operation table maps one name → one (callable, message, status)
  tuple (handlers/api.py 92–190), so a per-query status code (200 vs 202) and
  shape fork fights the existing dispatch design; harder to document in
  Swagger; easy for clients to mis-set.

### A3. Keep `/run` synchronous unchanged; add a `runs` sub-resource (chosen)

`POST /runs` (202, record), `GET /runs`, `GET /runs/{run_id}`,
`POST /runs/{run_id}/cancel`, `GET /runs/{run_id}/events`.

- Pros: zero breakage; REST-clean (`runs` is a real resource with ids now);
  each route keeps one shape and one status code, matching the one-name-one-
  tuple dispatch; the sync route silently gains persistence (its runs also
  produce records) without changing its response.
- Cons: two ways to start a run. Acceptable — the sync route becomes the
  documented "small quick runs / scripts" path, and the UI moves to `runs`.

**Chosen: A3.** The sync path internally reuses the same engine and records
(architecture.md "Interaction with the legacy sync route").

## Decision B — Step execution mechanism: chat-per-step (keep) vs Hermes delegate_tool vs kanban-based execution

### B1. Keep chat-per-step (`AgentManager.chat` → fresh `hermes -z` subprocess)

- Pros (decisive):
  - **Cross-profile isolation is the whole product.** Each step runs with
    `HERMES_HOME=<member's profile dir>` (hermes.py `_command_env`,
    1645–1651): the member's own config.yaml, model default, skills, memory,
    state.db. This is the Hermes-issue-#344 contract the docs promise
    (docs/implementation/06-agent-teams.md).
  - Deterministic per-step contract: one prompt in, one summary out, exit
    code, provider-error detection (`_provider_error`), per-step timeout.
  - A subprocess is a clean cancellation unit — kill the process, the step is
    dead (after the Phase 3 `CancelledError` fix, findings.md §4).
  - Zero new pinned-Hermes surface; the compat test stays small.
- Cons: cold-start cost per step — every step pays `hermes` interpreter +
  runtime init (measured seconds, not minutes; verify magnitude during
  Phase 0 if optimizing is ever proposed). Steps on the same agent are
  serialized anyway (per-agent lock), so cold starts dominate only wide
  convoys. Accepted.

### B2. Replace with Hermes `delegate_tool` / `async_delegation`

`.tools/hermes-agent/tools/delegate_tool.py` spawns child `AIAgent` instances
on a `ThreadPoolExecutor` **within one agent's runtime**: same process, same
profile, same credentials; children inherit the *parent's* toolsets minus
`DELEGATE_BLOCKED_TOOLS`; batch mode gives parallelism;
`async_delegation.py` adds background dispatch with a completion queue
(findings.md §5).

- Pros: no per-step process cold start; native batch parallelism; the
  completion queue is a ready-made async rail; the built-in child policy
  (no clarify, no memory writes, summary-only return) matches the team safety
  model almost word for word.
- Cons (decisive for cross-profile teams):
  - **No cross-profile execution.** Delegation children live inside ONE
    agent's runtime and profile. A team of researcher-profile + reviewer-
    profile + coordinator-profile cannot be expressed: every "member" would
    actually be the orchestrator's profile wearing a role prompt — different
    product. The per-member model/skills/memory guarantees would silently
    disappear.
  - **Unreachable without violating constraints.** `delegate_task` is an
    in-agent tool; Brain4All's FastAPI process would have to either import
    and drive Hermes-internal modules (forbidden: no copying/forking
    internals, and these are not public `hermes_cli` surface) or prompt the
    orchestrator agent to call the tool — non-deterministic, no per-step
    status, no per-step cancellation handle.
  - Cancellation and observability would depend on Hermes-internal thread
    machinery we cannot pin as public API.
- **Verdict: rejected for cross-profile teams — and Brain4All teams are
  cross-profile by definition (`_put_team` requires distinct member agent
  ids).** Honest caveat: for a hypothetical future "single-profile team"
  (one agent, many role-steps), B2 would be the better engine — cheaper,
  batch-parallel, purpose-built. If that product shape ever appears, revisit
  via a public `hermes_cli` hook rather than internal imports. Not this plan.

### B3. Execute steps as kanban tasks (ride plans 000–004 + the dispatcher)

Create one kanban task per step, let the kanban dispatcher run them, watch
board events.

- Pros: reuses an existing async execution + eventing rail; runs would appear
  on boards.
- Cons: the kanban dispatcher has no DAG semantics — no `needs`, no upstream
  summary injection, no per-agent serialization + team semaphore, no
  synthesis step; we would re-implement the whole engine as board choreography
  and lose the tight `run_team` contract; team runs polluting boards is a
  product decision nobody asked for; cancellation would go through task
  archiving semantics not designed for it.
- Verdict: rejected. The existing engine is correct and small; it needs a
  lifecycle, not a new executor.

**Chosen: B1 — keep chat-per-step**, move the engine behind a background task
with persistence, and fix the subprocess-kill-on-cancel gap.

## Decision C — SSE transport: file polling vs in-memory pubsub vs both

### C1. Pure file polling (exact `kanban_event_stream` clone)

Poll the run record ~1 s, emit when `revision` advances.

- Pros: dead simple; works for finished runs and across the staleness rule;
  no coupling between the stream and the engine; survives any registry state.
- Cons: up to ~1 s latency per transition; one disk read/second/stream
  (negligible at local scale).

### C2. Pure in-memory pubsub

Engine pushes events to per-run subscriber queues; no file reads.

- Pros: instant.
- Cons: a stream attached after completion (or after restart) has nothing to
  read — needs a file fallback anyway; queue lifecycle/backpressure code;
  events can outrun the persisted record (UI shows state that a crash then
  loses).

### C3. File polling as the backbone + an in-memory "changed" nudge (chosen)

The stream always renders from the persisted record (single source of truth);
an `asyncio.Event` on the registry entry is `set()` after each persist so the
poll loop wakes immediately instead of sleeping the full second
(architecture.md "SSE design").

- Pros: C1's correctness with near-C2 latency; the event is optional — absent
  registry entry degrades gracefully to plain 1 s polling; nothing is ever
  emitted that is not already on disk.
- Cons: a few extra lines over C1. Worth it.

**Chosen: C3.**

## Decision D — Run-file location and retention

### D1. Location

Options: (a) `DATA_DIR/teams/runs/<team_id>/<run_id>.json` — chosen;
(b) `DATA_DIR/team_runs/<team_id>/...` — needlessly forks the teams subtree;
(c) inside each team's YAML — turns every transition into a rewrite of the
team definition and couples run churn to team CRUD; rejected;
(d) inside agent profiles — runs belong to the team, not to any one member;
rejected.

(a) keeps all team-owned data under the existing `teams_root` (files.py line
41), needs only a new `team_runs_root = self.teams_root / "runs"` created in
`FileRepository.__init__`, and `delete_team` can remove a team's whole run
history in one `rmtree`. The theoretical `runs.yaml` vs `runs/` name overlap
is a non-collision (file vs directory; ids are generated hex) — noted in
findings.md §7.

### D2. Retention / pruning

Options: (a) unbounded — local disks are finite and a busy cron-driven team
could accumulate thousands of files; (b) age-based (delete >30 days) — a
rarely-run team loses its entire history; (c) **count-based, newest
`TEAM_RUN_RETENTION = 100` per team, pruned inside `put_team_run` (chosen)** —
bounded disk, history always keeps the most recent runs, no background job,
one constant to tune. Terminal and non-terminal records count equally, but the
active run is never pruned (it is by construction the newest). Summaries are
capped at 100 000 chars each (architecture.md) so worst-case disk per team is
bounded and small.

**Chosen: D1(a) + D2(c).** Plus: one active run per team
(409 `team_run_active`) and a process-wide `MAX_ACTIVE_TEAM_RUNS = 4` cap —
both are run-registry rules, not file rules, but they bound write churn too.
