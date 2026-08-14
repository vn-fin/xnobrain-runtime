# 013 — Architecture

## Component map

```text
Docker Web edition (Stages 1–3)
├── Small Tauri installer/maintenance utility
│   ├── Edition selection and install wizard only
│   └── Rust Docker installer driver
├── Docker runtime
│   ├── XNOBrain Web UI image
│   ├── FastAPI/Hermes runtime image
│   ├── 9router process
│   └── Persistent named data volume
└── User's default system browser          Product UI

Full Managed App (Stages 4–7)
├── Tauri native application
│   ├── Existing React/Vite product UI
│   ├── Integrated management UI
│   └── Rust managed-runtime core
└── Platform runtime
    ├── Linux packages/services
    ├── Windows managed WSL2 distribution
    └── macOS signed native-core runtime
```

The existing FastAPI/Hermes application remains the only application API.
Tauri commands are local install/management operations, not a second product
API. Docker Web never loads the product UI inside Tauri.

## Repository layout

All app-owned code and documentation lives below `app/`:

```text
app/
├── AGENTS.md                    App boundary and mandatory rules
├── README.md                    Contributor entrypoint
├── .gitignore                   Build, local state, and signing exclusions
├── Makefile                     Native-host build implementation
├── package.json                 App-only Node/Tauri scripts
├── package-lock.json            Pinned app frontend dependencies
├── index.html                   Installer/management shell entry
├── tsconfig.json
├── vite.config.ts
├── src/                         App-only React/TypeScript
│   ├── main.tsx
│   ├── App.tsx
│   ├── editions/                Web installer vs Full Managed selection
│   ├── installer/               Wizard, preflight, progress, recovery
│   ├── managed/                 App-version management screens
│   ├── bridge/                  Typed invoke/channel/event adapters only
│   ├── state/                   Reducers/state-machine projections
│   ├── components/              App-only accessible UI primitives
│   ├── styles/
│   └── test/
├── src-tauri/                   Rust/Tauri application
│   ├── Cargo.toml
│   ├── Cargo.lock
│   ├── build.rs
│   ├── tauri.conf.json
│   ├── capabilities/
│   │   ├── installer.json       Least privilege for Web installer
│   │   └── managed.json         Full Managed permissions
│   ├── icons/
│   └── src/
│       ├── main.rs              Minimal desktop entrypoint
│       ├── lib.rs               Wiring and command registration
│       ├── commands/            Thin typed Tauri boundary
│       ├── domain/              States, plans, manifests, errors
│       ├── services/            Installer/lifecycle orchestration
│       ├── drivers/
│       │   ├── docker/          Web edition only
│       │   ├── linux/           Managed app runtime
│       │   ├── windows_wsl/     Managed app runtime
│       │   └── macos/           Managed app runtime
│       ├── security/            Signature, digest, secret, redaction
│       ├── persistence/         Atomic local state
│       └── diagnostics/
├── schemas/                     Versioned manifest and IPC schemas
├── resources/                   Non-secret templates/notices bundled in app
├── scripts/                     Packaging/signing orchestration
├── tests/                       Cross-boundary and end-to-end tests
│   ├── fixtures/
│   └── e2e/
└── docs/                        App ADRs, support matrix, release operations
```

Generated frontend output, Rust `target/`, installers, signing intermediates,
download caches, secrets, and local runtime state are ignored and never
committed. Release artifacts go to Tauri's platform bundle directories and CI
publishes them without copying them into core repository directories.

### Dependency direction

```text
app/src → app/src/bridge → typed Tauri commands
                              ↓
commands → services → domain + driver traits
                         ↓
                      platform drivers

Full Managed product view → released loopback HTTP/SSE API
Docker Web product view   → default system browser → loopback HTTP/SSE API
```

No arrow points from core backend/Web UI source into app source. Tests may run
the released/core stack as an external fixture, but app code does not import
Python modules or `../src` files.

## Installer state machine

Persist non-secret state atomically after each transition:

```text
welcome
  → choose_edition
  → choose_docker_path
  → preflight
  → prerequisite_consent
  → waiting_for_prerequisite
  → waiting_for_reboot
  → runtime_plan
  → pulling
  → creating
  → starting
  → health_check
  → ready
```

Every long-running state also supports `cancelled`, `failed_retryable`, and
`failed_terminal`. Relaunch derives the next safe state from persisted intent
plus fresh system inspection; it never trusts an old “Docker running” flag.

## Installer and managed-runtime driver contract

Rust owns a platform-neutral interface conceptually equivalent to:

```text
capabilities() -> supported modes and reasons
preflight() -> checks[]
plan(manifest, choices) -> reviewed actions
install(plan, progress, cancellation)
status() -> absent | incomplete | stopped | starting | healthy | unhealthy
start(progress)
stop(timeout)
repair(progress)
uninstall_runtime(preserve_data)
diagnostics() -> redacted structured records
```

Neither installer React nor application React can pass arbitrary executables,
Compose YAML, shell fragments, paths, ports, or environment variables to this
interface. Commands use typed, validated inputs and a Tauri capability
allowlist.

## Runtime manifests

A signed JSON manifest contains at least:

```json
{
  "schema_version": 1,
  "release": "<semver>",
  "desktop_compatibility": "<range>",
  "images": {
    "ui": {"reference": "<registry/name>", "digest": "sha256:<digest>"},
    "runtime": {"reference": "<registry/name>", "digest": "sha256:<digest>"}
  },
  "architectures": ["amd64", "arm64"],
  "minimum": {"disk_bytes": 0, "memory_bytes": 0},
  "ingress": {
    "service": "traefik",
    "bind_host": "127.0.0.1",
    "container_port": 5152,
    "default_host_port": 5152
  },
  "health": {"path": "/api/brain/v1/health", "timeout_seconds": 0},
  "data_schema": 1
}
```

Docker Web and Full Managed App use separate signed manifests with explicit
edition fields and compatibility ranges. Exact registry names and measured limits are release configuration, not
hard-coded UI strings. The signature key is pinned in the desktop binary and
supports a documented rotation procedure.

## Docker installation layout

- Traefik is the only service with a Compose `ports` entry. It maps
  `127.0.0.1:<selected-host-port>:5152`; its dashboard stays disabled and
  `exposedByDefault` stays false.
- Frontend, runtime/FastAPI/Hermes, 9router, and optional observability services
  have no published host ports. Their internal ports are reachable only on the
  app-owned Docker networks through Traefik or required service-to-service
  links.
- The installer displays the manifest's `default_host_port` (initially 5152)
  and a Custom option. Custom input is a decimal integer from 1024 through
  65535. The user cannot configure the bind host or internal container port.
- Preflight binds/checks the exact loopback port immediately before create.
  Treat the earlier availability check as advisory because another process can
  win the race; translate Docker's bind failure back into port selection.
- Persist the selected host port atomically with install identity. Reinstall,
  repair, health checks, diagnostics, and browser launch use that stored value,
  not the current release default.
- Build the browser/health URL as `http://localhost:<selected-host-port>` and
  keep Traefik host routing compatible with both local browser and installer
  health requests. Never accept a user-supplied URL or hostname.
- Use a stable, namespaced Compose project name.
- Generate unique credentials on the device and store them in the OS secret
  store where possible; otherwise use an owner-only file.
- Bind the Traefik product endpoint to `127.0.0.1`, never all interfaces.
- Keep data in a stable named volume independent of image/container versions.
- Label all XNOBrain-owned Docker resources so repair/uninstall targets are
  exact and cannot touch unrelated Docker resources.
- Do not mount the Docker socket into the product runtime unless a separately
  reviewed feature requires it. The desktop driver talks to Docker from the
  host with narrowly defined operations.

## Installer UI

The wizard uses one primary action and one clear recovery action per screen:

1. Welcome and requirements summary.
2. Required Web Version/Full Managed App selection.
3. Use existing Docker/Install Docker selection.
4. Web access: **Default port 5152** or **Custom port**, with inline validation
   and conflict detection.
5. Preflight results with pass, warning, fail, and remediation.
6. Reviewed action plan including URL, download size, disk use, data location, and
   required elevation/reboot.
7. Progress with current action, total progress, elapsed time, and cancel.
8. Health verification through Traefik at the selected port.
9. Ready, with **Open Web Version**, which launches the system browser.

Progress comes from structured events (`phase`, `current`, `total`, `unit`,
`message_code`), not parsed human log text. Raw technical detail is available
behind “Details” and is redacted before display/export.

## Security and trust boundaries

- Sign the Web installer and every Full Managed App artifact; notarize macOS.
- Sign Tauri update artifacts and runtime manifests separately.
- Verify image digests after pull and before activation.
- Allow downloads only from configured HTTPS origins; no redirect to an
  unapproved origin.
- Never log provider credentials, Docker auth, generated secrets, prompts,
  user content, request bodies, or environment dumps.
- Elevation helpers accept a fixed operation enum and validated arguments,
  never an arbitrary command.
- Use a single-instance lock around installation and runtime mutations.
- The Full Managed App webview cannot navigate to untrusted origins;
  external links open in the system browser.

## Updates and rollback

- Web-installer update: signed updater, independent of the Web runtime.
- Full Managed App update: Tauri signed updater, applied only when compatible
  with the installed managed-runtime manifest.
- Docker runtime update: pull new digests alongside old images, stop/start at
  a controlled boundary, verify health, then mark active.
- On failure: restore the previous manifest/images and preserve the data
  volume. Data migrations require an explicit forward/rollback contract.
- Managed platform drivers use versioned runtime directories/packages and the same
  health-before-commit rule.
