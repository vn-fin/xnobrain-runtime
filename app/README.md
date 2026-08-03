# Brain4All App Workspace

All native installer and Full Managed App code lives here. Plan 013 defines
the staged implementation. The Docker edition installs the existing Web app
and displays that loopback site in a sandboxed Tauri webview, with an explicit
option to open it in the default browser. The later Full Managed edition will
reuse the same application shell while managing the runtime directly.

Docker Web publishes exactly one loopback host port through Traefik. The
installer offers the release default (initially 5152) or a validated custom
port; no backend, frontend, router, or telemetry container publishes a host
port directly.

The development runtime manifest selects the authenticated Web build with
required `xno-firebase` login and records its test authentication base URL.
Authentication remains a compile-time frontend contract, so the manifest pins
the exact matching image ID instead of trying to inject unsupported runtime
JavaScript configuration. The Firebase browser key is public build metadata
inside that image and is never copied into installer state or Compose files.

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
