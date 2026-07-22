# Deployment

The default Compose stack has two application containers plus Traefik:

```text
Traefik -> frontend container
        -> combined FastAPI + Hermes + 9router container
```

```bash
make image
docker compose up -d
```

Open `http://localhost` for the UI and `http://localhost/docs` for Swagger.
Only Traefik publishes a host port. The runtime's named volume holds profiles,
teams, notifications, Hermes state, and 9router credentials.

The runtime defaults to 4 CPUs and 4 GB RAM and requests a 100 GB writable
root disk. Increase `BRAIN4ALL_RUNTIME_CPUS`,
`BRAIN4ALL_RUNTIME_MEMORY`, or `BRAIN4ALL_RUNTIME_DISK_SIZE` in `.env`
when the host has more capacity. CPU and RAM use cgroup limits. Docker enforces
the root-disk request only on storage drivers with per-container quota support;
durable profile data in the named volume follows the Docker host's volume
capacity.

Set `ENTERPRISE_API_URL` to add authenticated Enterprise features. The
optional `authenticated` Compose profile starts an OTel collector that exports
to that URL. The Enterprise service and its databases are not part of this OSS
Compose project. Use `http://localhost:3100` when running Brain4All directly
on the host, or `http://host.docker.internal:3100` from this Compose stack. The
runtime and collector include the Linux host-gateway mapping for that name.

Builds produce only:

- `brain4all-frontend:<tag>`
- `brain4all-hermes-runtime:<tag>` (FastAPI, Hermes, and 9router)

`make build` also creates the checksummed split OCI bundle under `bin/images`.
Incus and managed-cloud runtime packaging belong in `brain4all-enterprise`,
not this repository.
