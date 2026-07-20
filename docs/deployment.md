# Local and cloud deployment

## Self-hosted

The public Compose project owns the complete installation:

```text
Traefik -> frontend
        -> Studio API -> OSS Hermes/9router runtime
                      -> pulled Enterprise API (authenticated extensions)
```

It starts Traefik, frontend, Studio API, OSS runtime, PostgreSQL, and the pulled
Enterprise API image. Only Traefik exposes a host port. The optional
`authenticated` profile starts the local OTel Collector.

Signed-out mode is the default and needs neither Internet nor authentication.
All Hermes features are unrestricted. Signed-in mode preserves that access and
adds plan-scoped Enterprise features such as usage and trace dashboards.

```bash
make build
make install
```

Open <http://localhost>.

## Cloud

Cloud always requires login. It uses the same public runtime contract but
launches the OSS Incus image. The Enterprise API owns tenant plans, managed
resource limits, PostgreSQL metadata, ClickHouse telemetry, and future RBAC or
workspace sharing.

## Build ownership

In `open-lumora`:

```bash
make build
python build_docker.py --path open-lumora-hermes-runtime:local
python build_vm.py
```

`make build` produces backend, frontend, and runtime images, then stores their
offline bundle as checksummed parts smaller than 50 MB under `bin/images`.

In `open-lumora-enterprise`:

```bash
make build
```

This builds only the Enterprise API image. It never builds or contains Hermes
extensions or runtime packaging.

## Authentication and telemetry

Set `AUTH_SERVICE_BASE_URL` only when enabling signed-in mode. The Enterprise
API fails closed for dashboard and ingestion routes without a valid account or
claimed device. New testing tenants resolve the Basic plan (`free` ID).

## Migrating from the previous two-Compose layout

Build or pull the OSS runtime before removing the old
`open-lumora-enterprise-runtime-1` container. On the next coordinated maintenance
window, remove that one obsolete runtime container and run `make install` from
this repository; Compose will create `open-lumora-runtime-1` on the same named
profile volume. Do not run both runtime containers after migration because they
would share the `runtime` DNS alias and profile files.
