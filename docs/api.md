# HTTP API

FastAPI listens on port `8642` inside the runtime container and is exposed by
Traefik at the same origin as the UI. Swagger UI is `/docs`, ReDoc is `/redoc`,
and the generated schema is `/openapi.json`.

Open Lumora JSON endpoints use:

```json
{"success": true, "data": {}, "message": "ok", "status_code": 200}
```

Pydantic request contracts are declared under `open_lumora.models`. Native
Hermes CLI endpoints keep their upstream response contracts and coexist in the
same OpenAPI document.

Primary Open Lumora groups:

- `/agent-gateway/v1/agents` and `/profiles`: profile inventory and lifecycle.
- `/agent-gateway/v1/agents-configs`: default and per-agent config/prompts.
- `/agent-gateway/v1/agents-skills`: isolated skill installation and state.
- `/agent-gateway/v1/agents/{id}/memory` and `/snapshots`: durable mutations.
- `/agent-gateway/v1/agents-workspaces`: validated workspace file access.
- `/agent-gateway/v1/agents-mcp`: per-profile MCP configuration.
- `/conversations/v1/conversations`: sessions, messages, usage, SSE chat runs,
  stop, and Hermes approval resolution.
- `/agent-gateway/v1/providers`: filtered 9router connections, models, OAuth,
  API-key setup, status, testing, and disconnect.
- `/agent-gateway/v1/cron/jobs` and `/api/v1/notifications`: local scheduling.
- `/api/v1/teams`: bounded local delegation teams.
- `/api/v1/bundles`: credential-free export, inspect, dry-run, and apply.
- `/sandboxes/v1/me/sandboxes`: status for the unified container runtime.
- `/api/v1/system`, `/limits`, `/health`, and `/enterprise/features`: edition
  and deployment discovery.

## Streaming runs

Create a conversation, then post `{"input":"..."}` with
`Accept: text/event-stream` to:

```text
/conversations/v1/conversations/{conversation_id}/chat/stream?agent={agent_id}
```

The stream emits `run.started`, `message.delta`, and one terminal
`run.completed`, `run.failed`, or `run.cancelled` event, followed by `[DONE]`.
Active runs can be stopped and pending tool approvals resolved through the
corresponding `/runs/{run_id}/stop` and `/approval` endpoints.

## Portable profiles

```bash
curl -sS -X POST http://localhost/api/v1/bundles/export \
  -H 'Content-Type: application/json' \
  -d '{"agent_ids":["a12345"]}' \
  -o profiles.lumora

curl -sS -X POST http://localhost/api/v1/bundles/inspect \
  -F file=@profiles.lumora
```

Bundles reject unsafe paths, symlinks, duplicates, checksum mismatches, zip
bombs, undeclared profiles, and unsupported capabilities. Credentials, logs,
caches, and host runtime state are excluded. Imports remap collisions, reset
approvals, pause cron jobs, and disable unresolved team members.

## Enterprise

Community endpoints need no account. Enterprise dashboard, observability, and
device calls are available only when `ENTERPRISE_API_URL` is configured. Auth,
trace context, and no user content beyond the explicit request are forwarded.
