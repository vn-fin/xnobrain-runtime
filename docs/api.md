# HTTP API

All JSON endpoints use `{ "success", "data", "message", "status_code" }`. OSS mode accepts requests without authorization and maps them to the local user. Enterprise implementations may require a bearer token and validate it through the external auth gRPC service.

Primary route groups:

- `/agent-gateway/v1/agents` manages agents and per-agent config.
- `/agent-gateway/v1/agents-skills/:agent_id` manages isolated skills.
- `/agent-gateway/v1/agents/:agent_id/memory` manages memory and snapshots.
- `/agent-gateway/v1/agents-workspaces/:agent_id` provides safe workspace file access.
- `/agent-gateway/v1/agents/:agent_id/conversations` manages conversations and SSE runs.
- `/agent-gateway/v1/providers` delegates provider credentials and models to 9router.
- `/agent-gateway/v1/cron/jobs` manages serialized OSS cron jobs.
- `/sandboxes/v1/me/sandboxes` reports the local Hermes container runtime.
- `/api/v1/limits` reports the active plan, capabilities, quota usage, and reset windows.
- `/api/v1/bundles` exports, inspects, previews, and atomically applies portable profiles.
- `/api/v1/device` reports, pairs, and unpairs the optional outbound cloud connection.

Agent creation, cron creation, manual cron execution, and additive provider connections use the same quota checks at both the HTTP and service boundaries. A denied HTTP request returns `429` and standard `RateLimit-*` response headers. Scheduled cron execution also reserves monthly usage atomically, so it cannot bypass the HTTP middleware.

The complete feature matrix, recommended plan values, and non-rate-limit errors such as `plan_required`, import size, and storage exhaustion are defined in [plans, features, and limits](plans.md).

## Portable profiles

Export one or more profiles without provider credentials, provider endpoints,
host paths, logs, or Hermes home state:

```bash
curl -sS -X POST http://localhost:3000/api/v1/bundles/export \
  -H 'Content-Type: application/json' \
  -d '{"agent_ids":["a12345"]}' \
  -o profiles.lumora
```

Inspect and preview an import before applying it:

```bash
curl -sS -X POST http://localhost:3000/api/v1/bundles/inspect \
  -H 'Content-Type: application/vnd.open-lumora.bundle' \
  --data-binary @profiles.lumora
curl -sS -X POST http://localhost:3000/api/v1/bundles/dry-run \
  -F file=@profiles.lumora
curl -sS -X POST http://localhost:3000/api/v1/bundles/apply \
  -F file=@profiles.lumora
```

Apply is atomic. Existing IDs are remapped; credentials and persisted
approvals are never imported; imported cron jobs are paused; executable/script
workspace files are placed under the profile's `quarantine/` tree.

## Optional cloud connection

Self-hosted operation does not require a cloud account or connection. To opt in,
configure `DEVICE_CLOUD_URL` and the pinned `DEVICE_SIGNING_PUBLIC_KEY`, then use
`POST /api/v1/device/pair`. The process registers through outbound HTTPS, polls
with a reconnect cursor, validates signed expiring commands, and journals each
state before execution. `POST /api/v1/device/unpair` durably disables reconnect
and removes cloud tokens without deleting the device key or any profile data.

`PUT /agent-gateway/v1/providers/:provider_id/connect` replaces the existing account of that provider by default. Use `?replace=false` to add another account; the active edition policy determines how many connections of one provider type are allowed. This lets the local edition keep one Codex account while an enterprise policy allows several.

Cron creation accepts the existing `interval_minutes` shorthand or a canonical
five-field `schedule` plus an IANA `timezone`. `mode=managed` jobs are excluded
from both the local ticker and manual run endpoint; only a validated, journaled
device command can dispatch them. Missed-run notifications support idempotent
`run_latest` and policy-limited `replay_bounded` actions, and quota is reserved
only for executions that actually start.

Example:

```bash
agent_id=$(curl -fsS -X POST http://localhost:3000/agent-gateway/v1/agents \
  -H 'Content-Type: application/json' -d '{"name":"researcher"}' | jq -r .data.id)

curl -fsS -X POST "http://localhost:3000/agent-gateway/v1/agents-skills/$agent_id" \
  -H 'Content-Type: application/json' \
  -d '{"skill_id":"summarize","name":"Summarize","content":"# Summarize\n\nProduce a concise summary."}'
```
