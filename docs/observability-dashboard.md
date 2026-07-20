# Observability dashboard implementation

## Current feature summary

The public Studio remains the user-facing process and data/profile owner. In a
local installation every browser request follows this private chain:

```text
browser -> Traefik -> frontend or Studio API -> open-lumora-gateway -> Hermes runtime
                                                |
                                                +-> ClickHouse aggregates
```

The browser cannot address `open-lumora-gateway`, runtime, or ClickHouse.
Traefik is the only service with a host port. Studio exposes two narrow proxy
reads for the dashboard:

- `GET /api/v1/dashboard/overview?window=24h|7d|30d`
- `GET /api/v1/dashboard/dependencies?window=24h|7d|30d`

The UI displays run success, agents and teams, tokens, estimated cost, p95
latency, errors, time-bucketed usage, CPU/memory, agent usage, skills, tools,
models, and a team/agent/skill/tool dependency graph. All time bucketing,
quantiles, sums, counts, and top-N reduction happen in ClickHouse. Raw spans,
logs, and metric samples are never sent to the frontend.

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

Repeated cumulative usage events use the highest reported value rather than
being summed twice. Missing provider usage remains zero instead of being
presented as exact. Prompts, model responses, tool arguments, credentials, and
raw local IDs are removed before the disk spool and OTLP export.

## Review data

`DASHBOARD_SMOKE_DATA_ENABLED=true` is the review default. When the local
ClickHouse trace table is empty, the gateway inserts deterministic synthetic
traces, operational error logs, resource metrics, token usage, and dependency
spans. The UI always marks these results as **Demo data**. Set the option to
`false` before a production release; it never replaces or alters real rows.

`DASHBOARD_ENABLED=true` controls the feature itself. ClickHouse retention is
seven days by default and can be changed with `CLICKHOUSE_RETENTION_DAYS`.

## Build and offline install

The enterprise repository builds the gateway and Hermes runtime images and
owns PostgreSQL and ClickHouse. The public repository builds only separate
backend and frontend images and writes those two images into a compressed
bundle under `bin/images`. Every bundle part is 47 MB and checksum verified.

```bash
make build
make -C ../open-lumora-enterprise build
make install
```

Public `make install` checks and concatenates only the frontend/backend bundle,
ensures the shared private network exists, and starts Traefik, frontend, and
Studio. The enterprise installation independently restores and starts the
gateway, runtime, PostgreSQL, ClickHouse, and workers.

Run `make smoke-api` after startup to validate safe read/status endpoints and
all six provider contracts through Traefik. OAuth starts are checked for a
non-empty authorization URL without changing any provider credentials.

The schedule listener is an enterprise-owned process. Studio serves CRUD/API
requests while the listener evaluates due work against the shared profile
volume and sends executions through the private gateway/runtime chain.
