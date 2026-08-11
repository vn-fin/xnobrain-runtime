---
name: tauri-app-development
description: Build, review, test, package, or document the Brain4All Tauri 2 installer and Full Managed App under app/. Use for Tauri/Rust commands, app-only React UI, Docker Web installation, managed platform drivers, native installers, signing, updates, capabilities, IPC, or make dev-app/win-app/mac-app/rpm-app work.
---

# Develop the Brain4All Tauri app

Keep every implementation change under `app/`. Read `app/AGENTS.md` and
`plans/013_desktop_app/README.md` before acting.

## Preserve the product boundary

- Treat Docker as the Web edition. Install/start the unchanged Web stack, then
  display its loopback URL in a sandboxed Tauri webview by default. Also offer
  an explicit system-browser action and a usable fallback when embedding fails.
- Keep the embedded Web surface visually primary. Put Docker lifecycle,
  bounded/redacted logs, system health, browser fallback, and future account
  actions behind small native overlay controls; do not fork or import Web UI.
- Publish one Docker Web host port through Traefik only. Keep every product and
  runtime service internal. Offer the manifest default or a validated custom
  loopback port and persist the selection.
- Treat Full Managed as the app edition. Put its installer and management UI in
  `app/src/` and its Rust lifecycle implementation in `app/src-tauri/`.
- Do not modify `src/`, `xnobrain/`, `server.py`, Hermes, 9router, or existing
  API behavior for an app task. Consume released HTTP/SSE contracts only.
- If a contract is missing, document the gap and stop that portion. Require a
  separately authorized core-contract change.

Read [references/boundaries-and-structure.md](references/boundaries-and-structure.md)
before adding or moving files.

## Workflow

1. Inspect the nearest app module, tests, `app/AGENTS.md`, and the applicable
   Plan 013 stage.
2. Check the current Tauri 2 official documentation before using a
   version-sensitive API or configuration key. Use
   [references/official-api-links.md](references/official-api-links.md) to
   choose the authoritative page.
3. Model lifecycle rules in Rust services and driver traits. Keep Tauri
   commands as thin typed serialization boundaries.
4. Expose one typed TypeScript adapter per command/channel. Components never
   call `invoke` directly and never construct shell/process input.
5. Add least-privilege capability entries only for the window/edition that
   needs them. Never use broad shell or filesystem permissions.
6. Add unit tests for domain/service behavior, IPC contract tests, frontend
   tests, and a platform-focused smoke test proportional to the change.
7. For embedded-window changes, verify the real Tauri window, Web surface,
   fullscreen restore, and lifecycle actions; browser-only simulation is not
   native evidence.
8. Run `make -C app check`, then the relevant native-host build target. Do not
   claim Windows/macOS packaging from Linux.

Read [references/coding-style.md](references/coding-style.md) for Rust,
TypeScript, IPC, error, state, security, and test conventions. Read
[references/packaging-and-release.md](references/packaging-and-release.md) for
build artifacts, signing, updater, sidecar, and platform rules.

## Stable commands

```text
make dev-app    local Tauri development
make win-app    NSIS build on Windows
make mac-app    app and DMG build on macOS
make rpm-app    RPM build on supported RPM Linux
make -C app check
```

End users receive a signed native artifact and open it normally. They do not
run these contributor commands or install the Rust/Node toolchains.

## Completion gate

- Keep the diff within `app/` except an explicitly requested plan, skill, or
  root Makefile entrypoint change.
- Preserve existing Web/backend tests and behavior.
- Reject arbitrary commands, paths, URLs, environment, Compose content, and
  privileged operations at the webview boundary.
- Verify manifests, update signatures, and artifact digests; redact secrets and
  user content; preserve data by default.
- Record the exact test/build commands and observed native-host artifacts.
