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

Runtime analytics read only the current profiles' Hermes `state.db` ledgers.
Runtime does not open a router SQLite database or call router management usage
APIs. Central personal/organization limits and durable usage history are served
by Control from the centralized router data path.

Provider connections, credentials, connection tests, and organization limits
are administered through the authenticated Control API. Runtime exposes no
provider-connection mutation routes and its API key cannot be used with
router management paths.

`GET /providers` and `GET /providers/{provider_id}/models` are read-only views
of the model catalog authorized for the current workload. Runtime obtains that
catalog from the centralized router's OpenAI-compatible `GET /models` endpoint.
`GET /providers/{provider_id}/models/{model}/reasoning` returns those same
provider/model-specific values plus `default_reasoning`; model IDs containing
`/` are supported. `default_reasoning` is derived from the live ordered model
catalog: the second supported level is preferred, the only level is used when
there is one, and models without reasoning metadata return `auto`. Agent and
Smart Route reasoning may be stored as `auto`; concrete model execution
resolves it through this metadata instead of a hard-coded effort.
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

## Safe marketplace package export

`POST /xnobrain/api/runtime/v1/marketplace/agents/{agent_id}/export` builds a
typed, deterministic publication package from the selected existing local agent.
The request is `{"license":"MIT"}`; the response data contains the Control
candidate fields `definition`, `requested_permissions`, `compatibility`, and
`license`, plus `schema_version`, `source_agent_id`, a canonical `sha256:`
`digest`, an ordered file manifest, enforced limits, and an exclusion report.
The digest covers the definition, requested permissions, compatibility, and
license using sorted compact UTF-8 JSON, matching marketplace version digest
material.

The export allowlist contains required `SOUL.md` and `workspace/AGENTS.md`,
enabled skill `SKILL.md` files, and UTF-8 regular files in each skill's
`references/`, `scripts/`, and `assets/` directories with approved extensions.
Only display name, description, model slot, and reasoning effort are copied
from profile configuration/metadata. Tool and MCP names are declarations; MCP
URLs, commands, headers, environment and other connection details are not
exported. Runtime grants no requested permission as a side effect of export.

The export is bounded to 202 files, 1,000,000 bytes per file, 10,000,000 total
bytes, 100 skills, 100 safe skill assets, and path depth 12. It rejects missing
required definition files, invalid UTF-8, non-regular files, symlinks anywhere
in the skills tree, path escapes, unsafe/case-colliding names, oversized input,
and common credential patterns in otherwise public content. It never traverses
or exports `.env`, credentials, provider/MCP connection values, `USER.md`,
memories, conversations/history/sessions, state databases, logs, caches, or
private workspace files. This endpoint is intentionally separate from the full
portable profile export and does not modify marketplace installation flow.

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


## Immutable conversation ownership

`POST /sessions?agent=<agent-id>` (and the hidden legacy `/conversations` alias)
accepts an optional typed `ownership_context`. Omission creates an explicit Personal
binding. Non-Personal bindings require the Control-verified private facade identity;
direct callers fail closed. The immutable context is returned by create, list, detail,
goal/subgoal, and durable run responses. Existing unlabeled Hermes sessions are
atomically backfilled Personal on first access. Run bodies cannot override owner,
organization, context, payer, or sponsor grant. See
[`contracts/conversation-ownership-v1.md`](contracts/conversation-ownership-v1.md).

## Unified workspace event stream

`GET /xnobrain/api/runtime/v1/events/stream` is the preferred browser SSE
subscription for shared workspace state. It multiplexes three event families:

- `agent.activity` — `{agents, updated_at}` activity snapshots;
- `workspace.stats` — the existing sandbox detail/VM status projection;
- `kanban.connected` and `kanban.task` — default-board connection and task events.

The Runtime starts one concurrent producer for each family and fans them into a
bounded queue. Disconnecting the SSE response cancels every producer. The
legacy `/agents/activity/stream`, `/sandboxes/detail/stream`, and
`/kanban/boards/{board_slug}/events/stream` endpoints remain available for
backward compatibility, but new browser code should use the unified stream.

## Organization artifact workspace adapter

The private Runtime exposes inspect, approved publish, and verified import operations under `/xnobrain/api/runtime/v1/agents-workspaces/{agent_id}/organization-artifacts`. Transfer capabilities are supplied ephemerally by Control. Imports reject unsafe paths/protected profile areas, stage bytes, enforce size/SHA-256, and atomically rename into the selected workspace. Publish requires `approved: true` and never returns a local absolute path.

### Automatic model routing

Selecting **Auto** for a provider randomizes the workload-scoped connected model
catalog for that provider. Selecting the system **Auto** blend randomizes all
connected models across providers. The first candidate is used for the run and
the remaining unique candidates are attached to the embedded agent's bounded
fallback chain over the same authenticated router transport. Model-not-found,
quota/rate-limit, malformed-response, and supported transient provider failures
can therefore advance to another candidate. Explicit models and user-created
blend strategies retain their existing behavior.

## Agent Maker blueprint drafts

Runtime exposes the pre-scaffold Agent Maker lifecycle under
`/xnobrain/api/runtime/v1/agent-blueprints`. `POST` creates a draft in the
creator profile selected by the required `?agent=<agent-id>` query parameter;
`GET /{blueprint_id}` resumes it; and `PATCH /{blueprint_id}` requires
`expected_revision`. Draft records remain private below that creator profile's
`.xnobrain/agent-blueprints` directory. A complete typed blueprint returns its
normalized proposed file manifest and a canonical SHA-256 digest.

`POST /{blueprint_id}/approvals` requires the current revision, exact canonical
digest, and an explicit `approve` decision. It does not accept `approved_by`.
The approver is the authenticated subject asserted by Control's private gRPC facade;
missing, unsigned, or direct caller identity fails with `trusted_subject_required`.
The approval records separate content, permission, model, and context digests.
Changing intent, context, or blueprint content increments the revision and
invalidates approval. Approval does not create a profile.

Scaffold, certification, activation, and cancellation routes are intentionally
not exposed in this delivery. Existing profile creation cannot yet atomically
publish the complete approved file plan as a paused child without creating
history/state and copying inherited skills, so implementing scaffold with that
API would violate the Agent Maker safety contract.

## Skill lifecycle usage

`GET /xnobrain/api/runtime/v1/agents/{agent_id}/skills/usage` accepts the
existing `days` or `from`/`to` UTC range parameters plus additive `context`,
`cursor`, and `limit` (1–100) filters. Omitting the additive filters preserves
the personal-context behavior and historical `skill_view` fallback when no
lifecycle event log exists.

New embedded runs write only allowlisted metadata beneath
`profiles/<agent-id>/skill-usage/v1/events/`: stable event ID, lifecycle type,
agent/context/run/session IDs, skill ID and `sha256:` digest, attribution,
timestamp, duration/outcome, and tool name. Prompt text, skill content, tool
arguments/results, credentials, and absolute paths are never written. Stable
event IDs make duplicate delivery idempotent. The response distinguishes
requested, loaded, reference-read, tool-invoked, completed, and failed events;
tool provenance is explicitly multiple or unattributed where it is not
singular. Coverage reports the source, selected range/context,
instrumentation version, event count, and unattributed tool count. Historical
`state.db` inference remains labeled `observed_load_only` and does not
manufacture tool or error metrics.
