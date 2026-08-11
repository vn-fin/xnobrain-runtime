# Start Here

## Layout

- `server.py`: root FastAPI entrypoint; run with `python server.py`.
- `requirements.txt`: backend/runtime dependencies.
- `xnobrain/routes/setup.py`: the only Brain4All route assembly point.
- `xnobrain/handlers`: HTTP/SSE and Enterprise proxy translation.
- `xnobrain/models`: Pydantic request/response contracts used by Swagger.
- `xnobrain/services`: application rules and orchestration.
- `xnobrain/repositories`: atomic profile/team/notification persistence.
- `xnobrain/integrations`: original Hermes CLI and 9router adapters.
- `xnobrain/telemetry.py`: metadata-only OpenTelemetry setup.
- `src`: Vite, React, and TypeScript UI.
- `runtime`: the combined FastAPI/Hermes/9router image.
- `docs/contracts`: versioned cross-repository protocols.

## Design rules

- Keep Hermes CLI's FastAPI application as the host application. Never start a
  server per profile or duplicate native Hermes APIs.
- Route handlers call services directly; local layers do not call each other
  through HTTP.
- Resolve every user path beneath its profile root and reject traversal and
  symlink escapes.
- Use temp-file-plus-fsync-plus-rename for mutable persistent files.
- Do not log secrets, headers, prompts, request/response bodies, or tool output.
- Local features must continue working when 9router, telemetry, or the optional
  Enterprise API is unavailable.
- Follow `.agents/rules/04-backend-service-groups.md`: one owning service group
  per file, consistently named across backend layers.
