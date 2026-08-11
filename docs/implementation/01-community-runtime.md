# OSS-01: file-only Community runtime

Priority P0. May run in parallel with enterprise scaffold and public contract work. Own `internal/repositories`, Community persistence under `internal/profile`, and focused tests. Do not edit route assembly, `cmd/main.go`, or enterprise code.

## Outcome

Self-hosted Free runs persistently with no database, Redis, Kafka, login, or network dependency. The existing in-memory fallback is not production persistence and must be replaced. The application API remains compatible while PostgreSQL-specific repository code is removed from the Community composition.

## Source-of-truth map

| Data | Community source of truth |
|---|---|
| Agent identity and metadata | `profiles/<agent-id>/config.yaml` plus a versioned metadata section |
| Memory, skills, snapshots, workspace | Existing profile tree |
| Conversations and messages | Hermes session store through the runtime adapter; do not duplicate into Studio storage |
| Cron definitions | `profiles/<agent-id>/cron/jobs/<cron-id>.yaml` |
| Daily/monthly usage | `profiles/<agent-id>/cron/usage/<period>.json` or a user-level aggregate with one explicit owner |
| Notifications and missed runs | `DATA_DIR/notifications/<id>.json` |
| Runtime IP/port/PID | `DATA_DIR/runtime/endpoint.json`, disposable and rewritten at startup |
| Provider credentials | 9router-owned storage; never Studio files |

## Design requirements

- Add a file-backed repository implementing only metadata that Studio truly owns. Prefer profile-specific stores over one large global JSON document.
- Every mutation uses a lock, writes a same-directory temporary file, fsyncs when durability matters, renames atomically, and preserves permissions.
- Directory scans tolerate an invalid profile by returning a structured diagnostic without hiding valid profiles.
- Schema versions are explicit. Readers migrate old supported shapes in memory and writers emit only the latest shape.
- Symlinks and traversal cannot escape the profile root.
- Agent deletion is soft first: mark deleted and move to a recoverable local trash area; permanent deletion is a separate explicit operation.
- Community Compose contains only Studio/Hermes and persistent volumes. Remove PostgreSQL, Redis, and Kafka services only after file persistence and restart tests pass.
- Keep paid PostgreSQL behavior in the enterprise repository; do not leave dormant paid infrastructure required by the Free binary.

## Migration

Provide a one-time importer from the current PostgreSQL export shape or a portable `.zip` profile archive. Do not silently discard existing conversations or crons. If Hermes already owns equivalent sessions, map identifiers and report anything not importable.

## Acceptance criteria

- Create agents, conversations, crons, usage, approvals, skills, memories, and snapshots; restart; verify all supported state remains.
- Start with network disabled and no database-related environment variables.
- Simulate process interruption before rename and prove the previous file remains valid.
- Run two concurrent cron/agent mutations and prove no lost update or corrupt YAML/JSON.
- Prove provider secrets never appear under `DATA_DIR/profiles` or bundle output.
- Root `npm run dev`, `go run cmd/main.go`, `make check`, and minimal Compose start remain valid.
