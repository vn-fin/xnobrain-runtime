# HTTP API

FastAPI generates the authoritative interactive contract at `/docs` and JSON
schema at `/openapi.json`. Hermes CLI native routes remain available under
`/api`; Brain4All adds these compatibility groups:

- `/agent-gateway/v1/agents`, `/profiles`, config, skills, memory, workspaces,
  MCP, providers, and cron
- `/conversations/v1/conversations` for history and structured SSE runs
- `/api/v1/teams`, `/bundles`, `/device`, `/notifications`, limits, deployment,
  dashboard, and observability
- `/sandboxes/v1/me/sandboxes` for local runtime information/setup

JSON responses use `{success,data,message,status_code}`. SSE sends structured
Hermes lifecycle objects and terminates with `data: [DONE]`.

Runtime statistics are available as a snapshot at
`/sandboxes/v1/me/sandboxes/detail` and as one-second SSE updates at
`/sandboxes/v1/me/sandboxes/detail/stream`.

Portable profile example:

```bash
curl -fsS -X POST http://localhost/api/v1/bundles/export \
  -H 'Content-Type: application/json' \
  -d '{"agent_ids":["agent-id"]}' -o profiles.zip
curl -fsS -X POST http://localhost/api/v1/bundles/inspect \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost/api/v1/bundles/dry-run \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost/api/v1/bundles/apply \
  -F file=@profiles.zip
```

Local endpoints do not require an Enterprise login. Enterprise proxy routes
return `503 enterprise_unavailable` when `ENTERPRISE_API_URL` is absent.
