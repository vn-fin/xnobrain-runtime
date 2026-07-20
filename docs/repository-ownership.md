# Repository and image ownership

This contract prevents public and enterprise builds from embedding one
another's executables or rebuilding the same infrastructure.

| Repository | Owned build artifacts | Owned deployment state |
| --- | --- | --- |
| `open-lumora` | `open-lumora-frontend`, `open-lumora-backend` | Agent profiles in `open-lumora_open_lumora_data`, public Traefik routes |
| `open-lumora-enterprise` | `open-lumora-gateway`, `open-lumora-hermes-runtime` | PostgreSQL, ClickHouse, gateway keys/state, scheduler and retention workers |

Hermes extensions, launchers, and runtime packaging live exclusively in the
Enterprise repository. The public repository retains only Studio's runtime
client contracts and never invokes a runtime Dockerfile or Enterprise build
context.

## Private deployment contract

Both Compose projects attach to the external Docker network
`open-lumora-control`. The enterprise gateway has the network alias
`open-lumora-gateway` and listens privately on port `3100`. Enterprise Hermes
workers mount the external `open-lumora_open_lumora_data` volume at `/opt/data`, matching
Studio's absolute profile paths.

The only supported Studio control-plane setting in Compose is:

```dotenv
OPEN_LUMORA_GATEWAY_URL=http://open-lumora-gateway:3100
```

Studio derives the private runtime and 9router paths from that base URL. The
browser never receives a gateway, runtime, database, or ClickHouse address.

## Enterprise authentication environment

Authentication belongs exclusively to `open-lumora-gateway`. The Enterprise
configuration and Compose files provide:

```dotenv
AUTH_SERVICE_BASE_URL=https://auth.example.com
AUTH_TIMEOUT=3s
```

`AUTH_SERVICE_BASE_URL` is empty in local no-login mode. Cloud mode must
validate it during startup and use it as the canonical endpoint for token and
session verification. Credentials must not appear in this URL, logs, traces,
or the public frontend/backend environment. Transport-specific options such as
TLS or gRPC remain private enterprise settings.

## Enterprise build contract

The Enterprise `make build`:

1. Build the Go gateway as the `open-lumora-gateway` executable/image.
2. Run `python build_docker.py --path open-lumora-hermes-runtime:<tag>`.
3. Keep `python build_vm.py` as the Incus runtime build.
4. Package PostgreSQL and ClickHouse deployment images and migrations.
5. Start the scheduler listener from the enterprise backend image.

The enterprise Compose stack starts before the public stack. Studio remains
healthy with compiled Free-limit fallback when the gateway is unavailable,
but agent runs, providers, telemetry dashboards, and managed scheduling require
the gateway/runtime stack.
