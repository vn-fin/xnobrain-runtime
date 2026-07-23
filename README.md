# Brain4All

Brain4All is a self-hosted React workspace for creating and running Hermes
agents. One Python/FastAPI process extends the original Hermes CLI dashboard
application and serves the default profile plus every named profile. One
9router process provides LLM routing. There is no Go service, PostgreSQL,
Redis, or per-profile API server.

```text
browser -> Traefik -> React UI
                   -> FastAPI + original Hermes core -> profile files
                                                    -> 9router -> LLMs
```

## Start

Docker Compose is the supported distribution on Linux, macOS, and Windows:

```bash
make image
docker compose up -d
```

Open <http://localhost>. Swagger is available at <http://localhost/docs> and
the generated OpenAPI document at <http://localhost/openapi.json>.

For local development, install Python 3.12+, Hermes Agent, Node.js 22+, and npm:

```bash
./scripts/dev.sh
# or run only the API
python server.py
```

Validation:

```bash
make check
make smoke-api
```

## Data safety

Named agent data belongs under `DATA_DIR/profiles/<agent-id>/`. Skills are
written only to `skills/<skill-id>/SKILL.md` inside that profile. Memory,
config, and skill mutations create immutable local snapshots, and mutable
files use temp-file, fsync, and rename. Provider credentials remain owned by
Hermes/9router and are excluded from portable `.zip` profile archives.

Self-hosted agents, profiles, skills, memory, MCP, providers, teams, and local
cron are unlimited. The application has no managed control plane, login, or
Enterprise API integration.

## Optional local telemetry

Telemetry is off by default. To run the local-only OpenTelemetry collector,
which writes metadata-only span summaries to its own container logs, run:

```bash
OTEL_ENABLED=true docker compose --profile otel up -d
```

The runtime only accepts the Compose collector or a loopback OTLP endpoint; it
will not export telemetry to a remote host.

See [architecture](docs/architecture.md), [development](docs/development.md),
[deployment](docs/deployment.md), and [API guide](docs/api.md).

## License

Choose and add a license before public distribution.
