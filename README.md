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
the offline-compatible optional `local-profile` mode. Set
`features.login: true` to show sign-in, the Account item in the left sidebar,
and confirmed sign-out. The checked-in standalone trial uses
`edition: "opensource"` with optional `xno-firebase` login; users can still
continue into the local server without signing in.

The Account popup loads the configured authenticated identity response only when it
is opened. A page refresh restores the saved token locally without calling
`/me`. While login is enabled, normal Brain4All API requests include the saved
access token as an `Authorization: Bearer <token>` header. Presentation flags
do not replace server-side authentication or authorization.

The `xno-firebase` provider follows the XNO browser flow: Firebase
email/password authentication, token exchange, token refresh, and identity
lookup through the configured control API.
Because the current remote API has no logout endpoint, sign-out clears the
namespaced Brain4All browser tokens. This direct-browser token provider is for
integration testing; production managed deployment should prefer the
same-origin gateway provider and HttpOnly session cookies.

Cloud and Enterprise deployments reuse the same frontend build and replace
`/config.js`. Set `api.remoteBaseUrl` to the Brain4All runtime API and
`api.controlBaseUrl` to the XNOBrain control API. The same values can be
provided at build time as `VITE_API_BASE_URL` and
`VITE_CONTROL_API_BASE_URL`. The checked-in cloud example demonstrates
required XNO login. For the recommended production gateway mode, configure:

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
For a Docker-free Linux or macOS development installation, run the project
installer. It creates a project-local Hermes Python environment under
`.tools/python` and installs Node.js/npm, 9router, and the agent CLIs. The
macOS target skips the optional office-tool and browser-engine downloads to
keep the development setup fast:

```bash
make install-local
```

Then start local development (Vite 5173, FastAPI 8642, and 9router 20128):

```bash
npm run dev
# or: make dev
```

The existing `setup-linux.sh` remains the Docker/Compose installer. For
API-only work after the local install, use
`BRAIN4ALL_DEV_SKIP_ROUTER=1 make dev`.

### Native VM services

Cloud VM images can run the backend without Docker or a per-workspace
frontend. Install the native runtime at `/opt/brain4all` with
`scripts/install-linux.sh`, then install the systemd units:

```bash
cd /opt/brain4all
sudo ./scripts/install-systemd-services.sh --start
```

`brain4all.target` owns the two long-running processes:

- `brain4all-api.service` runs FastAPI and the integrated Hermes runtime on
  port `8642`;
- `brain4all-9router.service` runs 9router on loopback port `20128`.

Hermes agent executions are children of the API service. Persistent state is
stored below `/srv/brain4all-data`, and runtime configuration is read from
`/etc/brain4all/brain4all.env`. The VM firewall must allow port `8642` only
from the authenticated workspace gateway; port `20128` must remain private to
the VM.

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
