# XNOBrain App Guide

This directory is the exclusive home of the XNOBrain native installer and
Full Managed App. Read `../AGENTS.md`, this file, Plan 013, and the repository
skill `.agents/skills/tauri-app-development/SKILL.md` before app work.

## Hard boundary

- Put every app-specific TypeScript, React, Rust, test, manifest, installer,
  packaging, helper, and document under `app/`.
- Do not change `../src/`, `../xnobrain/`, `../server.py`, the Hermes runtime,
  9router integration, Docker images, or existing Web UI behavior to make an
  app feature work.
- Consume the existing product only through its released HTTP/SSE contracts or
  an unmodified production Web build. Do not import files from `../src/` and do
  not reach into backend Python internals.
- If the public contract is insufficient, stop and document the missing
  contract in Plan 013. A separate explicitly authorized core-contract task is
  required before changing the core repository areas.
- The Docker edition still runs the unmodified Web product. After installation,
  the app may show its loopback URL in a sandboxed embedded browser surface and
  must also offer the system browser. Never copy, fork, or import the Web UI.
- In Docker Web, Traefik is the only service allowed to publish a host port.
  Keep its container entrypoint fixed and bind the selected external port to
  loopback. Frontend, FastAPI/Hermes, 9router, and observability services stay
  Docker-internal.
- The Full Managed edition is the app version. App-only installer and
  management UI belongs under `app/src/`.

## Architecture

- Keep Tauri commands thin. Put lifecycle and installer rules in Rust services,
  platform operations behind driver traits, and serialization at the command
  boundary.
- Accept typed inputs only. Never accept arbitrary shell, executable, URL,
  Compose YAML, path, environment, or privileged command input from a webview.
- Use Tauri capabilities with least privilege and separate installer and
  managed-app capability files.
- Persist state atomically, redact diagnostics, bind product services to
  loopback, verify signed manifests/digests, and preserve user data by default.
- Treat the Web port as a validated installer setting: offer the signed
  manifest's default (initially 5152) or a custom unprivileged port, preflight
  it, persist it, and use it consistently for health and browser launch.
- Keep `src-tauri/src/main.rs` minimal; wire the application in `lib.rs` and
  feature modules.

## Build and validation

- Development: `make dev-app`
- Windows installer, on Windows: `make win-app`
- macOS app/DMG, on macOS: `make mac-app`
- Linux RPM, on a supported RPM Linux host: `make rpm-app`
- App checks: `make -C app check`

Release builds are native-host builds. Do not claim that Windows/macOS signed
installers were built or validated from Linux.
