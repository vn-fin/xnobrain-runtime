# E03 architecture

Grounded in [`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)
(envelope, delivery, cursor), [`docs/enterprise-extension.md`](../../../docs/enterprise-extension.md)
(hardware-upgrade sequence, image split, persistent volume), and the code
facts in [findings.md](findings.md). Everything marked **verify** is a Phase-0
item.

## 1. Component diagram

```
 user runtime (Incus container or PC)                brain4all-enterprise (Go)                Incus host(s)
┌──────────────────────────────────────┐            ┌────────────────────────────────┐      ┌──────────────────┐
│ FastAPI process (brain4all + Hermes) │            │ device API (E01)               │      │ incusd           │
│ ┌──────────────────────────────────┐ │  HTTPS 443 │  /device/v1/register|prove|    │      │  instances       │
│ │ lifespan task:                   │ │  outbound  │  refresh|claim                 │      │  profiles        │
│ │ services/device_connector.py     │◄├────────────┤  /device/v1/commands/poll      │      │  (resource       │
│ │  long-poll loop, backoff+jitter  │ │  only      │  /device/v1/commands/{id}/     │      │   classes)       │
│ │  heartbeats · journal · router   │ │            │   status                       │      │  custom storage  │
│ └───┬──────────────┬───────────────┘ │            │  /device/v1/heartbeat          │      │  volumes         │
│     │              │                 │            ├────────────────────────────────┤      │  (DATA_DIR)      │
│ integrations/      │ existing        │            │ internal/fleet                 │      └────────▲─────────┘
│ device_identity.py │ services only:  │            │  command dispatch + outbox     │               │
│  Ed25519 key,      │  runtime.detail │            │  heartbeat store · inventory   │   REST (unix socket
│  tokens, verify    │  usage reporter │            │  stale detection · revocation  │   or HTTPS :8443,
│ DATA_DIR/device/   │  (E02) flush    │            ├────────────────────────────────┤   client cert)
│  key · token ·     │                 │            │ internal/incus driver          ├───────────────┘
│  journal/ · cursor │                 │            │  provision · drain · replace   │
└──────────────────────────────────────┘            │  rollout                       │
                                                    ├────────────────────────────────┤
                                                    │ PostgreSQL (no ORM)            │
                                                    │  devices · heartbeats ·        │
                                                    │  commands · sandboxes ·        │
                                                    │  resource_classes · rollouts   │
                                                    │ admin API: /admin/v1/fleet     │
                                                    └────────────────────────────────┘
```

Rules the diagram encodes:

- The runtime side opens **one** outbound connection; no inbound listener
  (contract). Incus containers and PC installs run the *identical* connector —
  the only difference is how the enrollment token arrives.
- The connector calls **existing services only** (`PlatformService.runtime`,
  the E02 reporter) — never the filesystem, never Hermes directly.
- The Incus driver lives entirely in `brain4all-enterprise`
  ([`AGENTS.md`](../../../AGENTS.md)); the control plane talks to `incusd`
  over its REST API, never to the containers' network.
- Enterprise outage: the lifespan task backs off and retries forever; nothing
  else in the process depends on it.

## 2. Connector state machine

```
                 ENTERPRISE_API_URL unset ──────────────► (never starts)

 ┌──────────────┐  key/device_id exist? no → register+prove   ┌─────────────┐
 │ disconnected │────────────────────────────────────────────►│ registering │
 └──────┬───────┘                                             └──────┬──────┘
        ▲    ▲              token issued / refreshed                 │
        │    │                                                       ▼
        │    │  network error, 5xx, poll timeout               ┌───────────┐
        │    └────────── backoff (exp + jitter, cap) ──────────┤ connected │
        │                                                      └─────┬─────┘
        │   401/revoked → erase cloud creds, stop                    │
        │                                                            │ shutdown /
        │                                                            │ unpair / revoke
        │                                                      ┌─────▼─────┐
        └──────────────────── after drain ─────────────────────┤ draining  │
                                                               └───────────┘
```

- **disconnected**: no active poll; timer-driven retry with exponential
  backoff (base 1 s, factor 2, cap 5 min) plus full jitter.
- **registering**: generate key if absent (0600, `DATA_DIR/device/`), POST
  register (public key [+ enrollment token if `BRAIN4ALL_ENROLLMENT_TOKEN` is
  present]), answer the possession challenge, store `device_id` + short-lived
  token. Token refresh re-enters this state's *refresh* sub-step, not full
  registration.
- **connected**: loop of long-poll (with cursor) → validate → journal →
  execute → status-post; heartbeat every `HEARTBEAT_INTERVAL` (default 60 s,
  server may override in poll response — **verify** against E01).
- **draining**: stop accepting new commands, let the in-flight command reach a
  terminal journal state (bounded by its `expires_at`), post final statuses,
  close. Unpair additionally deletes `DATA_DIR/device/` contents (cloud
  credentials + journal only — never profile data).

Revocation observed (401 with `revoked` error on poll/refresh) behaves as
unpair minus user intent: erase token, keep the key (**verify** with E01
whether a revoked key may re-register or is banned; contract says revocation
"rejects token refresh").

## 3. Journal on disk — `DATA_DIR/device/`

```
DATA_DIR/device/
├── key.ed25519          # private key, 0600, never leaves the machine
├── device.json          # device_id, claimed flag, endpoint URL, 0600
├── token.json           # short-lived access token + expiry, 0600
├── cursor               # last acknowledged cursor (opaque string from server)
└── journal/
    ├── cmd_01H....json  # one file per command, atomic write (tmp + rename)
    └── ...
```

Per-command journal file (append-only state history, rewritten atomically on
each transition, **before** the corresponding ack — the contract's "journal
records received, accepted, running, and terminal state before acknowledging
transitions"):

```json
{
  "command_id": "cmd_01H...",
  "type": "runtime.ping",
  "issued_at": "2026-07-25T02:00:01Z",
  "expires_at": "2026-07-25T02:15:00Z",
  "attempt": 1,
  "state": "completed",
  "history": [
    {"state": "received",  "at": "2026-07-25T02:00:02Z"},
    {"state": "accepted",  "at": "2026-07-25T02:00:02Z"},
    {"state": "running",   "at": "2026-07-25T02:00:02Z"},
    {"state": "completed", "at": "2026-07-25T02:00:03Z"}
  ],
  "result": {
    "status": "completed",
    "error_code": null,
    "started_at": "2026-07-25T02:00:02Z",
    "finished_at": "2026-07-25T02:00:03Z",
    "usage": {"duration_ms": 412}
  }
}
```

- Duplicate `command_id` → return the recorded state; never re-execute
  (contract). Lookup is by filename.
- The journal stores **no payload plaintext** and no tokens — only envelope
  metadata, states, and the redacted result (non-sensitive error code +
  resource usage summary, per contract).
- Bounded: prune terminal entries older than 14 days or beyond 1,000 files,
  oldest first, with a dropped-counter (mirrors `05-telemetry.md`'s
  drop-oldest rule).
- Status posts retry independently of execution using `command_id` as the
  idempotency key (`03-device-connector.md`).

## 4. Heartbeat payload — allow-listed fields only

Source: `PlatformService.runtime.detail()`
([`brain4all/integrations/runtime.py`](../../../brain4all/integrations/runtime.py)),
filtered through an explicit allow-list. Per
[`docs/implementation/05-telemetry.md`](../../../docs/implementation/05-telemetry.md),
allowed attributes are service/version, hashed identifiers, runtime class,
status, latency and the like; **forbidden** are file content/paths,
user-entered names, credentials. Hostnames (`info.id`,
`system.os.hostname`), image names, and OS strings from `detail()` are
therefore **dropped**.

```json
{
  "device_id": "dev_01...",
  "runtime_version": "1.4.2",
  "protocol_version": 1,
  "capabilities": ["runtime.ping", "runtime.restart_gateway", "usage.flush",
                   "supervised_restart"],
  "clock_skew_ms": 120,
  "resources": {
    "cpu_percent": 3.1,
    "cpu_limit": 2.0,
    "uptime_seconds": 86400,
    "memory_bytes": 412000000,
    "memory_limit_bytes": 4294967296,
    "disk_usage_bytes": 9000000000,
    "disk_total_bytes": 53687091200,
    "net_rx_bytes": 123456,
    "net_tx_bytes": 654321
  },
  "gateway_healthy": true,
  "queued_commands": 0
}
```

Everything is a number, boolean, version string, or capability enum. Nothing
else from `detail()` crosses the wire. Redaction is enforced by constructing
the payload from named fields (allow-list), not by deleting keys from
`detail()` output (deny-list), and asserted by test
([validation.md](validation.md) row S-4).

## 5. Command routing table (type → existing service call)

| Command type | Routed to (existing surface only) | Result | Notes |
|---|---|---|---|
| `runtime.ping` | `PlatformService.runtime.detail()` → heartbeat-style summary (same allow-list as §4) | `completed` + resource summary | Read-only; also refreshes `last_seen` server-side |
| `runtime.restart_gateway` | Graceful self-termination: journal terminal state `completed` + post status **first**, then signal the process to exit cleanly so the supervisor/container restart policy restarts it | `completed` (posted pre-exit) | **Gated** on `supervised_restart` capability; otherwise `rejected` / `unsupported_capability`. No existing restart primitive exists in the codebase today (findings §5) — this is the only safe mapping. **Verify** supervision detection in Phase 0 |
| `usage.flush` | E02 usage reporter flush (planned `plans/enterprise/E02_usage_collection/`); until it lands: no-op | `completed` (+ `noop: true` pre-E02) | Kicks an immediate outbox push |
| `bundle.backup` | — deferred (separate backup/DR plan) | `rejected` / `unsupported_capability` | Named so the enum is stable |
| anything else / unknown | — | `rejected` / `unknown_type` | Never touches Hermes or profiles (03-device-connector acceptance) |

Validation order before any routing (contract): version → device → signature
(pinned control-plane key, canonical JSON) → expiry → payload hash → replay
state (journal). A command failing any check is journaled `rejected` with the
matching error code and **cannot** reach a service call.

## 6. Incus lifecycle sequences (enterprise repo)

Resource classes (placeholder tiers — final values are a product decision,
findings §8.4; they back the per-plan "hardware classes" of
[`docs/plans.md`](../../../docs/plans.md)):

| class_id | vCPU | RAM | DATA_DIR volume | maps to |
|---|---|---|---|---|
| `rc.s` | 1 | 2 GiB | 20 GiB | free / pro |
| `rc.m` | 2 | 4 GiB | 50 GiB | promax |
| `rc.l` | 4 | 8 GiB | 100 GiB | enterprise default |

Each class is an Incus **profile** (`limits.cpu`, `limits.memory`, root-disk
size — **verify** exact keys against Incus 6.x).

**Provision (per user):**

1. Validate plan: sandbox count within `docs/plans.md` limits (1/1/5/custom).
2. Create custom storage volume `vol-<user>` if absent (persistent `DATA_DIR`).
3. Mint a **one-time enrollment token** (E01), bound to the user, short TTL,
   single use.
4. Create instance `b4a-<user>-<n>` from pinned image `brain4all:<version>`
   with profile `rc.*`, attach `vol-<user>` at `/opt/data`, inject
   `ENTERPRISE_API_URL` and `BRAIN4ALL_ENROLLMENT_TOKEN` via instance env
   config (**verify** OCI env injection).
5. Start; the connector inside registers with the token → device is created
   **already claimed** by the owning user; token is consumed server-side and
   discarded in-container (decision D, [approaches.md](approaches.md)).
6. Wait for first heartbeat (health gate, timeout → mark `provision_failed`);
   record sandbox row; emit audit event.

**Replace-with-drain** (used for restart, resize, and version upgrade — the
`docs/enterprise-extension.md` sequence verbatim):

1. Store desired state (resource-class ID and/or image version) on the
   sandbox row.
2. Validate the plan/entitlement.
3. **Drain**: send `runtime.ping` to confirm liveness, then stop dispatching
   new commands to the device and wait for in-flight commands/runs to reach
   terminal state (bounded by command TTLs; hard cap e.g. 10 min).
4. **Apply**: for a pure restart, `restart` the instance; for resize, update
   the profile/limits; for image change, stop → delete instance (volume
   survives by construction) → create from new pinned image → reattach volume.
5. **Restart/verify**: start; health gate = device reconnects (cursor intact)
   and posts a healthy heartbeat within the window.
6. **Audit**: emit an audit event with actor, sandbox, old/new class or
   version, duration, outcome.

Because `DATA_DIR` (profiles, agent memory, skills, device identity, journal,
cursor) lives on the volume, the replaced container comes back as the **same
device** and resumes from its cursor with no duplicate execution.

**Version rollout (staged, health-gated):**

1. Admin creates a rollout: target image `brain4all:<new>`, canary size N,
   health window, failure threshold.
2. Driver replaces N canary sandboxes (replace-with-drain each).
3. Health gate: all canaries reconnected + heartbeating healthy + no
   heartbeat regression for the window → proceed; else **halt** rollout,
   leave the rest on the old pin, surface the failure (rollback of canaries =
   replace back to old pin, same sequence).
4. Proceed batch-wise to the full fleet; rollout row tracks per-device state
   (`pending` / `replacing` / `healthy` / `failed`).

## 7. Fleet inventory & health API (enterprise repo)

Heartbeats upsert `device_heartbeats` (latest row per device) and append a
small rolling history. **Stale detection**: `status = stale` when
`now - last_seen > 3 × heartbeat interval`; `offline` beyond 10 minutes
(thresholds configurable). All admin routes require admin RBAC (E01 auth
seam), return `401/403` per `docs/plans.md` rules.

| Method + path | Purpose |
|---|---|
| `GET /admin/v1/fleet` | List devices (filter: status, version, resource class, runtime type) |
| `GET /admin/v1/fleet/{device_id}` | Device detail: heartbeat history, recent commands, sandbox linkage |
| `POST /admin/v1/fleet/{device_id}/commands` | Enqueue a command (`runtime.ping`, `usage.flush`) |
| `POST /admin/v1/fleet/{device_id}/restart` | Restart with drain (Incus sandboxes: replace sequence §6; PC devices: `runtime.restart_gateway` command, refused without capability) |
| `POST /admin/v1/fleet/{device_id}/revoke` | Revoke: stop new commands, close stream, reject refresh (contract) |
| `POST /admin/v1/sandboxes` | Provision an Incus sandbox for a user (§6) |
| `PATCH /admin/v1/sandboxes/{id}` | Set desired resource class / image version (triggers replace-with-drain) |
| `POST /admin/v1/rollouts` · `GET /admin/v1/rollouts/{id}` | Create / observe a staged version rollout |

`GET /admin/v1/fleet` response example:

```json
{
  "devices": [
    {
      "device_id": "dev_01H...",
      "user": {"id": "usr_9...", "email": "a@example.com"},
      "runtime_type": "incus",
      "sandbox_id": "sbx_3...",
      "resource_class": "rc.m",
      "runtime_version": "1.4.2",
      "status": "online",
      "last_seen": "2026-07-25T09:58:11Z",
      "health": {
        "gateway_healthy": true,
        "cpu_percent": 3.1,
        "memory_bytes": 412000000,
        "memory_limit_bytes": 4294967296,
        "disk_usage_bytes": 9000000000,
        "disk_total_bytes": 53687091200,
        "clock_skew_ms": 120
      }
    },
    {
      "device_id": "dev_01J...",
      "user": {"id": "usr_2...", "email": "b@example.com"},
      "runtime_type": "pc",
      "sandbox_id": null,
      "resource_class": null,
      "runtime_version": "1.4.1",
      "status": "stale",
      "last_seen": "2026-07-25T09:41:02Z",
      "health": {"gateway_healthy": true, "cpu_percent": 0.4}
    }
  ],
  "total": 2
}
```

PC-enrolled and Incus-provisioned devices are the **same row shape**; only
`runtime_type`/`sandbox_id`/`resource_class` differ. Alerts on stale devices
are deferred (README non-goals) — the status field is the v1 deliverable.

PostgreSQL tables (raw SQL migrations, **no ORM**): `devices` (E01-owned;
E03 adds columns `runtime_type`, `runtime_version`, `capabilities`),
`device_heartbeats`, `device_commands` (dispatch outbox + status),
`sandboxes` (user, instance name, volume, desired/actual class + image,
state), `resource_classes`, `rollouts` + `rollout_devices`, `audit_events`
(shared with E01).

## 8. Persistent-volume layout (Incus sandbox)

One custom storage volume per user, attached at `/opt/data` (the runtime's
default `DATA_DIR`, see `brain4all/integrations/runtime.py`):

```
/opt/data                      ← Incus custom volume vol-<user> (survives replace)
├── device/                    ← §3: identity + journal + cursor
├── ...                        ← FileRepository data (brain4all/app.py wires
│                                DATA_DIR into FileRepository)
└── (profiles root if colocated per image layout — verify image's
    HERMES_PROFILES_ROOT placement in Phase 0)
```

Container root disk (image + writable layer) is **disposable**; the volume is
the only durable state, exactly as `docs/enterprise-extension.md` requires
("Keep profile data on a persistent volume so replacement does not move agent
memory or skills"). **Verify** in Phase 0: where the shipped image mounts
`HERMES_HOME`/`HERMES_PROFILES_ROOT` relative to `DATA_DIR`, so the volume
covers profiles *and* device identity with one mount (if not, attach the same
volume at a second path or a second volume — decide on the probe).
