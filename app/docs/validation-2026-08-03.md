# Installer validation — 2026-08-03

## Passed on Linux x86-64

- Frontend type-check, five unit tests, production build, and four Playwright
  installer journeys.
- Three build-configuration generator tests, including rejection of loopback
  release registries and validation of Windows signing metadata.
- Rust format check, Clippy with warnings denied, and eight unit tests.
- Native 1120 × 620 installer journey from edition selection through healthy
  completion.
- Pull-only Compose runtime with three immutable image references: Traefik,
  Brain UI, and Runtime API. No image was built by the installer.
- Traefik is the sole published service at `127.0.0.1:6253`; Brain UI and
  Runtime API remain Docker-internal.
- Generated file-provider routing works without mounting the Docker socket.
- Runtime API health returned HTTP 200 through Traefik.
- DEB and RPM contain the native binary, desktop entry, and Docker installation
  helper.

Local development artifacts:

```text
src-tauri/target/release/bundle/deb/XNOBrain_0.1.0_amd64.deb
src-tauri/target/release/bundle/rpm/XNOBrain-0.1.0-1.x86_64.rpm
```

These packages are unsigned development artifacts, not release deliverables.
They embed the isolated local-registry validation manifest. Release generation
now rejects loopback registries unless `config-local` is explicitly requested.

## Authentication/API audit limitation

The available local pull-only Web image made one Firebase sign-in request, but
Firebase returned `API key not valid` before account authentication. A synthetic
auth exchange then showed one request each for agents, blends, and teams; the
Runtime API correctly rejected the synthetic bearer token with HTTP 401.

Consequently, authenticated page-by-page API timing and duplicate-call
validation is not claimed. It requires the immutable published XNOBrain Web
image compiled with the real development Firebase public API key. Credentials,
tokens, and browser state were not written to disk.

The separately discovered public XNOQuant Firebase client configuration
confirmed that the supplied username `kim` is not a Firebase email address; an
authorized account email is still required for a real sign-in audit.

## Native release gates still open

- Windows: build/sign the NSIS executable and run install, Docker prerequisite,
  start, upgrade, and uninstall checks on a supported Windows host.
- macOS: build/sign/notarize the DMG and run the same lifecycle checks on both
  required native architectures.
- Linux: sign packages and repeat clean-machine tests for each advertised
  distribution family.

Windows PowerShell setup/preflight scripts passed parser validation. The Linux
preflight passed in the native build container. Those checks do not substitute
for executing the Windows and macOS installers on their target systems.
