# Development and verification

Requirements are Python 3.12+, a current Hermes Agent installation, Node.js
22+, and npm. Copy `.env.example` to `.env` when local overrides are needed.

```bash
./scripts/dev.sh          # Vite 5173 + FastAPI 8642
python server.py          # API only
make check                # Python tests/compile + frontend tests/build
```

Vite proxies `/api`, `/agent-gateway`, `/conversations`, and `/sandboxes` to
port 8642. Swagger is at `http://127.0.0.1:8642/docs`.

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
