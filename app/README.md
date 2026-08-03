# Brain4All App Workspace

All native installer and Full Managed App code lives here. Plan 013 defines
the staged implementation. The Docker edition remains Web-only and opens the
default browser; the later Full Managed edition is the native Tauri app.

Docker Web publishes exactly one loopback host port through Traefik. The
installer offers the release default (initially 5152) or a validated custom
port; no backend, frontend, 9router, or telemetry container publishes a host
port directly.

The Tauri 2 workspace and Docker Web installer are isolated in this directory.
The browser-only development bridge is available for UI testing, while real
install operations are accepted only by the typed Rust command boundary.

Stable repository commands:

```text
make dev-app    development app
make win-app    Windows NSIS installer (run on Windows)
make mac-app    macOS app and DMG (run on macOS)
make rpm-app    Linux RPM (run on supported RPM Linux)
```

App-only validation:

```text
make -C app check       TypeScript, React, production build, Rust format/lint/tests
npm -C app run test:e2e Repeatable Windows-layout visual journeys and recordings
```

See `AGENTS.md` for the hard repository boundary and
`../plans/013_desktop_app/architecture.md` for the planned structure.
