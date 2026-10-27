# Source update startup invariants

Runtime owns durable-data evidence; Control owns the deployment, lease and retry.
Admission stays closed from drain until positive post-verification and resume.
Checkpoint IDs, operation/generation fences and target identity remain unchanged.

New coordinated source rollouts stage the target first, stop the old application,
then invoke `python -m xnobrain.integrations.runtime_source_checkpoint` from the
verified staged tree. Its bounded stdin request contains the existing operation,
generation, source target and managed durable paths. It is a node-local command,
not a public API; it runs as the workspace service user under the gateway's
source-action lock. It requires systemd to report a terminal application state
and zero main PID, matching paused/drained maintenance, and exclusive operation
and workspace activity locks. It never starts the application or connectors.

This captures the data after even an old connector has stopped writing. The
helper preserves the existing journal schema and uses the target's manifest
writer. Recovery reconciles a checkpoint saved before a lost journal write;
retry never replaces it with a fresh snapshot. New source must pass the same
post-verification before maintenance ends. Existing online checkpoints remain
immutable and use their original verifier semantics.

Checkpoint storage failures emit one `runtime_update_storage_failure` warning
before returning the existing HTTP 409/error code. Correlate `operation_id`,
`fence` and `phase=checkpoint` with Control. Unsafe-data diagnostics include
`storage_check` (`sqlite_sidecar_symlink`, `coordination_file`, `regular_file`,
or `file_type` for a legacy v1/v2 baseline), `storage_root` and
`relative_path`. Paths identify the rejected entry within a durable root, are
bounded to 512 characters, and replace non-printable characters. Logs never
include resolved symlink targets, absolute data roots, file contents, request
bodies, tokens or exception messages. These diagnostics do not relax validation,
release maintenance or change the checkpoint/HTTP response contract.

SQLite flushing validates and materializes each durable root's file list before
opening databases for `wal_checkpoint(TRUNCATE)`. Closing the last SQLite
connection can remove `-wal` and `-shm` files that were present during directory
enumeration; continuing a lazy file walk afterward would incorrectly reject the
now-missing sidecar as unsafe data. The subsequent manifest verifies the resulting
state normally. This does not ignore missing durable files, orphan sidecars, or
symlinked SQLite sidecars.

New checkpoints use manifest version 3, which avoids rereading every durable
byte twice while the workspace is in maintenance:

- Files up to 1 MiB retain exact bytes, permissions and ownership; an identical
  rewrite still verifies.
- Larger regular files are fingerprinted by size, modification time (ns),
  permissions and ownership without reading their content. Both the persistent
  volume reattachment and the legacy `cp --archive` copy preserve these fields.
- SQLite databases are compared only by the logical evidence below, not by
  their page bytes or size.
- Runtime-owned profile logs (`<root profile>/logs/` and
  `<profiles>/<id>/logs/`) are diagnostics and are not part of the manifest.
  User files named `*.log` elsewhere remain durable data.
- Checkpointing no longer runs a validation manifest before the SQLite flush.
  The flush opens only regular, non-link databases and refuses symlinked
  `-wal`/`-shm` sidecars before opening a database.

Version 2 checkpoints (written by Runtimes before manifest v3) keep exact bytes
for every file, with these narrowly defined equivalences:

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
- Symlinks are hashed as link text without opening or walking their targets,
  wherever they point (outside the profile, another profile, or a cycle);
  retargeting fails verification. A user-created link therefore never blocks an
  update. Checkpoint, flush and verification never follow a durable link.
- Manifest v3 records FIFOs and sockets by file type without opening them;
  adding or removing one fails verification. SQLite flushing opens only regular
  files. A v1/v2 baseline still rejects special files.

Manifest scans retry a disappearing-file race at most three times. SQLite
verification has a 30-second query deadline and the configured manifest byte
limit; failures remain closed. Post-verify records counts of added, removed,
changed, append-only-log and equivalent-SQLite entries without returning paths
or file/row content. `restore-ready` uses the same preservation proof.

Legacy checkpoints without a version retain their original byte-exact v1
comparison. They are never rebased or silently converted to v2: the old snapshot
cannot reconstruct the old logical SQLite contents. Version 2 checkpoints are
likewise never rebased to v3. A verifier older than v3 rejects a v3 baseline
with `runtime_update_manifest_version`, so a downgrade fails closed. Installing
this verifier does not automatically repair already-failed v1 checkpoints. Such workspaces
require the existing reviewed recovery procedure; do not clear maintenance or
overwrite a checkpoint to force success. Online checkpoints use the currently
installed writer; stopped-process source checkpoints use the verified staged
target's writer before the source swap.

During maintenance, managed default-profile repairs, approval defaults and
bundled skill installation wait until verified resume/abort releases admission.
Expired portability uploads and exports also remain intact during maintenance:
constructor cleanup uses workspace admission and activity fencing, then deferred
startup retries it after verified release. Transfer expiry and import pins still
apply after release; transfer files remain part of the integrity manifest.
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

Organization connector timer ticks treat update, rebalance and memory maintenance
as a pause. They retry after the normal connector interval without changing
`state.json`, credentials, heartbeat sequence, error state or timestamps. Error
recording also takes the workspace activity gate: maintenance may start between
a failed network tick and the attempted error write. Genuine connection failures
outside maintenance still persist their state and back off normally. Connector
state remains part of the exact durable-data proof; it is not excluded from
verification.

Post-verification now distinguishes HTTP 409 `runtime_update_data_not_preserved`
from `runtime_update_identity_mismatch`. Data mismatch takes precedence when both
checks fail; the persisted receipt retains both booleans. The structured event
`runtime_update_verification_failure` includes operation, fence, phase,
`data_preserved`, `installed_identity_matches` and added/removed/changed counts.
It contains no manifest entries, file contents or credentials. Control's gateway
accepts these codes as well as older Runtime's `runtime_update_post_verify_failed`.
Deploy the compatible gateway before using the new Runtime codes.

For an existing connector-state mismatch, preserve the original checkpoint and
operation. Installing this fix stops future writes during maintenance but cannot
restore bytes already changed by the previous connector. The checkpoint contains
hashes, not old state-file contents: current timestamps or identical file sizes
cannot prove that every other field is unchanged. Compare with a trusted backup
and restore only verified original data through the reviewed recovery procedure,
then retry the same fenced operation. If no such evidence exists, leave the
workspace in recovery for an explicitly reviewed recovery decision. Do not
rewrite checkpoint hashes, exclude connector state, clear maintenance, or force a
successful member state. An online checkpoint requires the fixed connector
already running. The stopped-process protocol instead stops the old connector
before capturing the first checkpoint; updating only the candidate after an
online checkpoint is insufficient.

Native VM provisioning must set `RUNTIME_UPDATE_DATA_PATH=/srv/xnobrain-data`.
The node gateway validates this data anchor before source-update drain. The
Docker default `/opt/data` is not the native volume layout.

The offline checkpoint helper independently verifies the exact writable managed
mount and existing `RUNTIME_DATA_VOLUME_ID` marker before repository construction.
Incus `virtiofs` mounts are valid. The gateway clears the unrelated
`RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT` for this process instead of applying the
custom-page local-filesystem allowlist to a managed volume. Missing mounts,
read-only mounts, missing/mismatched markers and absent volume IDs fail closed.
Recovery never initializes/adopts a volume, including when provisioning flags
are inherited. This check does not start the server or connectors.

Shutdown stages emit fixed stage names and elapsed time, including a warning
after ten seconds. This diagnoses the observed systemd stop timeout without
abandoning a still-running durable write. Gateway additionally requires a terminal
systemd state and `MainPID=0` before swapping source. The original cause of the
observed two-minute shutdown has not been reproduced by source tests.

Regression checks cover WAL mutation, SQLite page-layout churn, schema/metadata
changes, log prefix edits, link retargeting, special files, manifest v3
fingerprints, legacy comparison, deferred startup,
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

## Operator acceptance of legacy expired upload loss

Older Runtime startup could expire temporary transfers while maintenance was
active. For an already failed checkpoint, restoring the exact missing files
from a trusted backup remains preferable. When no copy exists, an operator can
explicitly accept the loss of expired, unused upload bundles. This is acceptance
of lost temporary data, **not restoration**. Legacy manifests contain hashes,
not deleted metadata; expiry and lack of use are operator attestations, not
facts inferred from a checksum.

The node-local command `python -m xnobrain.integrations.runtime_transfer_recovery`
has no HTTP route. Pause automatic rollout actions, stop `xnobrain-api.service`,
and run it with the Runtime Python environment as the OS account owning its data
directory. Preserve a protected copy of the existing journal/checkpoint first.
Both this helper and the Runtime verifier handling the subsequent post-verify
must contain this implementation. A helper receipt alone cannot change the
behavior of an older installed verifier. Never falsify an installed source
identity to make recovery pass.

Prepare a JSON array containing **every exact missing manifest path**, for
example the two paths below for each affected upload ID:

```json
[
  "data/transfers/uploads/1c5a78cdd660402ea4dd3cc6fde754a4/bundle.zip",
  "data/transfers/uploads/1c5a78cdd660402ea4dd3cc6fde754a4/metadata.json"
]
```

Use the existing operation journal's `generation` (maintenance fence, not the
Control deployment generation), `steps.checkpoint.checkpoint_id` and
`steps.checkpoint.manifest.digest`. Supply the actual existing durable roots
from this workspace's service configuration; the example placeholders below
must be replaced before execution:

```sh
python -m xnobrain.integrations.runtime_transfer_recovery \
  --data-dir /actual/data/directory \
  --profiles-root /actual/profiles/directory \
  --root-profile /actual/big-brother/directory \
  --operation-id upd_EXISTING \
  --generation EXISTING_FENCE \
  --checkpoint-id ckp_EXISTING \
  --manifest-digest EXISTING_MANIFEST_DIGEST \
  --paths-file /protected/missing-transfers.json \
  --reason 'INCIDENT reference: reviewed these expired unused uploads' \
  --attest-expired-unused
```

The command acquires the existing operation/activity locks, requires the active
paused operation and immutable checkpoint to match, and refuses missing,
symlinked, or unreadable portability pin databases. It opens that database
read-only without migrations. Only complete `bundle.zip`/`metadata.json` pairs
under 32-character lowercase hexadecimal upload IDs are eligible; pinned
uploads, symlink entries, unsupported v1 manifests, duplicate paths, partial
lists, unrelated additions/deletions/modifications, or changed fences fail
closed. All verification retains the original manifest version's semantics.

Acceptance is stored in `steps.expired_transfer_acceptance` with the checkpoint,
manifest digest, target, fence, exact paths, OS UID, timestamp and audit reason.
Repeating the identical approval is idempotent; it cannot widen or replace an
existing acceptance. It does not delete files, rewrite the checkpoint, change
Control rollout state, resume dispatch, or mark post-verification successful.

Restart the service and use the existing Control operator retry for the same
operation. Post-verify still checks installed identity and all other durable
data. Its existing `data_preserved`/`verified` gates reflect passing the explicit
verification policy; actual loss remains visible as `data_loss_accepted=true`,
`data_verification.accepted_removed`, and the unchanged `changes.removed` count.
A restored file must still match its original entry. Any additional loss fails.
Normal resume remains mandatory; source restoration verification stays strict.
