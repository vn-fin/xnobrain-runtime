# Open Lumora implementation roadmap

This directory turns the product plan into implementation work. Read this file, `docs/plans.md`, and the specification assigned to your work package before editing code. Cross-repository protocol changes require an explicit version change under `docs/contracts`; do not coordinate by importing private enterprise packages into this repository.

## Target system

`open-lumora` is the public runtime used by self-hosted Free and by cloud/on-premise workers. It owns Hermes execution, profiles, local scheduling, local quota safety, snapshots, portable bundles, telemetry instrumentation, and the optional outbound device connector. `open-lumora-enterprise` owns identity, plans, the PostgreSQL quota ledger, managed scheduling, device command delivery, fleet/hardware control, managed telemetry, billing, audit, and organization policy.

Self-hosted Free must run without login, database, or cloud connectivity. Cloud connection is optional and outbound-only. Cloud services may dispatch signed commands but never write directly into a local profile.

## Required read order

1. `docs/plans.md`
2. `docs/implementation/README.md`
3. The assigned numbered implementation specification
4. Relevant contracts under `docs/contracts/`
5. `AGENTS.md` and `.agents/rules/01-start-here.md`

## Priorities and dependency waves

| Wave | Work package | Repository | Can run with | Blocks |
|---|---|---|---|---|
| P0 | OSS-00 public composition and contracts | Open source | ENT-00 | Device, scheduler, import integration |
| P0 | OSS-01 file-only Community persistence | Open source | ENT-00, ENT-01 | Free production deployment |
| P0 | ENT-00 enterprise scaffold | Enterprise | OSS-00, OSS-01 | Every enterprise service |
| P0 | ENT-01 identity, plans, and quota ledger | Enterprise | OSS-01 | Managed scheduling and paid limits |
| P1 | OSS-02 local cron, daily quota, and misfires | Open source | OSS-03, ENT-01 | Managed cron end-to-end |
| P1 | OSS-03 outbound device connector | Open source | OSS-02, ENT-01 | Local managed scheduling |
| P1 | ENT-02 device registry and command gateway | Enterprise | OSS-02, ENT-01 | Scheduler-to-local delivery |
| P1 | ENT-03 managed scheduler | Enterprise | OSS-02, OSS-03, ENT-02 | Cloud cron product |
| P1 | OSS-04 portable bundle | Open source | OSS-02, ENT-02 | Upgrade/import flow |
| P1 | OSS-05 telemetry instrumentation | Open source | OSS-01, ENT-01 | Managed telemetry |
| P1 | ENT-04 telemetry and bundle import | Enterprise | OSS-04, OSS-05 | Retention and migration product |
| P2 | OSS-06 agent teams and delegation policy | Open source | ENT-03, ENT-04 | Team UI and enterprise governance |
| P2 | ENT-05 organization teams and advanced features | Enterprise | OSS-06 | Enterprise collaboration |
| P3 | Integration, UI, migration, security, and load tests | Both | Completed P0–P2 | Release |

P0 contracts are frozen before P1 integration begins. An implementation can proceed against fakes once its contract is frozen; it does not wait for the other repository's service.

## Parallel editing ownership

To avoid merge conflicts, assign one owner per row:

| Owner | Exclusive paths |
|---|---|
| Community persistence | `internal/repositories`, `internal/profile`, `db/community`, persistence tests |
| Local cron | `services/crons`, local notification service, quota-window tests |
| Device connector | `services/deviceconnector`, `internal/device`, connector tests |
| Portability | `services/portability`, bundle schema/tests |
| Telemetry | `internal/tracing`, telemetry middleware and redaction tests |
| Teams | `services/teams`, team/delegation tests |
| Frontend | `frontend/src/features/<feature>` only |
| Integration owner | `cmd/main.go`, `internal/v1/routes/SetupRoutes.go`, shared config, root Compose and docs |

Feature owners must not edit route assembly or `cmd/main.go`; they expose constructors and handlers for the integration owner. The integration owner starts only after the feature package compiles independently.

## Release gates

- No ORM in either repository.
- Self-hosted Free starts with no PostgreSQL, Redis, Kafka, login, or cloud endpoint.
- Restart tests prove agents, cron jobs, quotas, approvals, and notifications persist.
- All profile writes resolve under `DATA_DIR/profiles/<agent-id>` and use atomic replacement.
- Managed commands are authenticated, expiring, replay-safe, idempotent, and outbound-only.
- Missed cron occurrences do not consume quota until an execution is reserved.
- Telemetry excludes prompts, responses, memories, skills, credentials, tool arguments, and file contents.
- Bundle import rejects traversal, symlinks, oversized expansion, schema mismatch, and checksum failure.
- `go test ./...`, `go vet ./...`, frontend tests/build, local smoke tests, and cross-repository contract tests pass.

## Current state

OSS-00 through OSS-06 are implemented. Telemetry redacts before a bounded disk-backed retry spool, local logs rotate at seven days, and collector loss never blocks execution. Saved teams are owner-scoped YAML, enforce plan count/member/depth/concurrency limits under races, forward explicit Hermes toolset allowlists, block privileged toolsets for leaves, expose API/UI flows, and round-trip through `.lumora` bundles with complete ID remapping. Managed import uses authenticated restart-safe stage/commit/rollback endpoints and keeps staged profiles invisible until commit. Cross-repository import and enterprise PostgreSQL forward/restart/concurrency/isolation/rollback gates now pass. Production image execution, large-scale load, accessibility audit, disaster recovery, and external security assessment remain release gates.
