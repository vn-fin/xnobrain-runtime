# 013 — Findings

These findings define the constraints the desktop plan must preserve. Recheck
versions, operating-system requirements, and licensing before each release.

## 1. XNOBrain is already a web application

- The product UI is React, TypeScript, and Vite under `src/`.
- The application host is one FastAPI/Hermes process on port 8642, with one
  9router process. Neither edition may introduce another application API.
- The repository already has Docker Compose and platform setup scripts. They
  are developer-oriented inputs, not yet a consumer installer contract.
- Docker naturally remains the Web edition and opens this UI in the system
  browser. The later Full Managed App can reuse it inside Tauri; rewriting it
  in Tkinter, PyQt, or PySide would duplicate the product and its tests.

## 2. The difficult part is the runtime, not the window

The combined runtime contains a large Linux-oriented dependency set including
Python/Hermes, Node/9router, browser automation, office/PDF/image tooling,
OCR, Java, fonts, and command-line tools. A desktop framework can bundle a
small helper, but it cannot make this complete dependency graph portable.

Consequences:

- Do not embed the full runtime as one Tauri sidecar.
- Do not freeze the whole product with PyInstaller and call that native.
- Docker remains the fastest route to consistent full capability.
- Direct installations require a separate signed artifact per OS and CPU,
  plus a capability manifest that admits platform differences.

## 3. Existing install scripts are not a release installer

- `scripts/setup-windows.ps1` helps install WSL/Docker Desktop/make, but Docker
  first-run, elevation, reboot, and license acceptance still need a product
  flow.
- `make install-local` invokes the Linux installer. Native Windows and macOS
  full-runtime installation are not currently complete.
- The Compose path builds images locally. A desktop release must pull signed,
  prebuilt, multi-architecture images pinned by digest.
- The development Compose currently uses Traefik's internal entrypoint 5152
  and `XNOBRAIN_HTTP_PORT` for its host mapping. The app-owned Docker Web
  template must preserve Traefik as the sole ingress while binding the chosen
  host port to loopback; it must not edit the core Compose file.

## 4. Tauri is viable for the Full Managed App and installer utility

Tauri 2 can implement the small Web installer UI and later reuse the Vite
frontend for the Full Managed App. It can produce Windows MSI/NSIS, macOS
bundles and DMG, and Linux DEB/RPM/AppImage packages. It supports signed
updates and target-specific external binaries.

Limits relevant here:

- It uses the operating-system webview: WebView2 on Windows, WKWebView on
  macOS, and WebKitGTK on Linux. XNOBrain needs cross-webview UI testing.
- Each sidecar is built for a target triple. A sidecar is appropriate for the
  small runtime manager/helper, not the full Linux runtime.
- Installer production still requires Windows and macOS code signing,
  notarization on macOS, Linux package work, and per-platform CI runners.

Primary references:

- <https://v2.tauri.app/develop/sidecar/>
- <https://v2.tauri.app/distribute/>
- <https://v2.tauri.app/distribute/windows-installer/>
- <https://v2.tauri.app/plugin/updater/>

## 5. Docker cannot always be installed silently

Windows may require enabling WSL2/virtualization, administrator elevation, and
a reboot. Docker Desktop also presents license terms and may require user
interaction. macOS installation has signing/security and first-run steps.
Linux differs by distribution and package manager.

The Web installer may detect, explain, download from an official source,
verify, launch, and resume. It must not silently accept third-party terms,
hide elevation, or promise one-click completion where the operating system
requires interaction.

Primary references:

- <https://docs.docker.com/desktop/setup/install/windows-install/>
- <https://docs.docker.com/desktop/setup/install/mac-install/>
- <https://learn.microsoft.com/windows/wsl/install>

## 6. Distribution has a license gate

Before publishing installers or container images:

- Choose and add the XNOBrain project license if still unresolved.
- Inventory redistributed binaries, fonts, models, browser components, office
  components, and Python/Node packages.
- Record notices/source-offer obligations as applicable.
- Review Docker Desktop licensing for the intended customers.

This is a release blocker, not installer legal advice embedded in code.

## 7. Current facts that Phase 0 must measure

Do not design from assumed sizes or timings. Record for each release target:

- compressed image download and unpacked disk requirement;
- cold pull and cold start time;
- minimum practical memory and CPU;
- supported Windows/macOS/Linux versions and architectures;
- WebView availability and frontend rendering differences;
- port use and collision behavior;
- clean uninstall behavior and data volume location;
- which runtime tools work in Docker Desktop on each OS.
