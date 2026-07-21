# Open Lumora

Open Lumora is a self-hosted React workspace for creating and running Hermes
agents. One Python/FastAPI process extends the original Hermes CLI dashboard
application and serves the default profile plus every named profile. One
9router process provides LLM routing. There is no Go service, PostgreSQL,
Redis, or per-profile API server.

```text
browser -> Traefik -> React UI
                   -> FastAPI + original Hermes core -> profile files
                                                    -> 9router -> LLMs
                   -> Enterprise API (optional, ENTERPRISE_API_URL)
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
Hermes/9router and are excluded from `.lumora` bundles.

Self-hosted agents, profiles, skills, memory, MCP, providers, teams, and local
cron are unlimited. `ENTERPRISE_API_URL` enables optional authenticated
features without becoming a dependency of local Hermes operation.

See [architecture](docs/architecture.md), [development](docs/development.md),
[deployment](docs/deployment.md), and [API guide](docs/api.md).

## License

Add the project license before public distribution.
