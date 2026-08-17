# XNOBrain

For the complete local stack and browser UI, see the workspace root and
[`xnobrain-ui`](../xnobrain-ui/README.md). This repository documents the
Python/API runtime and native agent/provider installation.

XNOBrain is a self-hosted React workspace for creating and running AI agents.
One Python/FastAPI process serves the default profile plus every named profile,
and one private provider runtime handles LLM routing. There is no per-profile
API server.

```text
browser -> Traefik -> React UI
                   -> XNOBrain runtime -> profile files
                                       -> provider runtime -> LLMs
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

Copy the environment template and set `RUNTIME_AGENT_HOME` and
`RUNTIME_PROVIDER_DATA_DIR`. For Docker, use paths inside `/opt/data`:

```bash
cp .env.example .env
# Edit .env and set RUNTIME_AGENT_HOME=/opt/data/agent and
# RUNTIME_PROVIDER_DATA_DIR=/opt/data/provider-runtime.
```

The coordinated stack is started from the workspace root:

```bash
make dev
```

The control-plane Compose stack builds the UI from `xnobrain-ui`, the runtime
from this repository, and the managed administrator service. It also starts
the private Honcho API, deriver, PostgreSQL/pgvector, and Redis services.

Open <http://localhost:5173>. Swagger is available at
<http://localhost:5173/xnobrain/api/runtime/swagger_docs> and the generated OpenAPI
document at <http://localhost:5173/xnobrain/api/runtime/openapi.json>.

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

For a Docker-free Linux or macOS runtime installation, run the project
installer. It creates a project-local Python environment under `.tools/python`
and installs the provider runtime and agent CLIs. The macOS target skips the
optional office-tool and browser-engine downloads to keep the installation fast:

```bash
make install-local
```

Then start the runtime API and provider runtime:

```bash
make -C xnobrain-runtime dev
```

Run the React/Vite UI separately with `make -C xnobrain-ui dev`, or start the
full Docker stack with `make dev` from the workspace root.

The existing `setup-linux.sh` remains the Docker/Compose installer. For
API-only work after the local install, use
`XNOBRAIN_DEV_SKIP_ROUTER=1 make dev`.

### Native VM services

Cloud VM images can run the backend without Docker or a per-workspace
frontend. Install the native runtime at `/opt/xnobrain` with
`scripts/install-linux.sh`, then install the systemd units:

```bash
cd /opt/xnobrain
sudo ./scripts/install-systemd-services.sh
sudoedit /etc/xnobrain/xnobrain.env
sudo systemctl start xnobrain.target
```

`xnobrain.target` owns the two long-running processes:

- `xnobrain-api.service` runs the integrated XNOBrain runtime on
  private port `3000`;
- the provider-runtime service listens only on loopback port `20128`.

Agent executions are children of the API service. Persistent agent and provider
data is stored under `/srv/xnobrain-data`. Runtime configuration is read from
`/etc/xnobrain/xnobrain.env`. The VM firewall must allow port `3000` only
from the authenticated workspace gateway; port `20128` must remain private to
the VM.

Validation:

```bash
make check
make smoke-api
```

### Versioned runtime releases

The workspace root `.version` is the image-version source of truth for every environment. Dev
publishes from `main` only when that file changes. Staging and production
publish on every push to their environment branch, so merging `main` into
`staging` or `prod` deploys the version recorded in the merged workspace `.version`.
Manual dispatch remains available to retry the same version.

Each release publishes the UI image to GHCR with immutable and moving
tags:

```text
ghcr.io/vn-fin/xnobrain-ui/xnobrain-ui:dev-0.0.14
ghcr.io/vn-fin/xnobrain-ui/xnobrain-ui:dev-latest
```

The same workflow deploys one VM-builder service to the environment Swarm. It
runs on a node labelled `incus=true`, embeds the checked-out source instead of
cloning it with a repository token, installs and health-checks the native
backend in a temporary Ubuntu VM, and publishes this immutable Incus alias:

```text
xnobrain-runtime-dev-0.0.14
```

Staging and production use the same workspace `.version` value with their own environment
prefix. Re-running an existing environment/version skips the native VM build
after verifying the alias property. The managed control plane selects a release
by configuring the runtime image name and semantic version; it does not build
runtime artifacts.

## Data safety

Named agent data belongs under the configured agent home. Skills are
written only to `skills/<skill-id>/SKILL.md` inside that profile. Memory,
config, and skill mutations create immutable local snapshots, and mutable
files use temp-file, fsync, and rename. Provider credentials remain owned by
the local provider runtime. Portable `.zip` exports include every regular file in the
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
