# Local and cloud deployment

Open Lumora starts in `local` mode unless `START_MODE=cloud` is selected. The
browser reaches only Traefik. Studio, the enterprise gateway, Hermes, and
9router have no host port mappings.

```text
Browser
  -> Traefik
    -> Open Lumora Studio (UI + public API)
      -> Enterprise gateway (limits + private proxy)
        -> Hermes runtime API and 9router
```

Local mode starts the gateway and runtime Compose profiles. The gateway stores
only its installation identity, Free plan, and trust metadata in SQLite. It
does not require a login, PostgreSQL, or internet access after the images are
available. Studio reads its limits from the gateway and falls back to the same
compiled Free limits if the gateway is temporarily unavailable. Agent/profile
state remains in the shared Open Lumora volume.

Cloud mode sets `COMPOSE_PROFILES=cloud`, `START_MODE=cloud`, and
`CONTROL_GATEWAY_URL=https://...`. The local gateway and runtime profiles are
then omitted. The gateway URL is also the private runtime and 9router route
prefix, so users do not configure separate internal endpoints.

## Build and transfer images

In `open-lumora`:

```bash
make build
```

This builds the native binary and `open-lumora-studio:local` image. In
`open-lumora-enterprise`:

```bash
make build
```

This builds the enterprise gateway and extended Hermes runtime images. The
enterprise repository also provides:

```bash
python build_docker.py --path open-lumora-hermes-runtime:local
python build_vm.py
```

After all OCI images exist in the local daemon, run `make bundle` in the public
repository. It writes checksummed gzip parts below 50 MB under `bin/images/`.
Copy the complete directory and run `make load-bundle` on the target machine.
Never create or commit an unsplit Docker image tarball.

## Start

Copy `.env.example` to `.env`, keep `COMPOSE_PROFILES=local`, select a domain,
and run:

```bash
docker compose up -d
```

The default is `http://localhost`. Change `OPEN_LUMORA_DOMAIN` and configure
Traefik TLS separately when using a real domain.
