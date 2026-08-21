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
the response returns both boundaries as RFC3339 timestamps. A new conversation
run or team task is accepted only while every participating agent has
`spend_usd < weekly_usd`. The check happens once at top-level execution
acceptance; event streams, ordinary API routes, and internal team/sub-agent
calls do not repeat it. Rejection uses HTTP `429` with code
`weekly_budget_exceeded`. An accepted execution is never stopped mid-run when
it takes usage over the limit.

The provider catalog returns subscription connections first in curated
common-use order: OpenAI Codex, Claude Code, GitHub Copilot, Cursor, Grok Build,
xAI Grok, Kimi Code, Cline, Kilo Code, Kiro, Amazon Q Developer, ClinePass, and
Google Antigravity. GitHub Copilot, Grok Build, Kimi Code, Kilo Code, Kiro, and
Amazon Q use device-code authorization. Cline and ClinePass use the Cline
browser authorization flow. xAI Grok uses xAI's fixed-loopback PKCE flow and is
distinct from both the Grok Build subscription and the xAI API-key card. Cursor
uses OmniRoute's validated credential-import flow because the pinned provider
runtime does not expose browser OAuth for Cursor. OpenCode Go remains a guided
coding-plan key flow.

API-key credentials use one idempotent write contract:
`POST /xnobrain/api/runtime/v1/providers/{provider_id}/connections` with
`api_key` and, only for a custom OpenAI-compatible provider, `base_url`.
Preset providers always use their runtime-defined endpoint and ignore a
client-supplied base URL. Model inventories for OpenAI-compatible connections
come from OmniRoute's per-connection model catalog and are exposed under the
configured stable prefix; OmniRoute's UUID-backed node IDs are never public.
Subscription providers also expose only the union of models returned by
OmniRoute for their active, non-error connections. Their stable public model
prefixes are preserved, and models found only in OmniRoute's global catalog are
not exposed as usable subscription models. When OmniRoute publishes reasoning
effort aliases beside a base model, the runtime collapses those aliases into one
base-model row and derives its ordered `reasoning` values from the live catalog.
`GET /providers/{provider_id}/models/{model}/reasoning` returns those same
provider/model-specific values plus `default_reasoning`; model IDs containing
`/` are supported. `default_reasoning` is derived from the live ordered model
catalog: the second supported level is preferred, the only level is used when
there is one, and models without reasoning metadata return `auto`. Agent and
Smart Route reasoning may be stored as `auto`; concrete model execution
resolves it through this metadata instead of a hard-coded effort.
OpenCode Zen exposes only models returned for an active Zen API-key
connection; the legacy global `oc/*` free-model catalog is not listed.
Smart Route blends resolve at model-inference boundaries rather than only once
for an entire agent run. The initial user turn is classified once and reused;
later model continuations after tool results, goal-continuation prompts, and
delegated tasks are classified independently. Mixed-difficulty delegation
batches are grouped into cost-homogeneous execution waves, while explicit
delegation provider/model overrides remain authoritative. If a later routing
classification is temporarily unavailable, the active model continues instead
of failing the run. Supported reasoning values come from provider model
metadata and may include `none`, `minimal`, `xhigh`, `max`, and `ultra` in
addition to `low`, `medium`, and `high`.
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
