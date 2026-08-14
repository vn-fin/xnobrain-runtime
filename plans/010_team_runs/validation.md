# 010 — Validation

How completion is proven. Cross-links: [README.md](README.md),
[findings.md](findings.md), [architecture.md](architecture.md),
[approaches.md](approaches.md), [implementation.md](implementation.md).

An item is complete only when its evidence line is filled in with a real
command output reference (log excerpt, test name + pass line, or screenshot
path). Flipping a checkbox without evidence is not complete
(`plans/LOCAL_FEATURES_CHECKLIST.md`).

## 1. Compatibility test (Phase 0 gate)

Run: `python -m unittest xnobrain.tests.test_team_runs -k Compatibility`

Asserts (implementation.md Phase 0):

- `.tools/hermes-agent` reference checkout is at the pinned commit
  `a7a696ba59e0838a81351859abb39fb8484d4973` and the installed runtime matches
  it (mechanism per plan 001; verify during Phase 0).
- `AgentManager.chat` is a coroutine with parameters
  `(self, raw_name, body)`.
- `AgentManager.stop_run`, `_run_hermes_command`, `_chat_stream_events`
  exist; `hermes_cli.profiles.list_profiles` imports.
- `hermes --help` (when the binary is present) still documents `-z`,
  `--resume`, `--toolsets`, `--skills`, `--model`.
- After Phase 3: `inspect.getsource(AgentManager._run_hermes_command)`
  contains an `except asyncio.CancelledError` handler (tripwire for the
  subprocess-kill fix).

## 2. Unit and integration tests

`xnobrain/tests/test_team_runs.py` (implementation.md Phase 6, tests 1–10):

| Test | Proves |
|---|---|
| `test_async_run_lifecycle` | 202 start, background completion, persisted record schema |
| `test_sync_run_persists_and_keeps_legacy_shape` | legacy `POST /run` contract unchanged + sync history |
| `test_cancel_marks_steps_and_record` | cancel → `cancelled` record, non-terminal steps `cancelled` |
| `test_cancel_kills_subprocess` | `_run_hermes_command` kills the child on `CancelledError` (real `/bin/sleep` child, pid dead after cancel) |
| `test_run_active_conflict` | one active run per team (409 `team_run_active`) |
| `test_sse_event_shape` | `connected` → `run` (revision-ordered, full record) → `done` |
| `test_restart_staleness` | stale `running` file → `failed` / `interrupted_by_restart` on first read |
| `test_record_sanitization` | no composed prompts, stderr/stdout, or non-schema keys in run files |
| `test_retention_prunes` | per-team retention keeps newest `TEAM_RUN_RETENTION` files |
| `test_compat_*` | Phase 0 assertions |

Frontend: `npm test` (existing suites stay green) and
`npm run build` (type check of the new API/hook/component code).

## 3. Repository checks

- `make test` — full backend suite.
- `make check` — all repository checks, before handoff.

## 4. Manual end-to-end (real Hermes, real model via 9router)

Environment: `make backend` (or `make run`), 9router on `:20128`, two real
agents created in the UI.

1. Create a team of 2 agents (researcher + reviewer) plus an orchestrator in
   the Teams view.
2. Snapshot processes: `pgrep -af hermes | tee /tmp/010_before.txt`.
3. Start an async run with a DAG (`review` needs `research`) from the Runs
   panel; confirm the HTTP response returned within ~1 s (202) while the run
   continues.
4. Watch the SSE stream (UI chips, and raw:
   `curl -N http://localhost:8642/api/brain/v1/teams/<id>/runs/<run_id>/events`):
   `connected`, then `run` events as `research` goes running→completed and
   `review` starts.
5. While `review` is `running`: `pgrep -af hermes` shows its child process;
   click Cancel (or `curl -X POST .../cancel`).
6. Within ~5 s: `pgrep -af hermes` shows **no** team-step child remaining
   (compare against `/tmp/010_before.txt` — only pre-existing processes
   remain); the SSE stream ended with `done`; the run detail shows
   `cancelled`, `research` `completed`, `review` `cancelled`.
7. Start another run, let it complete; verify the history list shows both
   runs and the completed one has the orchestrator summary.
8. Restart the backend mid-run (start a run, `kill -9` the server process,
   start it again): the run appears as `failed` /
   `interrupted_by_restart` in the history on first load, and no stray
   `hermes` child from before the kill is still writing (the orphan from a
   hard `kill -9` is expected to die with its own timeout — record what you
   observe; the staleness rule covers the record, not a hard-killed parent's
   already-orphaned children. A graceful restart — SIGTERM — must leave
   `cancelled` records and no orphans).
9. `POST /api/brain/v1/teams/<id>/run` (legacy sync) with a small task still
   returns the full result dict, and the run appears in history with
   `mode: "sync"`.
10. Inspect a run file:
    `cat "$DATA_DIR/teams/runs/<team>/<run>.json" | python3 -m json.tool` —
    confirm schema and absence of prompt preambles/stderr.

## 5. Acceptance checklist (with evidence lines)

- [ ] Phase 0 compatibility test passes against the pinned Hermes.
      (evidence: test run output line)
- [ ] `POST /api/brain/v1/teams/{id}/runs` returns 202 with a `pending|running`
      record in under 2 s for a multi-minute run.
      (evidence: curl timing + response body)
- [ ] Run records persist across a graceful restart and list correctly via
      `GET /runs`. (evidence: history JSON before/after restart)
- [ ] SSE stream emits `connected` → revision-ordered `run` events → `done`.
      (evidence: `curl -N` transcript excerpt)
- [ ] Cancelling a run marks the record and remaining steps `cancelled` and
      persists the final state. (evidence: run file content after cancel)
- [ ] Cancelling a run terminates child hermes subprocesses.
      (evidence: `pgrep -af hermes` before/after cancel, steps 2–6 above)
- [ ] A record left `running` by a killed process reads back as `failed` with
      `error: "interrupted_by_restart"`. (evidence: step 8 output)
- [ ] Legacy `POST /run` response shape is unchanged (field-for-field) and
      sync runs land in history. (evidence: `test_sync_run_persists_and_keeps_legacy_shape` pass + curl body)
- [ ] Run files contain no composed prompts, stderr/stdout, credentials, or
      reasoning; only the documented schema keys.
      (evidence: `test_record_sanitization` pass + step 10 inspection)
- [ ] One active run per team enforced (409 `team_run_active`); process cap
      enforced (409 `too_many_team_runs`). (evidence: test pass lines)
- [ ] Retention prunes to `TEAM_RUN_RETENTION` files per team.
      (evidence: `test_retention_prunes` pass)
- [ ] Frontend Runs panel: history list, live chips updating via SSE, working
      Cancel button; `npm run build` clean.
      (evidence: screenshot + build output)
- [ ] `make test` and `make check` pass. (evidence: final output lines)
