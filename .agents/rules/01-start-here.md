# Start Here

## Layout

- `server.py`: root FastAPI/Uvicorn entrypoint, listening on port 8642.
- `open_lumora/app.py`: dependency composition around upstream Hermes objects.
- `open_lumora/routes/setup.py`: the one Open Lumora route assembly point.
- `open_lumora/handlers`: HTTP envelopes, streaming, uploads, and proxying.
- `open_lumora/models`: Pydantic request and response contracts for Swagger.
- `open_lumora/services`: orchestration and business rules by capability.
- `open_lumora/repositories`: atomic Open Lumora filesystem persistence.
- `open_lumora/integrations`: Hermes CLI/core/config and 9router adapters.
- `open_lumora/telemetry.py`: redacted OpenTelemetry instrumentation.
- `runtime`: the unified Hermes/FastAPI/9router Docker image and entrypoint.
- `src`: Vite, React, and TypeScript application.
- `Dockerfile.frontend` and `Dockerfile.backend`: Compose application builds.
- `bin/images`: optional checksummed release bundles; normal builds do not write here.
- `docs`: architecture, API, deployment, versioned contracts, and history.

## Python style and boundaries

- Routes declare paths and Pydantic bodies; handlers translate HTTP; services
  own rules; repositories persist; integrations call Hermes and 9router.
- Do not create a generic `data` package or a second local HTTP API between
  these layers.
- Prefer explicit types, narrow adapters, early validation, bounded timeouts,
  and async I/O for HTTP/subprocess streams.
- Extend Hermes' native FastAPI app. Keep Open Lumora routes ahead of Hermes'
  SPA catch-all without changing upstream source.
- Never return or log credentials, runtime tokens, prompts, responses, memory,
  skill content, or tool arguments.

## Safety and ownership

- Resolve every user-controlled path beneath its profile root and reject
  traversal and symlink escapes.
- Use temp-file, fsync, and atomic rename for persistent files.
- Snapshot every memory and skill mutation before returning success.
- Provider credentials stay with local 9router. Only filtered connection/model
  metadata crosses the Open Lumora API.
- Community is database-free and unrestricted. Optional Enterprise calls use
  `ENTERPRISE_API_URL` and propagate auth/trace headers only for Enterprise-owned
  endpoints.
