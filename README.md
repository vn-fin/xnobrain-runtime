# XNOBrain

For the complete local stack and browser UI, see the workspace root and
[`xnobrain-ui`](../xnobrain-ui/README.md). This repository documents the
Python/API runtime and native agent installation.

XNOBrain is a self-hosted React workspace for creating and running AI agents.
One Python/FastAPI process serves the default profile plus every named profile.
Managed LLM calls use the configured centralized router directly with a
API key issued by Control. The workspace does not install or start
router and stores no provider credentials or router usage database. There
is no per-profile API server.

The production OCI image compiles the XNOBrain API endpoint and its required
Python imports with Nuitka. `BUILD_MODE=onefile` (default) packages one
executable, while `BUILD_MODE=module` uses Nuitka standalone mode and copies the
compiled application directory for faster packaging and startup without
one-file extraction. Both modes expose `/usr/local/bin/app.so`. The image still
contains the upstream Hermes Python environment and office Python tools because
agent CLI, skill synchronization, and document tooling execute independently of
the API endpoint.

```text
browser -> Traefik -> React UI
                   -> XNOBrain runtime -> profile files
                                       -> central router -> LLMs
                                              |
                                              -> Control token introspection
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

Copy the environment template and set `RUNTIME_HERMES_HOME`,
`RUNTIME_LLM_ROUTER_URL`, and a provisioned API key:

```bash
cp .env.example .env
# Do not store a production API key in a committed file.
```

The coordinated stack is started from the workspace root:

```bash
make dev
```

The coordinated root stack builds the UI from `xnobrain-ui`, the runtime from
this repository, and the Go control plane. Runtime profiles and memory are
file-backed; the runtime image does not start PostgreSQL or Redis services.

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
and installs Hermes and agent tooling. The macOS target skips the
optional office-tool and browser-engine downloads to keep the installation fast:

```bash
make install-local
```

Then start the runtime API:

```bash
make -C xnobrain-runtime dev
```

Run the React/Vite UI separately with `make -C xnobrain-ui dev`, or start the
full Docker stack with `make dev` from the workspace root.

The existing `setup-linux.sh` remains the Docker/Compose installer.

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

`xnobrain.target` owns one long-running workspace process:

- `xnobrain-api.service` runs the integrated XNOBrain runtime on
  private port `3000`;
Agent executions are children of the API service. Persistent agent data is
stored under `/srv/xnobrain-data`. Runtime configuration is read from
`/etc/xnobrain/xnobrain.env`. The VM firewall must allow port `3000` only
from the authenticated workspace gateway.

Validation:

```bash
make check
make smoke-api
```

### Versioned runtime releases

This repository has no publish or deployment workflow. `xnobrain-release`
records an immutable runtime repository tag, builds `Dockerfile.backend`, publishes the OCI
image to GHCR, and deploys the coordinated stack. The workspace root
`.version`, the immutable release manifest, and `xnobrain-release/latest.json`
are the coordinated version sources.

Each release publishes an immutable runtime OCI tag:

```text
ghcr.io/vn-fin/xnobrain-runtime/xnobrain-runtime:0.0.15
```

The UI, control API, and Incus gateway receive the same release version. No
component repository publishes a mutable `latest` tag.

```text
ghcr.io/vn-fin/xnobrain-ui/xnobrain-ui:0.0.15
ghcr.io/vn-fin/xnobrain-control/brain-control-api:0.0.15
ghcr.io/vn-fin/xnobrain-control/incus-gateway:0.0.15
```

## Data safety

Named agent data belongs under the configured agent home. Skills are
written only to `skills/<skill-id>/SKILL.md` inside that profile. Memory,
config, and skill mutations create immutable local snapshots, and mutable
files use temp-file, fsync, and rename. Provider credentials remain encrypted
in centralized router infrastructure and never enter the workspace. Portable `.zip` exports include every regular file in the
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
