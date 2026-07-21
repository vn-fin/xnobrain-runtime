# Deployment

## Self-hosted Community

```text
Traefik -> frontend container
        -> runtime container (FastAPI + Hermes + 9router)
```

Build and start:

```bash
make run
```

Open <http://localhost>; Swagger is <http://localhost/docs>. Only Traefik binds
the host. The runtime API listens on `8642` and 9router on `20128` inside the
runtime container. Persistent state lives in the
`open-lumora_open_lumora_data` volume.

The runtime runs as UID/GID 10001. Its image initializes `/opt/data` with that
ownership so a fresh named volume starts without a root process. The frontend
uses unprivileged nginx.

`ENTERPRISE_API_URL` defaults to empty. Setting it enables only optional
Enterprise interfaces. The `authenticated` Compose profile starts an
OpenTelemetry Collector when an authenticated Enterprise deployment needs it.

## Images and offline bundle

```bash
make frontend-image
make runtime-image
make bundle
```

The public OCI bundle contains the frontend and unified runtime images in
checksummed parts under `bin/images`.

## Enterprise and Incus

The OSS repository owns the Docker runtime image. Per the current deployment
boundary, Incus orchestration/build packaging belongs to the sibling
`open-lumora-enterprise` deployment repository; this OSS tree contains no
`build_vm.py` or Incus installer. Enterprise consumes the released OSS Docker
runtime/core and adds authenticated control-plane behavior without copying the
`open_lumora` package.
