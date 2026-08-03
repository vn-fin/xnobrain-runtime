# Boundaries and project structure

## Source ownership

App-owned paths:

- `app/src/`: installer and managed-app React/TypeScript.
- `app/src-tauri/`: Rust/Tauri commands, services, drivers, security, and
  packaging configuration.
- `app/schemas/`: versioned manifest and IPC schemas.
- `app/resources/`: non-secret bundled templates and notices.
- `app/scripts/`: signing/packaging orchestration.
- `app/tests/`: cross-boundary and end-to-end tests.
- `app/docs/`: app ADRs, support matrix, and release operations.

Read-only dependencies for app tasks:

- `src/`: existing Web UI.
- `brain4all/`, `server.py`: existing FastAPI/Hermes backend.
- `runtime/`, Dockerfiles, Compose files, and Hermes/9router integration.
- `docs/contracts/`: released contracts the app may consume.

Never import core TypeScript or Python source into the app. Communicate through
released HTTP/SSE contracts or use an unmodified production Web artifact.

## Dependency direction

```text
React component → typed bridge adapter → Tauri command
                                      → service → domain + driver trait
                                                        → platform driver
Managed product view → loopback HTTP/SSE contract
Docker Web UI → system browser → loopback HTTP/SSE contract
```

Commands translate IPC only. Services own installation/lifecycle rules. Domain
types express states, plans, manifests, errors, and progress. Drivers contain
OS/Docker operations. Security and persistence are reusable infrastructure.

## Edition rules

- `docker`: Web edition only; installer/maintenance utility opens the system
  browser after health succeeds.
- `managed`: native Tauri app; integrates management and product views and owns
  a platform-managed runtime.
- Use separate Tauri capability files, identifiers, manifests, and release
  metadata. Never accidentally grant managed permissions to the Web installer.
