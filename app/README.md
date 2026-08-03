# Brain4All App Workspace

All native installer and Full Managed App code lives here. Plan 013 defines
the staged implementation. The Docker edition remains Web-only and opens the
default browser; the later Full Managed edition is the native Tauri app.

Docker Web publishes exactly one loopback host port through Traefik. The
installer offers the release default (initially 5152) or a validated custom
port; no backend, frontend, 9router, or telemetry container publishes a host
port directly.

The Tauri project has not been scaffolded yet. Plan 013 Stage 0 creates it
inside this directory without modifying the existing backend or Web UI.

Stable repository commands:

```text
make dev-app    development app
make win-app    Windows NSIS installer (run on Windows)
make mac-app    macOS app and DMG (run on macOS)
make rpm-app    Linux RPM (run on supported RPM Linux)
```

See `AGENTS.md` for the hard repository boundary and
`../plans/013_desktop_app/architecture.md` for the planned structure.
