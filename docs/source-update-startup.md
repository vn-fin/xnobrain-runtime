# Source update startup invariants

Runtime owns durable-data evidence; Control owns the deployment, lease and retry.
Admission stays closed from drain until positive post-verification and resume.
Checkpoint IDs, operation/generation fences and target identity remain unchanged.

New checkpoints use manifest version 2. Ordinary files retain exact bytes,
permissions and ownership. The following narrowly defined equivalences permit
an unchanged workspace to restart without falsely reporting data loss:

- Existing profile `logs/agent.log` may append bytes only when its complete old
  prefix still hashes identically. Deletion, rotation, truncation, other log files
  and metadata changes fail verification.
- SQLite files are recognized by extension and header. A read-only transaction
  checks integrity and hashes schema, user/application versions and every typed
  row (including duplicate counts and available row identities). Committed WAL
  data participates in that transaction. Only a recognized database accounts for
  its regular WAL/SHM files; orphan sidecars stay in the ordinary manifest and
  symlinked sidecars fail closed. SQLite may create transient WAL/SHM coordination
  files while opening the read transaction. No journal mode, schema or user rows
  are changed by verification.
- Hermes profile `state.db` alone permits the reviewed `state_meta` initialization
  from absent `fts_storage_version` to string `1`. Schema and every other row
  remain exact. Changing/removing an existing marker or another metadata row
  fails. This is not a general migration allowlist.
- Symlinks are hashed as links without walking their targets. Links must stay
  within their own profile/data scope, except the canonical root `profiles` alias
  and managed `.venv/bin/python*` links into `/opt/xnobrain/.tools/uv-python`.
  Retargeting, cross-profile/outside links and cycles fail closed.

Manifest scans retry a disappearing-file race at most three times. SQLite
verification has a 30-second query deadline and the configured manifest byte
limit; failures remain closed. Post-verify records counts of added, removed,
changed, append-only-log and equivalent-SQLite entries without returning paths
or file/row content. `restore-ready` uses the same preservation proof.

Legacy checkpoints without a version retain their original byte-exact v1
comparison. They are never rebased or silently converted to v2: the old snapshot
cannot reconstruct the old logical SQLite contents. Installing this verifier
does not automatically repair already-failed v1 checkpoints. Such workspaces
require the existing reviewed recovery procedure; do not clear maintenance or
overwrite a checkpoint to force success. A Runtime must already have the new
checkpoint writer before its next update can benefit from v2 evidence.

During maintenance, managed default-profile repairs, approval defaults and
bundled skill installation wait until verified resume/abort releases admission.
Repairs acquire the workspace activity gate so the next checkpoint cannot race
them. Deferred startup errors log an exception type without private content.
The deferred startup task is cancelled on shutdown; it never changes checkpoint
data merely to make a newly installed skill available. Failed verification may
be retried against the same checkpoint after the underlying issue is corrected.

`ensure_default_agent` updates `agent.json` and its timestamp only when managed
metadata changes. Updating the profile registry with identical display metadata
does not rewrite `profiles.yaml`. Opening a version-1 portability task database
does not reassign `PRAGMA user_version`; even assigning the same value changes
SQLite's file header. Actual profile repairs, metadata edits and schema creation
still persist normally.

Native VM provisioning must set `RUNTIME_UPDATE_DATA_PATH=/srv/xnobrain-data`.
The node gateway validates this data anchor before source-update drain. The
Docker default `/opt/data` is not the native volume layout.

Shutdown stages emit fixed stage names and elapsed time, including a warning
after ten seconds. This diagnoses the observed systemd stop timeout without
abandoning a still-running durable write. Gateway additionally requires a terminal
systemd state and `MainPID=0` before swapping source. The original cause of the
observed two-minute shutdown has not been reproduced by source tests.

Regression checks cover WAL mutation, SQLite page-layout churn, schema/metadata
changes, log prefix edits, symlink escapes, legacy comparison, deferred startup,
failed verification/retry and existing maintenance lifecycle tests.
Automatic local Incus verification is documented in Control's
`docs/runtime-source-rollout-local-verification.md`.

## Source verification (2026-10-15)

Focused Runtime checks passed: 69 tests across `test_runtime_update_evidence`,
`test_runtime_updates`, `test_runtime_source_drain`,
`test_runtime_storage_profile_alias` and `test_runtime_storage_memory_state`.
Control config/domain/worker/gateway tests, isolated PostgreSQL rollout fixtures
and `go vet ./...` passed. The pipeline fleet gate passed 20 tests. Both Compose
files rendered with synthetic configuration, and release manifest validation
passed for 0.0.26. Swagger was regenerated through Control's `make swagger`.

Ruff format/check passed for the authored evidence, SQLite, startup, shutdown and
test modules, `app.py`, and root `scripts/{runtime_fleet_gate,test_runtime_fleet_gate}.py`.
The modified existing service/profile-alias tests passed configured Ruff rules;
generated Swagger and vendor trees were excluded from formatting.

The complete regression suites are not green. Runtime FastAPI has the same six
failures on original HEAD and this change; Control handler failures also reproduce
on original HEAD, and builder fixtures lack `jq`. Runtime full discovery was
interrupted after hanging in a budget mock with an unexpected `context_id`
argument. Root tests passed 120/123; two failing assertions reproduce on original
HEAD and one dry-run assertion depends on local environment configuration.
These failures were not changed or hidden. No remote deployment or VM acceptance
test was performed for this source change, and the real shutdown timeout remains
unreproduced.
