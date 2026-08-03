# 013 — Implementation Plan

Each stage has its own acceptance gate. Do not begin full runtime-management
features while a P0 installer gate remains incomplete.

## Stage 0 — P0: edition contract and Web-installer proof

1. Create the Tauri project exclusively under `app/` following the structure in
   [architecture.md](architecture.md). Do not change core backend or Web UI
   source.
2. Wire the root Makefile entrypoints to `app/Makefile`:
   `make dev-app`, `make win-app`, `make mac-app`, and `make rpm-app`.
   Native release targets fail clearly on the wrong build host.
3. Record supported OS/architecture targets and measured runtime resource
   requirements in a versioned desktop support document.
4. Define separate identifiers, manifests, data locations, and UI copy for
   Docker Web and Full Managed App.
5. Add a minimal Tauri workspace for the Web installer/maintenance wizard. It
   must not embed or present the Brain4All product UI.
6. Prove that successful install/start launches the default browser at the
   loopback Web URL on Windows, macOS, and Linux.
7. Define and validate the signed runtime-manifest schema.
8. Define typed installer command/event contracts and the runtime-driver trait.
9. Produce installer wireframes for every state, including Full Managed App
   planned, reboot, retry, repair, and destructive data deletion confirmation.
10. Complete the third-party redistribution and Docker Desktop licensing gate.

Gate: the Web installer completes its wizard and browser handoff on all target
systems without implying that it is the Full Managed App.

## Stage 1 — P0: Windows Docker Web installer MVP

1. Implement atomic install-state persistence and single-instance locking.
2. Implement the required edition-selection screen and Docker path selection.
3. Implement Windows preflight:
   - supported Windows build and CPU architecture;
   - WebView2;
   - WSL2/virtualization readiness when required by Docker Desktop;
   - Docker client/daemon/Compose compatibility;
   - disk, memory, network, permissions, and port availability.
4. Implement “Use existing Docker” without altering Docker configuration.
5. Implement “Install Docker” as an explicit-consent flow using official
   sources, elevation only when required, and persisted reboot/relaunch resume.
   Do not accept Docker terms on the user's behalf.
6. Implement signed manifest fetch/cache/verification.
7. Pull images by digest with structured progress, cancellation, retry, and
   cleanup limited to incomplete Brain4All-owned artifacts.
8. Generate per-install secrets, Compose configuration, labels, loopback
   ports, and the persistent data volume.
9. Start the stack, poll health with bounded backoff, and show Ready only after
   the API and required runtime components are healthy.
10. Open the healthy loopback URL in the user's default browser. Later runs of
    the utility open/repair the Web version; they never host the product UI.
11. Add Continue/Retry/Repair/Uninstall-runtime flows. Preserve data by
    default; deletion is a separate confirmed action.
12. Export a redacted diagnostics ZIP containing versions, state transitions,
    check results, and bounded service logs.
13. Produce and sign the NSIS installer. Evaluate MSI as an enterprise
    deployment artifact after the consumer flow is stable.

Gate: clean-machine, existing-Docker, reboot-resume, repair, and failure
matrix passes on supported Windows versions.

## Stage 2 — P0: macOS and Linux Docker Web installers

### macOS

1. Implement Docker Desktop detection and official install/open flow.
2. Add Intel/Apple Silicon support, disk/memory checks, and loopback health.
3. Sign and notarize the app and DMG, including all bundled helpers.
4. Validate permissions, Gatekeeper behavior, update signatures, and uninstall.

### Linux

1. Select an initial supported distribution matrix; do not claim generic
   Linux support without testing.
2. Detect compatible Docker Engine/Compose and daemon permissions.
3. Provide reviewed, distro-specific prerequisite installation through the
   official package sources. Show elevation and package changes before action.
4. Build signed DEB and RPM packages; add AppImage only if its update and
   system-integration behavior meets the same gates.

Gate: the same manifest and driver contract completes clean install, existing
Docker, repair, and uninstall on every supported target.

## Stage 3 — P1: Docker Web installer production hardening

1. Signed desktop auto-update with compatibility checks and staged rollout.
2. Runtime update by digest with health-gated activation and rollback.
3. Download resume, mirror policy, proxy support, and bounded retry rules.
4. Accessibility and keyboard-only audit of the complete wizard.
5. Localization-ready message codes; keep logs and state language-neutral.
6. Crash recovery, telemetry opt-in, support bundle privacy review, and release
   observability that contains no user content or secrets.
7. SBOM, vulnerability scan, provenance, third-party notices, and reproducible
   release metadata for shell, helpers, and images.

## Stage 4 — P1: Full Managed App foundation and Linux release

1. Add the Full Managed App Tauri shell around the existing React/Vite product
   UI and an integrated management navigation area.
2. Prove streaming chat, navigation, Markdown, files, clipboard,
   notifications, and accessibility in the Linux webview.
3. Split the runtime into versioned core and optional tool packages.
4. Produce DEB/RPM packages with declared system dependencies and a systemd
   user service; avoid global mutable application state.
5. Implement the Linux managed driver using the common state/progress contract.
6. Include start/stop/restart, health, redacted logs, update/rollback, repair,
   resource information, backup/restore, and data-location management.
7. Enable Full Managed App only on validated distributions.

## Stage 5 — P1: Full Managed App for Windows through WSL2

1. Produce a signed, versioned Brain4All WSL2 root filesystem.
2. Add preflight and explicit elevation/reboot handling for WSL2 enablement.
3. Import with a stable distro name and versioned location; preserve user data
   separately from the runtime root filesystem.
4. Start/stop/health-check through fixed `wsl.exe` operations, not arbitrary
   shell commands from the webview.
5. Implement update, rollback, repair, unregister-runtime, and separately
   confirmed data removal.
6. Validate the embedded product UI on WebView2 and expose the same management
   contract as Linux.
7. Enable Windows Full Managed App only after the clean-machine matrix passes.

## Stage 6 — P2: Full Managed App for macOS native core

1. Define the native-core capability boundary and identify Docker-only tools.
2. Bundle/sign/notarize versioned Python, Node, 9router, and supported helpers.
3. Implement the macOS managed driver and launchd lifecycle where appropriate.
4. Report unsupported optional capabilities honestly in the installer and UI.
5. Validate the product UI on WKWebView and sign/notarize all nested binaries.
6. Enable Full Managed App only on supported macOS/architecture combinations.

## Stage 7 — P2: advanced Full Managed App operations

Extend the App version rather than adding these controls to Docker Web:

- runtime overview and health history;
- start, stop, restart, repair;
- searchable live logs with redaction;
- resource and port configuration with validation;
- runtime update channel and rollback controls;
- backup, restore, and data-location management;
- disk cleanup restricted to unreferenced Brain4All artifacts;
- migration between supported runtime drivers only after a tested data contract
  exists.
