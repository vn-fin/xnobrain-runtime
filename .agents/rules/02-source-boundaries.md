# Source and Product Boundaries

- Author backend/runtime changes in `xnobrain/`, `extensions/`, `runtime/`, or root runtime configuration as appropriate.
- Author web UI changes only in `src/` and root Vite/test configuration.
- Never hand-edit generated files under `dist/` or `dist/assets/`; regenerate only when a task explicitly requests distributable build output.
- Do not modify `app/`. It is a separate application surface and is out of scope unless a user explicitly includes it.
- Keep internal provider/runtime implementation names out of product UI. Show provider brands, agent purpose, status, and user-facing recovery actions instead.
- Treat `/xnobrain/api/runtime/<version>` as the public runtime API namespace and keep backend routes, frontend clients, proxies, tests, telemetry, and docs synchronized.
