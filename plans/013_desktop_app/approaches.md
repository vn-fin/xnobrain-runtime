# 013 — Approaches and Decisions

## Decision A — Two editions have different launch surfaces

The product has two explicit editions:

- **Web Version (Docker):** Docker hosts the services and web UI. After install
  or start, XNOBrain opens in the user's default system browser. It never
  renders the product inside a desktop webview.
- **Full Managed App:** Tauri renders the existing React UI in a native window
  and manages its platform runtime. This is the application version.

The Docker-first installer may use Tauri for its small wizard/maintenance UI,
but that utility is not presented as the XNOBrain Full Managed App.

## Decision B — Tauri 2 is the Full Managed App shell

Why:

- It preserves the existing UI and API contracts.
- Rust is suitable for process lifecycle, downloads, digest verification,
  state machines, and tightly scoped privileged helpers.
- Native installers and a signed updater exist across the target platforms.
- The shell remains small even though runtime artifacts are large.

Before Full Managed App implementation, Stage 4 must prove streaming chat,
downloads,
dialogs, deep links, clipboard, notifications, responsive layout, and the main
agent/session UI on all three webview engines. If a blocking webview defect
cannot be resolved within the Stage-0 timebox, Electron is the recorded
fallback; Tkinter/PyQt are not fallback UI frameworks.

## Decision C — Product version is always explicit

The installer requires a user choice between Web Version and Full Managed App.
Store the choice only after the user presses Continue. Never infer the edition
from Docker being installed.

For the first release:

- Web Version (Docker): enabled and marked Available.
- Full Managed App: visible but marked Planned; it explains the staged support
  and returns to edition selection.

As managed runtimes ship, capability detection enables Full Managed App only
where its signed platform runtime is available.

## Decision D — Docker Web has two prerequisite paths

After Web Version (Docker) is selected:

1. **Use existing Docker** validates client/daemon compatibility, architecture,
   Compose support, disk, and permissions without changing the installation.
2. **Install Docker** explains requirements and licensing, obtains explicit
   consent, launches the official installer, and resumes after installation or
   reboot.

XNOBrain does not redistribute Docker Desktop inside its installer unless a
later legal and technical decision explicitly permits it. Downloads use
official sources and checksums/signatures where published.

## Decision E — Traefik is the only Docker Web ingress

Only Traefik publishes a host port. Its container entrypoint stays fixed at
5152; the installer maps a selected loopback host port to it. Frontend,
FastAPI/Hermes, 9router, and optional observability services publish no host
ports.

The signed runtime manifest supplies the recommended external port (initially
5152). The user may keep it or select a custom port in the unprivileged range.
The bind address is not customizable in the consumer installer: it remains
loopback. This prevents “custom port” from accidentally becoming LAN exposure.

## Decision F — Separate Web installer, managed app, and runtimes

Four independently versioned artifacts:

- Web installer: small Tauri wizard/maintenance utility that opens a browser.
- Full Managed App: Tauri application and React assets, released later.
- Runtime manifests: schema-versioned, signed metadata identifying compatible
  image digests, architectures, ports, health checks, minimum resources, and
  migrations.
- Runtime artifacts: Docker Web images first; managed platform runtimes later.

The desktop updater does not implicitly replace runtime data or pull a new
runtime. Compatibility is checked before either side updates.

## Decision G — Pull releases; never build on customer machines

The Web installer pulls prebuilt multi-architecture images by immutable
digest. Tags are display metadata only. A failed new release retains the last
healthy manifest and images for rollback.

## Decision H — Web maintenance stays minimal; app management comes later

The Docker Web installer owns only what is necessary to complete installation:

- prerequisite detection;
- download/pull and progress;
- create/start;
- health check;
- retry/repair/uninstall;
- launch XNOBrain.

A later **Full Managed App** owns the integrated product and management
experience: app window, lifecycle, live logs, resource settings,
backup/restore, updates, repair, and diagnostics. These features are not added
to the Docker Web edition as a desktop container dashboard.

## Decision I — Full Managed App runtimes are platform-specific

The Full Managed App hides a platform-specific managed runtime behind one app:

- Linux: DEB/RPM runtime and systemd user services.
- Windows: a signed XNOBrain WSL2 root filesystem imported and managed by the
  app, without Docker Desktop; this is not a strict Win32 port.
- macOS: a signed native-core runtime. Large optional tools may remain Docker-
  only until native equivalents are proven.

Strict full-native Windows is deferred because it would require porting and
validating every POSIX-oriented runtime tool and skill.

## Decision J — Data removal is a separate destructive choice

Uninstalling the desktop shell, uninstalling runtime components, and deleting
XNOBrain user data are three separate operations. Data deletion requires a
specific confirmation showing the resolved data location. Repair and update
never delete user data.
