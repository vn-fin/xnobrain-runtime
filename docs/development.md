# Development and verification

The repository root is the Vite project root: application code is in `src/`,
static assets are in `public/`, and the committed production build is emitted
to `dist/`. Node dependencies are installed into the root `node_modules/`.
The Python application remains in the separate `brain4all/` package.

On Linux or macOS, install the local toolchain with `make install-local`. It
creates `.tools/python` for the project, installs Hermes and the Python
requirements, and provisions Node.js/npm, 9router, and the agent CLIs. The
macOS target skips the optional office-tool and browser-engine downloads to
keep setup fast. Copy `.env.example` to `.env` when local overrides are needed.

```bash
npm run dev               # Vite 5173 + reload FastAPI 8642 + 9router 20128
make dev                  # same supervised local stack
make backend              # API only, using the selected environment
make check                # Python tests/compile + frontend tests/build
```

Vite proxies the canonical `/xnobrain/api/runtime/v1` namespace to port 8642. Swagger is
at `http://127.0.0.1:8642/xnobrain/api/runtime/swagger_docs`.

The development script supervises Vite, FastAPI, and 9router and stops all
three on Ctrl-C. The backend watches Python files and reloads automatically;
the frontend uses Vite's normal HMR. Override `XNOBRAIN_DEV_WEB_PORT` or
`XNOBRAIN_DEV_API_PORT` when the default ports are occupied.

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
