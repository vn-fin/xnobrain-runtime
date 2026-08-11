# Deployment

The default Compose stack has two application containers plus Traefik:

```text
Traefik -> frontend container
        -> combined FastAPI + Hermes + 9router container
```

```bash
docker compose up -d --build
```

Open `http://localhost:5152` for the UI and
`http://localhost:5152/xnobrain/api/runtime/swagger_docs` for Swagger.
Only Traefik publishes a host port. The runtime's named volume holds profiles,
teams, notifications, Hermes state, and 9router credentials.

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
2026-07-24 09:00:00 INFO brain4all.http: HTTP GET /xnobrain/api/runtime/v1/health -> 200 (1.234 ms, trace_id=-)
```

Set `LOG_LEVEL` in `.env` to change the minimum level.

Builds produce only:

- `brain4all-frontend:<tag>`
- `brain4all-hermes-runtime:<tag>` (FastAPI, Hermes, and 9router)

`make build` also creates the checksummed split OCI bundle under `bin/images`.
