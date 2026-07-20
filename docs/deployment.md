# Local and cloud deployment

Open Lumora starts in `local` mode unless `START_MODE=cloud` is selected. The
public repository owns only the frontend and Studio backend images. The
enterprise repository owns `open-lumora-gateway`, the Hermes/9router runtime,
PostgreSQL, ClickHouse, and enterprise worker processes. The browser reaches
only Traefik; private services have no public ingress.

```text
Browser
  -> Traefik
    -> Open Lumora Studio (UI + public API)
      -> open-lumora-gateway (limits + private proxy)
        -> Hermes runtime API and 9router
```

The two Compose projects meet on the external, internal-only
`open-lumora-control` network. The enterprise deployment must give its gateway the network
alias `open-lumora-gateway`; the enterprise runtime must mount the established
external `open-lumora_open_lumora_data` volume that Studio owns. Studio reads limits from the
gateway and falls back to compiled Free limits while it is unavailable.
Agent/profile state remains in the shared volume.

For a remote cloud gateway, set `OPEN_LUMORA_GATEWAY_URL=https://...` in the
public Compose environment. For host development use
`CONTROL_GATEWAY_URL=http://localhost:3100`. The gateway URL is also the
private runtime and 9router route prefix, so users do not configure separate
internal endpoints.

## Build and transfer images

In `open-lumora`:

```bash
make build
```

This builds and bundles only `open-lumora-backend:local` and
`open-lumora-frontend:local`. In `open-lumora-enterprise`:

```bash
make build
```

This must build `open-lumora-gateway`, the extended Hermes runtime, and its
PostgreSQL/ClickHouse deployment dependencies. The enterprise repository also
provides:

```bash
python build_docker.py --path open-lumora-hermes-runtime:local
python build_vm.py
```

The public `make build` writes its two images as checksummed gzip parts below
50 MB under `bin/images/`. Enterprise runtime, gateway, PostgreSQL, and
ClickHouse transfer artifacts belong to the enterprise repository. Never
create or commit an unsplit Docker image tarball.

## Start

Copy `.env.example` to `.env`, select a domain and gateway endpoint, then run:

```bash
make install
```

The default is `http://localhost`. Change `OPEN_LUMORA_DOMAIN` and configure
Traefik TLS separately when using a real domain.

## Enterprise gateway environment contract

The enterprise deployment owns authentication. Its gateway environment must
expose `AUTH_SERVICE_BASE_URL` as the canonical external-auth endpoint, with
`AUTH_TIMEOUT` controlling request deadlines. Local no-login mode may leave
the base URL empty; authenticated cloud mode must reject startup when it is
missing. Existing gRPC-specific settings are enterprise implementation details
and must not leak into the public frontend/backend image.
