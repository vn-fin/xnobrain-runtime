# XNOBrain Desktop App

This directory is the isolated Tauri 2 installer and future managed desktop
workspace. It does not import or modify the existing Web UI or backend source.

The current Docker/Web edition is distributed as one native installer file.
After launch it can install Docker Engine/Desktop and Docker Compose (with the
operating system's permission and Docker-license prompts), pull three immutable
images (Traefik, Brain UI, and Runtime API), generate a private Compose stack,
and start XNOBrain. Traefik is the
only service with a host mapping and binds only to `127.0.0.1` on the selected
default or custom port. Routing uses a generated read-only file; the Traefik
container is not given the Docker socket.

## Contributor setup

From this directory, `make` installs/checks native Tauri prerequisites, installs
the stable Rust toolchain with Clippy and rustfmt, and runs `npm ci`. The root
delegates `make dev-app`, `make win-app`, `make mac-app`, and `make rpm-app` to
this Makefile.

On a fresh Windows machine without GNU Make, bootstrap once from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File app\scripts\setup-windows.ps1
```

The bootstrap installs Make as well as Node, MSVC, WebView2, and Rust. Open a
new terminal afterward; subsequent setup and build commands use Make normally.

Copy `.env.example` to `.env` or pass variables directly to Make. Release
builds require the Brain UI and Runtime API images as immutable registry digest
references; mutable tags, example registries, and local-only images are
rejected. `make config-local` is an explicit exception for isolated installer
validation and must not feed release artifacts.

```text
make                         contributor setup on Linux, macOS, or Windows
make dev                     local native development with a safe test manifest
make brain-image             Brain UI image with validated public build env
make appimage                one standalone Linux AppImage
make win                     one NSIS .exe, on a Windows host
make mac                     one DMG, on a macOS host
make rpm                     one RPM, on a supported RPM Linux host
make deb                     one DEB, on a supported Debian Linux host
make check                   frontend plus strict Rust checks and tests
```

Native package targets run a host preflight before compiling. Set
`XNOBRAIN_REQUIRE_SIGNING=1` in release automation to require an installed
Windows signing certificate or a macOS signing identity plus notarization
credentials. Local developer artifacts remain unsigned by default.

Example release configuration:

```text
make linux-app \
  XNOBRAIN_APP_NAME=XNOBrain \
  XNOBRAIN_APP_DESCRIPTION="Private XNOBrain Docker Web workspace" \
  XNOBRAIN_API_BASE_URL=https://public.dev.xno.vn \
  XNOBRAIN_AUTH_BASE_URL=https://public.dev.xno.vn \
  XNOBRAIN_BRAIN_CONTROL_BASE_URL=https://public.dev.xno.vn \
  XNOBRAIN_FIREBASE_API_KEY=AIza<complete-public-web-api-key> \
  XNOBRAIN_IMAGE=registry.example/xnobrain@sha256:<64-hex-digest> \
  XNOBRAIN_RUNTIME_API_IMAGE=registry.example/xnobrain-runtime-api@sha256:<64-hex-digest>
```

For the Docker-first single-file Linux release, use `make linux-app` from the
repository root. It writes `binary/XNOBrain-linux-<architecture>.AppImage`.
The user opens that one file to install/connect Docker, start the runtime, and
open the Web version. DEB/RPM remain optional repository-packaging formats and
are not required for this distribution path.

The embedded Web application keeps browser interactions inside the native
shell: OAuth and other safe HTTP(S) `window.open` requests use a related popup
so same-origin callbacks and device authorization continue to work. Native
Tauri file-drop interception is disabled on the main window so files and
folders reach the Web application's existing HTML5 workspace upload handlers.
Downloads, clipboard access, and iframe fullscreen remain explicitly enabled.

No credentials belong in `.env`, generated manifests, Compose, screenshots, or
logs. Authentication and Brain Control are external HTTPS contracts compiled
into the Brain UI image from `AUTH_BASE_URL` and `API_CONTROL_BASE_URL`. Their
expected origins are recorded with `XNOBRAIN_AUTH_BASE_URL` and
`XNOBRAIN_BRAIN_CONTROL_BASE_URL`; neither runs as a local container.
`make config` also writes the matching ignored, mode-0600
`generated/brain-ui-build.env` for the separate Brain UI image build. The
installer manifest stores only the Firebase key's SHA-256 fingerprint.

The published Brain UI image must already be compiled for those same origins,
the authentication provider, and Firebase public API key. Vite embeds those
values at image build time; installer metadata cannot retrofit them. Release
validation must reject an image that cannot complete authentication and Brain
Control calls against the selected environment.

See [platform support](docs/platform-support.md) for the native validation and
release requirements.
