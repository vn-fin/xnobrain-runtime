# Architecture

Open Lumora is a modular monolith. The browser calls one Fiber application. The API invokes Go services as functions rather than making internal HTTP calls, and those services own agent lifecycle, conversations, cron scheduling, profile files, snapshots, and edition limits.

The frontend lives in `frontend/` and is compiled into static assets served by the Go binary. The backend entrypoint is `cmd/main.go`. Business rules live under `services/<service_name>`; safe profile I/O is concentrated in `internal/profile`; runtime execution is behind `internal/runtime`; persistence is behind `internal/repositories`; transport code is under `internal/api`, with centralized route assembly in `internal/v1/routes/SetupRoutes.go`.

Community storage is file-only. Versioned agent metadata lives in each profile `config.yaml`, conversations and messages live in the profile session tree, cron definitions are individual YAML files, usage is one atomically replaced user aggregate, and notifications are individual JSON files. Writes use same-directory temporary files, fsync, rename, and a process lock. PostgreSQL, Redis, and Kafka dependencies live only in `open-lumora-enterprise`; neither the Community binary nor its Go module depends on them. No ORM is used in either repository.

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

Host development defaults to the Hermes CLI adapter. The Docker deployment
uses a separate extended Hermes runtime image. Studio calls only the enterprise
gateway; the gateway privately proxies Hermes `/v1/runs`, `/events`, `/stop`,
and `/approval` plus the 9router API. Studio, gateway, and runtime share the
profile volume so every absolute profile path has the same meaning without
granting the browser or host direct runtime access.

The gateway adapter starts a profile API server lazily and reuses it for that profile until the Go process stops. `CONTAINER_IDLE_ENABLED` and `CONTAINER_IDLE_TIMEOUT_MINUTES` remain policy metadata for a future managed-container controller; the local OSS application container does not stop itself because doing so would also remove its UI and API.

## Editions and quotas

`pkg/studio` is the public application-composition seam and `pkg/studio/contract` contains downstream-safe repository, runtime, lifecycle, and DTO contracts. The open-source policy returns a local principal and explicit limits for agents, cron definitions, parallel cron runs, daily/monthly cron runs, and provider connections. HTTP middleware exposes consistent `429` responses and rate-limit headers; services repeat enforcement for schedulers and other non-HTTP callers. Daily and monthly usage reserve together in one atomic file mutation, and parallel execution is isolated per principal.

A downstream enterprise composition can implement the same policy with gRPC token verification, tenant-aware plan lookup, RBAC, audit, and licensed limits. `-1` means unlimited for numeric limits. The enterprise control plane remains responsible for distributed reservations, container resource classes, billing, and managed telemetry; the local application remains usable without that control plane.

The product-level availability and recommended Free, Pro, and Enterprise values are maintained in [plans, features, and limits](plans.md). Paid PostgreSQL repositories and infrastructure were moved into the enterprise repository; later managed features continue to compose through the public contracts rather than importing Community internals.
