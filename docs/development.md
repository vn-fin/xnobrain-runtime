# Development and verification

The UI lives in the sibling `xnobrain-ui` repository. This repository contains
the Python application in the `xnobrain/` package and the agent/provider
installation tooling.

On Linux or macOS, install the local toolchain with `make install-local`. It
creates `.tools/python` for the project, installs Hermes and the Python
requirements, and provisions Node.js/npm, 9router, and the agent CLIs. The
macOS target skips the optional office-tool and browser-engine downloads to
keep setup fast. Copy the workspace root `../.env.example` to `../.env` when
local overrides are needed.

```bash
make -C ../xnobrain-ui dev # Vite UI on 5173
make dev                  # runtime API and provider runtime
make backend              # API only, using the selected environment
make check                # Python tests and compile checks
```

The UI's Vite proxy targets the canonical `/xnobrain/api/runtime/v1` namespace on port 8642. Swagger is
at `http://127.0.0.1:8642/xnobrain/api/runtime/swagger_docs`.

The runtime development script supervises the API and provider runtime and
stops both on Ctrl-C. Override `XNOBRAIN_DEV_API_PORT` or
`XNOBRAIN_DEV_ROUTER_PORT` when the default ports are occupied.

The Python tests use isolated temporary profile roots. For a real chat smoke
test, use an existing Hermes profile without changing its config and send a
short prompt through the conversation SSE endpoint. Never print credentials or
copy a user's `.env` into test output.

Container verification:

```bash
make image
docker compose up -d
docker compose ps
make smoke-api
```

After a profile mutation, verify its files remain under
`DATA_DIR/profiles/<id>`, mutable content was atomically replaced, and an
immutable snapshot exists where required.
