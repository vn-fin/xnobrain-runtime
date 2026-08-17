# Deployment

The runtime repository builds the API/agent container. The workspace root
Compose stack adds the UI, control plane, and Traefik:

```text
Traefik -> XNOBrain UI container
        -> XNOBrain runtime API container
```

```bash
make dev
```

Open `http://localhost:5152` for the UI and
`http://localhost:5152/xnobrain/api/runtime/swagger_docs` for Swagger.
Only Traefik publishes a host port. The runtime's named volume holds profiles,
teams, notifications, Hermes state, and OmniRoute credentials.

The runtime defaults to 4 CPUs and 4 GB RAM and requests a 100 GB writable
root disk. Increase `XNOBRAIN_RUNTIME_CPUS`,
`XNOBRAIN_RUNTIME_MEMORY`, or `XNOBRAIN_RUNTIME_DISK_SIZE` in `.env`
when the host has more capacity. CPU and RAM use cgroup limits. Docker enforces
the root-disk request only on storage drivers with per-container quota support;
durable profile data in the named volume follows the Docker host's volume
capacity.

The stack is local-only: it does not include or call an Enterprise API. The
optional `otel` Compose profile starts a local OpenTelemetry collector. It is
disabled by default and has no outbound exporter:

```bash
OTEL_ENABLED=true docker compose --profile otel up -d
```

The collector receives metadata-only spans from the runtime and writes basic
summaries to its container logs. `OTEL_EXPORTER_OTLP_ENDPOINT` is restricted to
the Compose collector or a loopback address.

Runtime logs use standard human-readable Python logging. Each record includes
the timestamp, level, logger name, and message, for example:

```text
2026-07-24 09:00:00 INFO xnobrain.http: HTTP GET /xnobrain/api/runtime/v1/health -> 200 (1.234 ms, trace_id=-)
```

Set `LOG_LEVEL` in `.env` to change the minimum level.

This repository's runtime build produces only:

- `xnobrain-runtime:<tag>` (FastAPI, agent engine, and provider runtime)

The UI image is built by `xnobrain-ui`.
