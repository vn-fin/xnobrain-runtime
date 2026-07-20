# Architecture

Open Lumora is a modular monolith. The browser calls one Fiber application. The API invokes Go services as functions rather than making internal HTTP calls, and those services own agent lifecycle, conversations, cron scheduling, profile files, snapshots, and edition limits.

The frontend lives in `src/` and ships as its own container. The backend entrypoint is `cmd/main.go`. Business rules live under `services/<service_name>`; safe profile I/O is concentrated in `internal/profile`; runtime execution is behind `internal/runtime`; persistence is behind `internal/repositories`; transport code is under `internal/api`, with centralized route assembly in `internal/v1/routes/SetupRoutes.go`.

Hermes-owned state is file-backed. Versioned agent metadata lives in each
profile `config.yaml`, conversations and messages live in the profile session
tree, cron definitions are individual YAML files, and notifications are
individual JSON files. Self-hosted Compose also starts PostgreSQL for the
pulled Enterprise API; the OSS backend does not query it. No ORM is used.

## Agent profiles

Each agent is assigned a six-character ID and this fixed tree:

```text
DATA_DIR/profiles/<agent-id>/
  config.yaml
  AGENTS.md
  skills/<skill-id>/SKILL.md
  memories/MEMORY.md
  memories/USER.md
  workspace/AGENTS.md
  sessions/
  cron/
  logs/
  snapshots/
  home/.hermes -> profile root
```

The runtime receives both `HERMES_HOME=<profile>` and `HOME=<profile>/home`. This covers Hermes code that respects `HERMES_HOME` and compatibility paths that resolve `~/.hermes`, while keeping every write inside the selected profile. Paths are validated, traversal is rejected, and symlink escapes are blocked.

Skill and memory mutations create content-addressed snapshot manifests and immutable payloads. Restore copies the selected payload back to its original target. The per-profile `AGENTS.md` teaches Hermes where live memory, skills, and snapshots belong.

## Approval persistence

Runtime approval events include a subsystem key such as `memory_write` or `skills_write`. Resolving with `session` or `always` calls the profile manager before resuming Hermes. It records the choice in `approvals.persisted` and disables that subsystem's `write_approval` flag in the selected agent's `config.yaml`. A subsequent process reads the same file, so this is not browser-only state.

## Runtime and providers

Studio calls the OSS Hermes runtime directly for `/v1/runs`, `/events`,
`/stop`, `/approval`, and 9router operations. The public repository builds the
runtime and owns its extensions. Studio and runtime share
`open-lumora_open_lumora_data`; private HTTP travels on
`open-lumora-control`. The browser never receives a runtime address.

The gateway adapter starts a profile API server lazily and reuses it for that profile until the Go process stops. `CONTAINER_IDLE_ENABLED` and `CONTAINER_IDLE_TIMEOUT_MINUTES` remain policy metadata for a future managed-container controller; the local OSS application container does not stop itself because doing so would also remove its UI and API.

## Editions and quotas

`internal/studio` is the application-composition seam and
`internal/studio/contract` contains repository, runtime, lifecycle, and DTO
contracts. The open-source policy returns an unrestricted local principal.
Enterprise plans govern only authenticated extension APIs and managed cloud
resources; they never reduce self-hosted Hermes access.

A downstream enterprise composition can implement the same policy with gRPC token verification, tenant-aware plan lookup, RBAC, audit, and licensed limits. `-1` means unlimited for numeric limits. The enterprise control plane remains responsible for distributed reservations, container resource classes, billing, and managed telemetry; the local application remains usable without that control plane.

The product-level availability and recommended Free, Pro, and Enterprise values are maintained in [plans, features, and limits](plans.md). Paid PostgreSQL repositories and infrastructure were moved into the enterprise repository; later managed features continue to compose through the public contracts rather than importing Community internals.
