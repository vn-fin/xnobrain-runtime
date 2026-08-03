# 013 — Architecture

## Component map

```text
Docker Web edition (Stages 1–3)
├── Small Tauri installer/maintenance utility
│   ├── Edition selection and install wizard only
│   └── Rust Docker installer driver
├── Docker runtime
│   ├── Brain4All Web UI image
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

Neither installer React nor application React can pass arbitrary executables, Compose YAML, shell
fragments, paths, ports, or environment variables to this interface. Commands
use typed, validated inputs and a Tauri capability allowlist.

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
  "health": {"path": "/health", "timeout_seconds": 0},
  "data_schema": 1
}
```

Docker Web and Full Managed App use separate signed manifests with explicit
edition fields and compatibility ranges. Exact registry names and measured limits are release configuration, not
hard-coded UI strings. The signature key is pinned in the desktop binary and
supports a documented rotation procedure.

## Docker installation layout

- Use a stable, namespaced Compose project name.
- Generate unique credentials on the device and store them in the OS secret
  store where possible; otherwise use an owner-only file.
- Bind product endpoints to `127.0.0.1`, never all interfaces by default.
- Resolve ports before creation and store the selected values.
- Keep data in a stable named volume independent of image/container versions.
- Label all Brain4All-owned Docker resources so repair/uninstall targets are
  exact and cannot touch unrelated Docker resources.
- Do not mount the Docker socket into the product runtime unless a separately
  reviewed feature requires it. The desktop driver talks to Docker from the
  host with narrowly defined operations.

## Installer UI

The wizard uses one primary action and one clear recovery action per screen:

1. Welcome and requirements summary.
2. Required Web Version/Full Managed App selection.
3. Use existing Docker/Install Docker selection.
4. Preflight results with pass, warning, fail, and remediation.
5. Reviewed action plan including download size, disk use, data location, and
   required elevation/reboot.
6. Progress with current action, total progress, elapsed time, and cancel.
7. Health verification.
8. Ready, with **Open Web Version**, which launches the system browser.

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
- Direct drivers use versioned runtime directories/packages and the same
  health-before-commit rule.
