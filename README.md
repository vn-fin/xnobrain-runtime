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

Install Docker, Docker Compose, and make with the platform installer:

```bash
./scripts/setup-linux.sh
# or: ./scripts/setup-macos.sh
```

On Windows, run this in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-windows.ps1
```

Then start Brain4All without make:

```bash
docker compose up -d --build
```

Open <http://localhost:5152>. Swagger is available at
<http://localhost:5152/docs> and the generated OpenAPI document at
<http://localhost:5152/openapi.json>.

### Optional account UI

The frontend reads `/config.js` before it starts. Its built-in fallback remains
the offline-compatible optional `local-profile` mode. The checked-in standalone
trial uses `edition: "opensource"` with optional `xno-firebase` login; users can
still continue into the local server without signing in.

Optional remote product features use `api.remoteBaseUrl`. The checked-in local
trial points to `https://api.dev.xnoquant.io`, enables the Example tab, and
renders the authenticated `/auth/v1/me` response separately from the Account
view. Set `features.example: false` to remove that tab. Remote feature flags
only control presentation; the remote API must enforce every protected
capability.

The `xno-firebase` trial provider follows the XNOQuant browser flow: Firebase
email/password authentication, `GET /auth/v1/auth/token` to exchange the
Firebase ID token, `POST /auth/v1/auth/refresh`, and `GET /auth/v1/me`.
Because the current remote API has no logout endpoint, sign-out clears the
namespaced Brain4All browser tokens. This direct-browser token provider is for
integration testing; production managed deployment should prefer the
same-origin gateway provider and HttpOnly session cookies.

Cloud and Enterprise deployments reuse the same frontend build and replace
`/config.js`. The checked-in cloud example demonstrates required XNOQuant
login. For the recommended production gateway mode, configure:

- `GET /control/v1/bootstrap` to restore the active HttpOnly-cookie session;
- `POST /control/v1/auth/login` for the login form;
- `POST /control/v1/auth/logout` to end the session.

Workspace API calls use same-origin credentials. The Go gateway remains
responsible for authentication, authorization, and authenticated
user-to-workspace routing; frontend edition and feature values are
presentation controls, not security controls.

For a Docker-free Linux development installation, run the project installer. It
installs the Dockerfile-derived system and office tools, a project-local Hermes
Python environment under `.tools/python`, Node.js/npm, 9router, and the Codex,
Claude, and agent-browser CLIs:

```bash
./scripts/install-linux.sh
# or: make install-local
```

Then start local development (Vite 5173, FastAPI 8642, and 9router 20128):

```bash
npm run dev
# or: make dev
```

The existing `setup-linux.sh` remains the Docker/Compose installer. For
API-only work after the local install, use
`BRAIN4ALL_DEV_SKIP_ROUTER=1 make dev`.

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
