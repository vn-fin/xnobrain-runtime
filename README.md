# Open Lumora

Open Lumora is a self-hosted React workspace for building and running Hermes
agents. Its backend is one Python/FastAPI server built on the original Hermes
CLI FastAPI application. The same server manages the default profile and every
named profile; it does not start a separate HTTP server per agent.

The Community edition has no Go backend, PostgreSQL, ORM, Redis, or required
cloud service. Agent state is stored atomically under
`DATA_DIR/profiles/<agent-id>/`. The optional Enterprise relationship is one
outbound `ENTERPRISE_API_URL`; an unavailable Enterprise API cannot restrict
local agents, profiles, skills, memory, MCP, providers, teams, or cron.

## Start

Docker Compose is the supported installation path:

```bash
make run
```

This builds both application images from the root Dockerfiles and starts the
Compose stack. It does not create a native binary under `bin/`.

Open <http://localhost>. The API is routed through the same Traefik address,
and interactive Swagger documentation is at <http://localhost/docs>.

There are two Open Lumora application images:

- `open-lumora-frontend`: the React UI served by unprivileged nginx.
- `open-lumora-hermes-runtime`: FastAPI, the original Hermes core/CLI, and the
  bundled 9router process.

Traefik is the edge router. The optional `authenticated` Compose profile adds
only the OpenTelemetry Collector.

## Development

Install Python 3.12+, Node.js 22+, npm, and Hermes Agent. The root entrypoint is:

```bash
python server.py
```

It listens on port `8642` by default and keeps FastAPI Swagger enabled. Run the
frontend separately with `make src`, or use `make dev` for both development
processes. Validation is:

```bash
make check
make smoke-api   # with the Compose stack running
```

## Profile safety

Agent `abc123` owns `DATA_DIR/profiles/abc123`. Agent-created skills are always
written to `profiles/abc123/skills/<skill-id>/SKILL.md`, memory is profile-local,
and every memory or skill mutation creates an immutable snapshot before success.
Portable bundles exclude credentials, logs, cache, and host-only runtime state.

Provider credentials are owned by the bundled local 9router service and are
never returned by the Open Lumora API. Telemetry records route/status/timing and
safe identifiers, never prompts, responses, memory, skill content, or secrets.

See [architecture](docs/architecture.md), [development](docs/development.md),
[deployment](docs/deployment.md), and the [HTTP API](docs/api.md).

## License

Add the project license before public distribution.
