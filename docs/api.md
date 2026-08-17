# HTTP API

FastAPI generates the authoritative interactive contract at
`/xnobrain/api/runtime/swagger_docs` and JSON schema at `/xnobrain/api/runtime/openapi.json`.
Hermes CLI native routes remain available under `/api`; XNOBrain's
application API is exclusively namespaced beneath `/xnobrain/api/runtime/v1` and is
grouped by feature:

- `/xnobrain/api/runtime/v1/agents`, `/profiles`, config, skills, memory, workspaces,
  MCP, providers, and cron
- `/xnobrain/api/runtime/v1/conversations` for history and structured SSE runs
- `/xnobrain/api/runtime/v1/analytics` for UTC usage series and per-agent weekly budgets
- `/xnobrain/api/runtime/v1/teams`, `/bundles`, `/notifications`, limits, and deployment
- `/xnobrain/api/runtime/v1/sandboxes` for local runtime information/setup

JSON responses use `{success,data,message,status_code}`. SSE sends structured
Hermes lifecycle objects and terminates with `data: [DONE]`.

The namespace and current version are defined once in
`xnobrain/routes/definition.py`. Each feature owns a route module in
`xnobrain/routes/` and a matching operation module in
`xnobrain/handlers/operations/`; `routes/setup.py` only assembles those
groups. Business logic remains in the feature services under
`xnobrain/services/`.

Runtime statistics are available as a snapshot at
`/xnobrain/api/runtime/v1/sandboxes/detail` and as one-second SSE updates at
`/xnobrain/api/runtime/v1/sandboxes/detail/stream`.

Agent execution activity is available as a compatibility snapshot at
`/xnobrain/api/runtime/v1/agents/activity`. UI clients should use the
`/xnobrain/api/runtime/v1/agents/activity/stream` SSE endpoint, which emits an
`activity` event initially and whenever an agent changes between `idle` and
`running`. The runtime caches the database-backed Kanban portion for ten
seconds so status monitoring does not continuously scan every board.

Agent budgets use `weekly_usd` at
`GET|PUT /xnobrain/api/runtime/v1/analytics/agents/{agent_id}/budget`. The
minimum configured limit is USD 1; clearing the value restores the USD 20
default. Weeks run Sunday 00:00 through the following Sunday 00:00 UTC, and
the response returns both boundaries as RFC3339 timestamps. A new chat run is
accepted only while `spend_usd < weekly_usd`. An accepted run is never stopped
mid-turn when it takes usage over the limit.

The provider catalog returns subscription connections first in common-use
order: Claude Code, OpenAI Codex, GitHub Copilot, Cursor, Grok Build, Google
Antigravity, and OpenCode Go. GitHub Copilot and Grok Build use device-code
authorization. Cursor uses OmniRoute's validated credential-import flow because
the pinned provider runtime does not expose browser OAuth for Cursor. The xAI
API-key card remains separate from the Grok Build subscription card.

API-key credentials use one idempotent write contract:
`POST /xnobrain/api/runtime/v1/providers/{provider_id}/connections` with
`api_key` and, only for a custom OpenAI-compatible provider, `base_url`.
Preset providers always use their runtime-defined endpoint and ignore a
client-supplied base URL. Model inventories for OpenAI-compatible connections
come from OmniRoute's per-connection model catalog and are exposed under the
configured stable prefix; OmniRoute's UUID-backed node IDs are never public.
OpenCode Zen exposes only models returned for an active Zen API-key
connection; the legacy global `oc/*` free-model catalog is not listed.
The runtime derives a SHA-256 fingerprint for the provider-runtime identity;
submitting the same key updates its existing connection while a different key
adds another connection. The raw key and full fingerprint are never returned.
The previous `PATCH /providers/{provider_id}/update` route has been removed.
Individual keys are tested with
`POST /providers/{provider_id}/connections/{connection_id}/test` and removed
with `DELETE /providers/{provider_id}/connections/{connection_id}`.

Portable profile example:

```bash
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/export \
  -H 'Content-Type: application/json' \
  -d '{"agent_ids":["agent-id"]}' -o profiles.zip
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/inspect \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/dry-run \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/apply \
  -F file=@profiles.zip
```

All application endpoints are local; the project does not expose Enterprise
proxy, device-pairing, dashboard, or observability routes.
