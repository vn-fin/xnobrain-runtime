# Brain4All Setup

This guide covers the open-source, self-hosted Brain4All runtime in this
repository. Local development runs three processes:

| Component | Default address | Purpose |
| --- | --- | --- |
| React/Vite | `http://127.0.0.1:5173` | Browser UI |
| FastAPI + Hermes | `http://127.0.0.1:8642` | Brain4All API and agent runtime |
| 9router | `http://127.0.0.1:20128` | Model and provider routing |

Vite automatically tries the next available port when `5173` is occupied.
For example, if ports `5173` and `5174` are already used, open
`http://127.0.0.1:5175`.

## Local Linux development

Run all commands from the `brain4all/` repository root.

### 1. Check the host

Use a normal user account, not `root`:

```bash
./scripts/install-linux.sh --check
```

The installer supports `apt`, `dnf`, and `pacman`. It uses `sudo` only when it
needs to install system packages.

### 2. Install the project-local runtime

```bash
make install-local
```

This installs or prepares:

- Hermes and its Python environment under `.tools/hermes-agent/`;
- the project Python entry point under `.tools/python/`;
- Node.js and npm when a suitable system Node is unavailable;
- 9router and supported agent CLIs under `.tools/`;
- optional browser and office/document tooling.

For a smaller installation when the omitted tools are already present or not
needed:

```bash
./scripts/install-linux.sh --skip-system-packages --skip-office-tools --skip-browser
```

### 3. Start local development

```bash
make dev
```

`make dev` explicitly starts the open-source local mode:

- API requests go to `http://127.0.0.1:8642`;
- the frontend edition is `opensource`;
- browser authentication is disabled;
- managed XNOBrain auth and control-plane URLs are disabled;
- provider calls go through the local 9router process.

The local dev command intentionally overrides managed frontend values that may
exist in `.env`, including an API URL such as `http://localhost:3000`.

Open the URL printed by Vite. Stop all three development processes with
`Ctrl+C` in the terminal running `make dev`.

### 4. Confirm the local API

```bash
curl http://127.0.0.1:8642/api/brain/v1/agents
curl http://127.0.0.1:8642/api/brain/v1/providers
```

Swagger is available at:

```text
http://127.0.0.1:8642/api/brain/swagger_docs
```

The sandbox statistics endpoint is an SSE stream, so it remains connected:

```bash
curl -N http://127.0.0.1:8642/api/brain/v1/sandboxes/detail/stream
```

It should return HTTP 200 and `event: stats`; it must not call port `3000` or
return a managed-API 401 response.

## Local data locations

The default host paths are:

```text
~/.hermes/                         default Hermes profile and shared state
~/.hermes/profiles/<agent-id>/     named agent profiles
~/.hermes/profiles/<agent-id>/cron profile-scoped cron jobs and executions
~/.9router/                        local provider/router state
```

You can choose other locations before installation or startup:

```bash
HERMES_HOME=/path/to/hermes-home \
NINE_ROUTER_DATA_DIR=/path/to/9router-home \
make dev
```

Do not expect `~/.hermes` inside a VM to refer to the host user's home. For a
native Brain4All VM service installation, the service user's home is normally
`/srv/brain4all-data/home`, so Hermes data is stored at:

```text
/srv/brain4all-data/home/.hermes
```

## Development overrides

The local script accepts these optional variables:

| Variable | Default |
| --- | --- |
| `BRAIN4ALL_DEV_FRONTEND_HOST` | `127.0.0.1` |
| `BRAIN4ALL_DEV_FRONTEND_PORT` | `5173` |
| `BRAIN4ALL_DEV_BACKEND_HOST` | `127.0.0.1` |
| `BRAIN4ALL_DEV_BACKEND_PORT` | `8642` |
| `BRAIN4ALL_DEV_ROUTER_HOST` | `127.0.0.1` |
| `BRAIN4ALL_DEV_ROUTER_PORT` | `20128` |
| `BRAIN4ALL_DEV_API_BASE_URL` | local backend host and port |
| `BRAIN4ALL_DEV_SKIP_ROUTER` | `0` |

Example with fixed alternative ports:

```bash
BRAIN4ALL_DEV_FRONTEND_PORT=5180 \
BRAIN4ALL_DEV_BACKEND_PORT=8650 \
BRAIN4ALL_DEV_ROUTER_PORT=20130 \
make dev
```

`BRAIN4ALL_DEV_API_BASE_URL` is normally derived from the selected backend host
and port. Set it only when the browser must reach the backend through a
different address.

For API-only work without starting 9router:

```bash
BRAIN4ALL_DEV_SKIP_ROUTER=1 make dev
```

Provider and model operations will be unavailable until a router is running.

## Port conflict troubleshooting

Inspect the known development ports before starting another stack:

```bash
ss -ltnp | rg ':(5173|5174|5175|8642|20128)\b'
ps -eo pid,ppid,args | rg 'make dev|scripts/dev.sh|vite --host|9router --host|python server.py'
docker ps --format '{{.Names}} {{.Ports}}'
```

If another `make dev` terminal is still running, stop it with `Ctrl+C`. Only
terminate a process after verifying that it belongs to this Brain4All checkout.
Do not stop services merely because Vite reports that `5173` is occupied; Vite
can safely select the next port.

FastAPI and 9router use fixed ports. If `8642` or `20128` belongs to another
required service, choose alternative ports with the variables above.

## Docker deployment

For the containerized self-hosted application:

```bash
docker compose up -d --build
```

Or use:

```bash
make run
```

Open `http://localhost:5152`. This mode uses the Compose networking and
Traefik configuration rather than the Vite development URLs.

## Native VM services

After installing the repository at `/opt/brain4all`, install and start the
systemd services with:

```bash
cd /opt/brain4all
sudo ./scripts/install-systemd-services.sh --start
```

The `brain4all.target` unit owns the API/Hermes service and local 9router. In a
managed VM, expose port `8642` only to the authenticated workspace gateway and
keep port `20128` private to the VM.

## Validation

Run focused checks while developing and the full suite before release:

```bash
make check
make smoke-api
```

Useful focused commands include:

```bash
.tools/python/bin/python -m unittest brain4all.tests.test_cron_delivery
npm test -- --run src/api/crons.test.ts src/components/CronView.test.tsx
npm run build
```

