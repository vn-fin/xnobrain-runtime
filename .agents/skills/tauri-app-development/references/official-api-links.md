# Official Tauri and platform API references

Use current official documentation for version-sensitive code. Prefer the
Tauri v2 pages below; do not copy examples from Tauri v1 or unverified blogs.

## Project and frontend

- Project structure: <https://v2.tauri.app/start/project-structure/>
- Vite frontend configuration: <https://v2.tauri.app/start/frontend/vite/>
- Tauri configuration reference: <https://v2.tauri.app/reference/config/>
- JavaScript API: <https://v2.tauri.app/reference/javascript/api/>
- Rust crate API: <https://docs.rs/tauri/latest/tauri/>

## IPC, process, and state

- Calling Rust/commands/channels/events:
  <https://v2.tauri.app/develop/calling-rust/>
- Calling the frontend from Rust:
  <https://v2.tauri.app/develop/calling-frontend/>
- State management: <https://v2.tauri.app/develop/state-management/>
- Sidecars: <https://v2.tauri.app/develop/sidecar/>
- Shell plugin: <https://v2.tauri.app/plugin/shell/>
- Opener plugin: <https://v2.tauri.app/plugin/opener/>

## Security

- Security overview: <https://v2.tauri.app/security/>
- Capabilities: <https://v2.tauri.app/security/capabilities/>
- Permissions: <https://v2.tauri.app/security/permissions/>
- Command scopes: <https://v2.tauri.app/security/scope/>
- Content Security Policy: <https://v2.tauri.app/security/csp/>

## Tests and distribution

- Tests: <https://v2.tauri.app/develop/tests/>
- Distribution overview: <https://v2.tauri.app/distribute/>
- Windows installer: <https://v2.tauri.app/distribute/windows-installer/>
- Windows signing: <https://v2.tauri.app/distribute/sign/windows/>
- macOS bundle/DMG: <https://v2.tauri.app/distribute/dmg/>
- macOS signing/notarization: <https://v2.tauri.app/distribute/sign/macos/>
- RPM: <https://v2.tauri.app/distribute/rpm/>
- Updater plugin: <https://v2.tauri.app/plugin/updater/>

## Runtime prerequisites

- Docker Desktop Windows:
  <https://docs.docker.com/desktop/setup/install/windows-install/>
- Docker Desktop macOS:
  <https://docs.docker.com/desktop/setup/install/mac-install/>
- Docker Engine install: <https://docs.docker.com/engine/install/>
- Microsoft WSL install: <https://learn.microsoft.com/windows/wsl/install>
- WSL commands/import: <https://learn.microsoft.com/windows/wsl/basic-commands>

Before relying on a page, confirm it documents Tauri 2/current supported
platforms and recheck toolchain prerequisites in the release environment.
