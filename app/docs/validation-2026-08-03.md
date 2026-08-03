# Installer validation — 2026-08-03

## Passed on Linux x86-64

- Frontend type-check, five unit tests, production build, and four Playwright
  installer journeys.
- Four build-configuration generator tests, including rejection of loopback
  release registries, validation of Windows signing metadata, and rejection of
  incomplete Firebase Web API keys.
- Rust format check, Clippy with warnings denied, and eight unit tests.
- Native 1120 × 620 installer journey from edition selection through healthy
  completion.
- Pull-only Compose runtime with three immutable image references: Traefik,
  Brain UI, and Runtime API. No image was built by the installer.
- Traefik is the sole published service at `127.0.0.1:6255`; Brain UI and
  Runtime API remain Docker-internal.
- Generated file-provider routing works without mounting the Docker socket.
- Runtime API health returned HTTP 200 through Traefik.
- The standalone Linux AppImage contains its launcher, native executable, icon,
  and Docker installation helper.
- The exact AppImage completed a fresh installer journey and created a healthy
  three-container deployment through Traefik at `127.0.0.1:6256`.
- The Windows MSVC target passed a `cargo xwin check`. Native Windows installer
  execution remains a Windows-host release gate.

Current single-file Linux artifact:

```text
binary/XNOBrain-linux-x86_64.AppImage
SHA-256: 3a6b173da3faee3dde3e742ae3380beb91979f99d3fd0e6f501e316ba8e0f454
```

This is an unsigned development artifact, not a public release deliverable. It
embeds the isolated local-registry validation manifest. Release generation
now rejects loopback registries unless `config-local` is explicitly requested.

## Authentication/API audit limitation

The complete supplied Firebase Web API key was accepted by Firebase's
`accounts:signInWithPassword` endpoint and reached credential validation. The
build generator requires all 39 characters and writes the key as
`FIREBASE_API_KEY` for `Dockerfile.frontend`; the Brain UI constructs the
expected Identity Toolkit URL from that value. A synthetic
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
