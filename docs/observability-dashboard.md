# Observability dashboard implementation

## Current feature summary

The public Studio remains the user-facing process and data/profile owner. Usage
observability is available only after login and device claim. Anonymous local
mode does not export telemetry or expose a local usage dashboard. An
authenticated installation follows this private chain:

```text
browser -> Traefik -> frontend or unified FastAPI/Hermes runtime
                              |
                              +-> Enterprise API -> ClickHouse aggregates
```

The browser cannot address `open-lumora-gateway`, runtime, or ClickHouse.
Traefik is the only service with a host port. Studio exposes two narrow proxy
reads for the dashboard:

- `GET /api/v1/dashboard/overview?window=24h|7d|30d|90d|365d`
- `GET /api/v1/dashboard/dependencies?window=24h|7d|30d|90d|365d`
- `GET /api/v1/observability/traces?window=...`
- `GET /api/v1/observability/traces/:trace_id`
- `GET /api/v1/observability/metrics?window=...&metric=...&runtime_id=...&container=...&limit=...`

The UI displays run success, agents and teams, tokens, estimated cost, p95
latency, errors, time-bucketed usage, CPU/memory/block-I/O/network, agent usage,
skills, tools, models, and a team/agent/skill/tool dependency graph. All time bucketing,
quantiles, sums, counts, and top-N reduction happen in ClickHouse. Raw spans,
log bodies, and metric samples are never sent to the frontend. The trace explorer is
the bounded exception: it returns at most 1,000 sanitized span metadata rows for
one authorized trace so the UI can render its waterfall and dependency graph.

## Runtime measurements

The Hermes event adapter follows the runtime-hook approach demonstrated by
`nujovich/hermes-telemetry`, while keeping Open Lumora's OTLP and ClickHouse
pipeline. Each run records the values Hermes or its provider actually returns:

| Scope | Measurements |
| --- | --- |
| LLM call | provider, requested/response model, input/output tokens, cache read/write tokens combined as cached tokens, estimated USD cost, latency, success/error |
| Agent run | hashed agent ID, display name, model/provider, interactive/automated mode, duration and status |
| Team run | hashed team ID, team name, parent/child agent spans, duration and status |
| Cron run | hashed cron and agent IDs, schedule-run duration and status |
| Tool/skill | safe name, call span, parent agent span and success/error |

Every completed tool or skill span includes `lumora.response.chars`, measured
as Unicode response characters. No response content is retained. Conversation
messages keep `trace_id` and `run_id` metadata so users can correlate chat and
execution without copying chat content into telemetry.

Claimed Studio exports through the OSS deployment's private OTel Collector. The
Collector batches and compresses OTLP into the gateway. The gateway acknowledges
after authentication, sanitization, plan retention assignment, and enqueue into
a bounded Go channel. Fixed workers retry ClickHouse inserts. There is no Kafka,
PostgreSQL telemetry copy, or SQLite telemetry inbox.

Repeated cumulative usage events use the highest reported value rather than
being summed twice. Missing provider usage remains zero instead of being
presented as exact. Prompts, model responses, tool arguments, credentials, and
unapproved raw IDs are removed before OTLP export. Opaque agent/conversation
references are retained only to let the trace explorer open the existing Hermes
chat; chat and agent logs remain in Hermes rather than ClickHouse.

## Review data

`DASHBOARD_SMOKE_DATA_ENABLED=true` is the review default. When the local
ClickHouse trace table is empty, the gateway inserts deterministic synthetic
traces, resource metrics, token usage, and dependency
spans. The UI always marks these results as **Demo data**. Set the option to
`false` before a production release; it never replaces or alters real rows.

`DASHBOARD_ENABLED=true` controls the server capability. Retention is assigned
per accepted row from the user plan: Basic 7 days, Pro 90 days, Pro Max 180
days, and Enterprise 365 days for traces, safe span events, and runtime metrics.

## Build and offline install

The public repository builds the frontend and unified Hermes runtime images.
The Enterprise repository builds only its API image and owns the ClickHouse
schema. Normal local startup builds directly from the root Dockerfiles and does
not write application binaries or image bundles under `bin/`.

```bash
make run
make -C ../open-lumora-enterprise build
```

Use the `authenticated` Compose profile to start the local Collector after
configuring login and tenant-bound telemetry credentials. An offline image
bundle remains available as an explicit release-only `make bundle` step.

Run `make smoke-api` after startup to validate safe read/status endpoints and
all six provider contracts through Traefik. OAuth starts are checked for a
non-empty authorization URL without changing any provider credentials.

Local scheduling is OSS-owned and unrestricted. Managed cloud scheduling and
future collaboration policy remain Enterprise API concerns.
