# HTTP API

FastAPI generates the authoritative interactive contract at
`/xnobrain/api/runtime/swagger_docs` and JSON schema at `/xnobrain/api/runtime/openapi.json`.
Hermes CLI native routes remain available under `/api`; Brain4All's
application API is exclusively namespaced beneath `/xnobrain/api/runtime/v1` and is
grouped by feature:

- `/xnobrain/api/runtime/v1/agents`, `/profiles`, config, skills, memory, workspaces,
  MCP, providers, and cron
- `/xnobrain/api/runtime/v1/conversations` for history and structured SSE runs
- `/xnobrain/api/runtime/v1/teams`, `/bundles`, `/notifications`, limits, and deployment
- `/xnobrain/api/runtime/v1/sandboxes` for local runtime information/setup

JSON responses use `{success,data,message,status_code}`. SSE sends structured
Hermes lifecycle objects and terminates with `data: [DONE]`.

The namespace and current version are defined once in
`brain4all/routes/definition.py`. Each feature owns a route module in
`brain4all/routes/` and a matching operation module in
`brain4all/handlers/operations/`; `routes/setup.py` only assembles those
groups. Business logic remains in the feature services under
`brain4all/services/`.

Runtime statistics are available as a snapshot at
`/xnobrain/api/runtime/v1/sandboxes/detail` and as one-second SSE updates at
`/xnobrain/api/runtime/v1/sandboxes/detail/stream`.

Portable profile example:

```bash
curl -fsS -X POST http://localhost:5152/xnobrain/api/runtime/v1/bundles/export \
  -H 'Content-Type: application/json' \
  -d '{"agent_ids":["agent-id"]}' -o profiles.zip
curl -fsS -X POST http://localhost:5152/xnobrain/api/runtime/v1/bundles/inspect \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost:5152/xnobrain/api/runtime/v1/bundles/dry-run \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost:5152/xnobrain/api/runtime/v1/bundles/apply \
  -F file=@profiles.zip
```

All application endpoints are local; the project does not expose Enterprise
proxy, device-pairing, dashboard, or observability routes.
