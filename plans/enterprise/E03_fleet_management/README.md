# E03 — Fleet management (Incus + devices)

Priority: **P1, after E01** (the connector authenticates against E01's device
enrollment endpoints). Independent of E02 — the only touch point is the
`usage.flush` command, which degrades to a no-op until the E02 reporter exists.
Part of the enterprise program; read [`../README.md`](../README.md) first and
align with it exactly.

Read the sibling documents in order:

- [findings.md](findings.md) — what the contracts already fix, what the code
  actually exposes, risks, open questions.
- [architecture.md](architecture.md) — components, connector state machine,
  journal format, heartbeats, command routing, Incus sequences, fleet API.
- [approaches.md](approaches.md) — the five decisions and why.
- [implementation.md](implementation.md) — two tracks (OSS Python, enterprise
  Go), ordered file-by-file.
- [validation.md](validation.md) — acceptance checklist with evidence, mapped
  directly to `device-command-v1`'s security acceptance tests.

Also read before starting:
[`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)
(the governing contract),
[`docs/implementation/03-device-connector.md`](../../../docs/implementation/03-device-connector.md)
(responsibilities — ⚠️ retired Go-era spec: concepts stand, package paths do
not; the OSS side is Python),
[`docs/enterprise-extension.md`](../../../docs/enterprise-extension.md)
(container hardware upgrades, image split, persistent profile volume),
[`docs/plans.md`](../../../docs/plans.md) (managed sandbox counts and hardware
classes per plan), and [`AGENTS.md`](../../../AGENTS.md) ("Incus and cloud
runtime packaging are maintained outside this repository").

## Goal

Manage a fleet of **single-user runtimes** — some in Incus containers the
enterprise provisions, some on users' own PCs — from one control plane:

1. **OSS device connector (Python, this repo).** The client side of
   `device-command-v1`: Ed25519 device key under `DATA_DIR/device/` (0600),
   register/prove/refresh against the E01 endpoints, **one** outbound
   long-poll connection with backoff + jitter, heartbeats carrying version,
   capabilities, and a numeric resource summary (from the existing
   [`xnobrain/integrations/runtime.py`](../../../xnobrain/integrations/runtime.py)
   `LocalRuntimeManager`), a durable command journal (received → accepted →
   running → terminal, written **before** ack; duplicates return the recorded
   state), and an initial command set of `runtime.ping`,
   `runtime.restart_gateway`, and `usage.flush`. Commands route through
   **existing services only — never direct file access**. Dormant unless
   `ENTERPRISE_API_URL` is configured; an enterprise outage never blocks local
   use.
2. **Incus provisioning (enterprise repo, Go).** Per-user container lifecycle:
   create from the pinned public `xnobrain:<version>` image, persistent
   volume for `DATA_DIR` that survives container replacement, resource classes
   (CPU/RAM/disk tiers backing the hardware classes in `docs/plans.md`),
   start/stop/restart/replace **with drain** (reusing the
   `docs/enterprise-extension.md` hardware-upgrade sequence), staged image
   rollout (canary N → all, gated on health), and **auto-enrollment**: a
   provisioned container boots with a one-time enrollment token so it appears
   claimed to its owning user immediately.
3. **Fleet inventory & health (enterprise repo).** Devices table (from E01) +
   heartbeat ingestion; `GET /admin/v1/fleet` returning device, user, version,
   status, last_seen, resource class, and health; stale-device detection; a
   revocation flow.

A PC-installed user who enrolls manually and an Incus-provisioned user appear
in the **same fleet list**, managed through the same command channel.

## Non-goals

- **No cron over the command channel yet.** `device-command-v1` already covers
  misfire policies and missed-occurrence summaries; that machinery is deferred
  to a later plan. This plan implements only the identity, heartbeat, and
  command slices.
- **No Kubernetes.** Incus only, and Incus packaging lives in
  `xnobrain-enterprise` per `AGENTS.md` — this repo ships zero Incus code.
- **No auto-scaling.** Resource-class changes are explicit admin/entitlement
  operations, never automatic.
- **No backup/DR.** `bundle.backup` is a named-but-deferred command type; the
  managed backup program is a separate plan (enterprise README build-map #8).
- **No VPN/mesh.** Outbound-only TLS on 443, exactly as the contract mandates.
  No inbound listener, no tunnels.
- **No ORM** in either repo, per `docs/enterprise-extension.md`.
- **No alerts.** Stale-device *detection* ships; alerting/paging is deferred.

## Phases

- **Phase 0 — Freeze and probe.** Freeze the connector's used slice of
  `device-command-v1` (envelope fields, journal states, cursor, long-poll
  endpoint shapes) against E01's actual endpoints, and probe a real Incus host
  to resolve every "verify" item in [findings.md](findings.md).
- **Phase 1 — OSS device identity.** `xnobrain/integrations/device_identity.py`:
  key generation (0600), register, prove, refresh, unpair (erase cloud
  credentials only).
- **Phase 2 — OSS connector.** `xnobrain/services/device_connector.py`: the
  long-poll loop with backoff + jitter, journal, heartbeats, command router;
  lifespan wiring in `xnobrain/app.py` following the kanban-dispatcher
  pattern.
- **Phase 3 — OSS UI + tests.** "Enterprise" section in
  `src/features/system/` (SystemView tab pattern, as plan 012's
  `BlendsSection`); fake-control-plane tests in
  `xnobrain/tests/test_device_connector.py`.
- **Phase 4 — Enterprise fleet core.** `internal/fleet`: command dispatch,
  heartbeat ingestion + store, inventory queries, stale detection, revocation;
  migrations; `GET /admin/v1/fleet` and command endpoints.
- **Phase 5 — Incus driver + provisioning.** `internal/incus`: create from
  pinned image, persistent `DATA_DIR` volume, resource-class profiles,
  auto-enrollment token injection.
- **Phase 6 — Replace/drain + rollout.** The enterprise-extension
  hardware-upgrade sequence (drain → apply → restart/replace → verify → audit)
  and staged version rollout with health gates.
- **Phase 7 — End-to-end validation** per [validation.md](validation.md).

## Definition of done

- An admin provisions an Incus container for a user; it boots **pre-enrolled**
  (one-time token exchanged and discarded on first boot), heartbeats, and
  shows in `GET /admin/v1/fleet` with user, version, resource class, and
  health.
- The admin restarts that container remotely **with drain**: active runs
  drain, the container restarts, health is verified, an audit event is
  emitted, and the device reconnects with its cursor — no command executes
  twice.
- A **PC-installed (non-Incus) user enrolls manually** from the Settings
  Enterprise panel and appears in the **same fleet list**, distinguishable
  only by runtime type.
- **Unpair works and erases only cloud credentials** — device key/token/journal
  under `DATA_DIR/device/` are removed; profiles, agents, memories, and skills
  are untouched. Revocation from the server closes the stream and rejects
  token refresh.
- Every security acceptance test in
  [`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)
  passes against a fake control plane ([validation.md](validation.md)).
- With `ENTERPRISE_API_URL` unset, the connector never starts, opens no
  sockets, and creates no files. With it set but the server down, local
  features are fully functional (program principle: enterprise outage never
  blocks local).
