# Brain4All

For detailed local, Docker, and native VM installation instructions, see
[SETUP.md](SETUP.md).

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

Copy the environment template and enter the required `HERMES_HOME` and
`NINE_ROUTER_DATA_DIR` values. For Docker, use paths inside `/opt/data`:

```bash
cp .env.example .env
# Edit .env and set HERMES_HOME=/opt/data/hermes and
# NINE_ROUTER_DATA_DIR=/opt/data/nine-router.
```

Then start Brain4All without make:

```bash
docker compose up -d --build
```

Compose also starts the private Honcho API, deriver, PostgreSQL/pgvector, and
Redis services. `HONCHO_MEMORY_ENABLE=true` selects it as the default memory
provider. Honcho remains reachable only on the internal Compose network and
uses the runtime's local 9router endpoint for language-model requests.

Open <http://localhost:5152>. Swagger is available at
<http://localhost:5152/xnobrain/api/runtime/swagger_docs> and the generated OpenAPI
document at <http://localhost:5152/xnobrain/api/runtime/openapi.json>.

### Required account UI

XNOBrain requires an authenticated account. The Web application starts in
Enterprise mode with the `xno-firebase` provider and does not offer an
anonymous or browser-local profile. Copy `.env.example` to `.env` to configure
the Auth API, Brain Control API, and public Firebase Web API key.

Firebase is the only persistent browser login session. On every initial page
load or revisit, the frontend refreshes the Firebase identity token and calls
the configured XNOQuant `/token` and `/me` endpoints before mounting the
application. The returned XNOQuant access token exists only in memory and is
never written to local or session storage. Normal XNOBrain API requests
include that in-memory token as an `Authorization: Bearer <token>` header.
Presentation flags do not replace server-side authentication or authorization.

The `xno-firebase` provider follows the XNO browser flow: Firebase
email/password authentication, persistent Firebase refresh, fresh XNOQuant
token exchange, and identity lookup through the configured authentication
API. Because the current remote API has no logout endpoint, sign-out clears
the persisted Firebase session and the in-memory XNOQuant token.

The local workspace API remains same-origin. Cloud authentication and control
features are configured with `XNOBRAIN_AUTH_BASE_URL`,
`XNOBRAIN_BRAIN_CONTROL_BASE_URL`, `XNOBRAIN_WEB_EDITION`, and
`XNOBRAIN_FIREBASE_API_KEY`.

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
`XNOBRAIN_DEV_SKIP_ROUTER=1 make dev`.

### Native VM services

Cloud VM images can run the backend without Docker or a per-workspace
frontend. Install the native runtime at `/opt/brain4all` with
`scripts/install-linux.sh`, then install the systemd units:

```bash
cd /opt/brain4all
sudo ./scripts/install-systemd-services.sh
sudoedit /etc/brain4all/brain4all.env
sudo systemctl start brain4all.target
```

`brain4all.target` owns the two long-running processes:

- `brain4all-api.service` runs FastAPI and the integrated Hermes runtime on
  port `8642`;
- `brain4all-9router.service` runs 9router on loopback port `20128`.

Hermes agent executions are children of the API service. The service user's
home is `/srv/brain4all-data/home`, so the standard homes are easy to inspect
at `~/.hermes` (`/srv/brain4all-data/home/.hermes`) and `~/.9router`
(`/srv/brain4all-data/home/.9router`). Runtime configuration is read from
`/etc/brain4all/brain4all.env`. The VM firewall must allow port `8642` only
from the authenticated workspace gateway; port `20128` must remain private to
the VM.

Validation:

```bash
make check
make smoke-api
```

### Versioned runtime releases

`.version` is the image-version source of truth for every environment. Dev
publishes from `main` only when that file changes. Staging and production
publish on every push to their environment branch, so merging `main` into
`staging` or `prod` deploys the version recorded in the merged `.version`.
Manual dispatch remains available to retry the same version.

Each release publishes the common frontend to GHCR with immutable and moving
tags:

```text
ghcr.io/vn-fin/xnobrain-runtime/xnobrain-frontend:dev-0.0.13
ghcr.io/vn-fin/xnobrain-runtime/xnobrain-frontend:dev-latest
```

The same workflow deploys one VM-builder service to the environment Swarm. It
runs on a node labelled `incus=true`, embeds the checked-out source instead of
cloning it with a repository token, installs and health-checks the native
backend in a temporary Ubuntu VM, and publishes this immutable Incus alias:

```text
xnobrain-runtime-dev-0.0.13
```

Staging and production use the same `.version` value with their own environment
prefix. Re-running an existing environment/version skips the native VM build
after verifying the alias property. The managed control plane selects a release
by configuring the runtime image name and semantic version; it does not build
runtime artifacts.

## Data safety

Named agent data belongs under `~/.hermes/profiles/<agent-id>/`. Skills are
written only to `skills/<skill-id>/SKILL.md` inside that profile. Memory,
config, and skill mutations create immutable local snapshots, and mutable
files use temp-file, fsync, and rename. Provider credentials remain owned by
Hermes/9router. Portable `.zip` exports include every regular file in the
selected profile directory while redacting credential values; imports discard
archived credential files and inherit them from the destination installation.

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
