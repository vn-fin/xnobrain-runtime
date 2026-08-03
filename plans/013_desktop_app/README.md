# 013 — Desktop App and Installer

Priority: **P0 for the Docker Web installer**, followed by the independently
shippable **Full Managed App**. Docker is only the Web version: it runs the
stack and opens Brain4All in the user's normal browser. The Full Managed App
is the native application version and is implemented later with Tauri 2.

Read the sibling documents in order:

- [findings.md](findings.md) — verified repository constraints and desktop
  packaging facts.
- [approaches.md](approaches.md) — framework, install-mode, and rollout
  decisions.
- [architecture.md](architecture.md) — components, state machine, security,
  and desktop/runtime boundaries.
- [implementation.md](implementation.md) — ordered stages and file-level work.
- [validation.md](validation.md) — release gates and platform test matrix.

Also read [`AGENTS.md`](../../AGENTS.md) and
[`plans/LOCAL_FEATURES_CHECKLIST.md`](../LOCAL_FEATURES_CHECKLIST.md) before
implementation. All application code belongs under [`app/`](../../app/); its
local [`AGENTS.md`](../../app/AGENTS.md) is mandatory for app work.

## Goal

Deliver a normal desktop installation experience while preserving one
Brain4All product and one runtime contract:

1. The user downloads a signed installer and opens it normally.
2. The first screen requires a **product-version selection**: **Web Version
   (Docker)** or **Full Managed App**.
3. The first release implements the Web version end to end. Full Managed App
   remains visibly planned but cannot report a false successful installation.
4. Web Version detects prerequisites, helps install or connect Docker, pulls
   pinned Brain4All images, creates persistent data, starts the stack, waits
   for health, and opens the URL in the **system browser**.
5. Docker Web publishes one loopback port through Traefik only. Installation
   offers the release default port or a validated custom port.
6. A failed or interrupted install can resume or repair without losing data.
7. Full runtime management is added only after installation is reliable.

## Product rule: version selection is mandatory

The wizard never silently chooses a runtime. Its first actionable screen is:

```text
Choose your Brain4All version

  Web Version (Docker)           Browser based, available first
  Full Managed App               Native managed app, planned

                                      Continue
```

During the Docker-first release, selecting Full Managed App opens an
explanation and lets the user go back; it does not install a partial app. Once
the managed application is released for the current OS, the same selection
downloads/starts its dedicated installation flow.

After selecting Docker, the user chooses one of:

- **Use existing Docker** — validate the daemon and continue.
- **Install Docker** — show requirements and license implications, launch the
  official platform installer with explicit consent, then resume detection.

## Priority and stage overview

| Stage | Priority | Deliverable | Ships independently |
|---|---|---|---|
| 0 | P0 | Edition contract, Web installer proof, runtime manifest | No |
| 1 | P0 | Docker Web installer MVP, Windows first | Yes |
| 2 | P0 | Docker Web installer for macOS and Linux | Yes |
| 3 | P1 | Web installer updates, rollback, diagnostics, accessibility | Yes |
| 4 | P1 | Full Managed App foundation and Linux release | Yes |
| 5 | P1 | Full Managed App for Windows through managed WSL2 | Yes |
| 6 | P2 | Full Managed App for macOS with native core | Yes |
| 7 | P2 | Advanced Full Managed App operations | Yes |

Strict Win32-native installation of the complete Hermes/tool runtime is not
in this plan. The Windows Full Managed App privately manages a Brain4All WSL2
distribution because the current runtime and toolchain are Linux-oriented.

## Docker-installer MVP scope

Included:

- A small signed installer/maintenance UI; it is not the Full Managed App.
- Required Web Version/Full Managed App selection.
- Existing-Docker and install-Docker choices.
- OS/architecture, disk, memory, daemon, port, and virtualization checks.
- Default/custom Web port selection; only Traefik publishes it to loopback.
- Resumable prerequisite/install state, including reboot recovery on Windows.
- Pull by immutable image digest with visible progress and cancellation.
- Unique per-install secrets, loopback-only ports, persistent named data.
- Start, health wait, system-browser handoff, repair, and uninstall choices.
- Useful error messages and an exportable redacted diagnostic bundle.

Not included in the MVP:

- A container dashboard, terminal, arbitrary Compose editor, or Docker GUI.
- CPU/RAM tuning, live log explorer, backups, runtime channel switching, or
  scheduled updates.
- Full Managed App installation or embedded desktop product UI.
- Building Docker images on the customer's computer.
- Silent Docker Desktop installation or automatic license acceptance.

## Repository boundary

Plan 013 is implemented entirely in `app/`. App development must not modify
the core backend (`brain4all/`, `server.py`, Hermes/9router runtime), the Web UI
(`src/`), or their existing behavior. The app consumes released HTTP/SSE
contracts and may display an unmodified production Web build. If a missing
public contract is discovered, record it and schedule an explicitly authorized
core-contract change separately; do not make an incidental core edit.

The stable build entrypoints are:

```text
make dev-app    # local Tauri development
make win-app    # Windows NSIS build, on Windows
make mac-app    # signed app/DMG build, on macOS
make rpm-app    # RPM build, on supported RPM Linux
```

Release packaging is performed on its native OS so platform signing and
bundling are real. Future users receive the resulting signed installer/package
and open it normally; they do not install Node, Rust, or run Make.

## Definition of done for the first release

- A clean supported Windows machine can run the signed installer, explicitly
  select Docker, install/connect prerequisites, survive a required reboot,
  pull the pinned release, reach the Brain4All health endpoint, and open the
  Web version in the system browser without using a terminal.
- A machine with Docker already running follows a shorter path and does not
  reinstall or modify Docker.
- Cancel, network loss, daemon failure, port conflict, insufficient disk, and
  image-health failure all lead to retryable states with actionable errors.
- Re-running the Web installer offers **Continue**, **Repair**, or
  **Uninstall Web runtime**
  as appropriate and preserves user data unless deletion is separately and
  explicitly confirmed.
- The same Web runtime manifest drives Windows, macOS, and Linux; the installer
  never builds images locally and never uses floating tags.
- UI commands cannot execute arbitrary shell input, ports bind only to
  `127.0.0.1`, secrets are not logged, and update/image signatures are
  verified.
- Container inspection shows one published port owned by Traefik and zero
  published ports on frontend, runtime, 9router, or observability services.
- Automated tests and the manual platform matrix in
  [validation.md](validation.md) pass with recorded evidence.
