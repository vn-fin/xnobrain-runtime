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

Set `ENTERPRISE_API_URL` to add authenticated Enterprise features. The
optional `authenticated` Compose profile starts an OTel collector that exports
to that URL. The Enterprise service and its databases are not part of this OSS
Compose project.

Builds produce only:

- `open-lumora-frontend:<tag>`
- `open-lumora-hermes-runtime:<tag>` (FastAPI, Hermes, and 9router)

`make build` also creates the checksummed split OCI bundle under `bin/images`.
Incus and managed-cloud runtime packaging belong in `open-lumora-enterprise`,
not this repository.
