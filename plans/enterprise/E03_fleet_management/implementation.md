# E03 implementation — ordered steps, two tracks

Track 1 is OSS Python in this repo; Track 2 is Go in `brain4all-enterprise`
(paths there are relative to that repo's root). Phase 0 blocks both tracks.
Decisions referenced: [approaches.md](approaches.md); shapes:
[architecture.md](architecture.md); proof: [validation.md](validation.md).

## Phase 0 — freeze and probe (both tracks, blocking)

1. **Freeze the connector's used slice of
   [`device-command-v1`](../../../docs/contracts/device-command-v1.md)**
   against E01's actual endpoints: exact paths and DTOs for register / prove /
   refresh / claim / poll(+cursor) / ack / status / heartbeat; canonical-JSON
   rules and signature **test vectors** (findings §8.3); heartbeat-interval
   override field; revoked-key re-registration policy (findings — architecture
   §2). Record the frozen slice as an addendum in `docs/contracts/`
   (versioned; incompatible change ⇒ v2, per contract header). No Phase 1+
   code before this lands.
2. **Probe a real Incus host** and resolve every "verify" item from
   [findings.md](findings.md) §6 and architecture §8: OCI image
   `brain4all:<version>` boots under Incus 6.x (or needs conversion);
   `environment.*` config reaches the entrypoint; custom volume attach at
   `/opt/data` with correct ownership; which `limits.*` apply live vs need
   restart; unix-socket vs HTTPS trust bootstrap; the Incus Go client's module
   path/license (decision C); where the image places
   `HERMES_HOME`/`HERMES_PROFILES_ROOT` relative to `DATA_DIR` (one volume or
   two). Write results into findings.md §6 (replacing "verify" marks).
3. Confirm the `supervised_restart` detection env with the image owner
   (findings §8.2).

## Track 1 — OSS device connector (this repo, Python)

### Phase 1 — device identity

4. **`brain4all/integrations/device_identity.py`** (new). Owns everything
   under `DATA_DIR/device/` (architecture §3):
   - `DeviceIdentity(data_dir)` — lazy dir creation (`device/`, mode 0700;
     files 0600); `ensure_key()` Ed25519 via the stdlib-adjacent
     `cryptography` package (already a transitive dependency — verify in
     Phase 0, else vendor-free `pynacl` decision); key never serialized into
     logs or responses.
   - `register(endpoint, enrollment_token=None)` → POST public key
     (+ token when `BRAIN4ALL_ENROLLMENT_TOKEN` present), answer the
     possession challenge, persist `device.json` + `token.json`.
   - `refresh()` — proof-of-possession token refresh; `claim(code)` for
     account claim; `unpair()` — delete `device/` contents only (never
     profile data); `verify_envelope(cmd)` — version, device_id, pinned
     server-key signature over canonical JSON, expiry (with the bounded skew
     window and server-time offset, findings §7), `payload_sha256`.
   - Transport DTOs as plain dataclasses; no Pydantic dependency at this
     layer (matches integrations style, e.g. `runtime.py`).
5. **`brain4all/integrations/__init__.py`** — export `DeviceIdentity`
   alongside `LocalRuntimeManager`.

### Phase 2 — connector service + lifespan

6. **`brain4all/services/device_connector.py`** (new). The state machine of
   architecture §2:
   - `DeviceConnector(identity, platform_service, endpoint)`; single
   `async def run()` loop: registering → connected (long-poll with cursor,
   decision A) → dispatch; exponential backoff + full jitter on any error;
   one in-flight command; bounded status-retry deque (idempotent by
   `command_id`).
   - `Journal` class: per-command atomic files under `DATA_DIR/device/journal/`
     (tmp + `os.replace`), duplicate lookup, terminal-state pruning caps
     (architecture §3).
   - Heartbeat builder: **allow-list construction** from
     `platform_service.runtime.detail()` per architecture §4 — named numeric
     fields only; hostname/image/OS strings never read into the payload.
   - Command router: the table in architecture §5. `runtime.ping` →
     `runtime.detail()` summary; `runtime.restart_gateway` → journal+post
     `completed`, then graceful exit signal, gated on `supervised_restart`;
     `usage.flush` → E02 reporter flush if wired, else `completed`+`noop`;
     unknown → `rejected`/`unknown_type` with **no** service call.
   - Missed-occurrence summaries from poll responses: log + counter, no
     execution (cron machinery deferred; README non-goals).
7. **`brain4all/app.py`** — wire into the existing wrapped lifespan next to
   the kanban dispatcher: if `os.getenv("ENTERPRISE_API_URL")` is set, build
   `DeviceIdentity(data_dir)` + `DeviceConnector(...)` and
   `asyncio.create_task(connector.run(), name="brain4all-device-connector")`;
   on shutdown, `connector.drain()` then cancel with
   `suppress(asyncio.CancelledError)` — mirroring the dispatcher teardown. If
   unset: no task, no files, no sockets (dormancy is the absence of the
   task).
8. **`brain4all/services/platform.py`** — expose read-only connector status
   for the UI (`self.device_connector` optional): enrollment state, last
   connected, queued command count, endpoint; plus `unpair()` passthrough.
9. **Routes/handlers** — small additions in the existing single
   route-assembly point (`brain4all/routes/setup.py`) + `brain4all/handlers/`:
   `GET  /api/v1/enterprise/device` (status), `POST
   /api/v1/enterprise/device/enroll` (manual pairing token), `POST
   /api/v1/enterprise/device/unpair`. All local-only; they never proxy to the
   enterprise server beyond the connector's own calls.

### Phase 3 — Settings UI + tests

10. **`src/src/features/system/EnterpriseSection.tsx`** (new) — follow the
    `SystemView` tab pattern used by plan 012's `BlendsSection.tsx`: add
    `{ id: 'enterprise', label: 'Enterprise' }` to the `tabs` array in
    `src/src/features/system/SystemView.tsx`, a
    `{section === 'enterprise' && <EnterpriseSection ... />}` card, and the
    `'enterprise'` member on `SettingsSection` in `src/src/hooks/useRouter.ts`.
    Content is minimal per `03-device-connector.md` UX: enrollment status,
    cloud endpoint, last connected time, queued commands, an enroll-with-token
    form (PC path, decision D), and **Unpair** with a confirm explaining that
    only cloud credentials are erased. Anonymous-mode recovery-code display
    ships only if E01 exposes it (Phase-0 item).
11. **`src/src/features/system/api.ts`** — add the three device endpoints to
    the existing `systemApi`.
12. **`brain4all/tests/test_device_connector.py`** (new) — a **fake control
    plane** (in-process ASGI app or `httpx.MockTransport`) driving the full
    contract sequence, per the `device-command-v1` acceptance list:
    register → connect → dispatch → acknowledge → duplicate →
    reconnect-with-cursor → revoke. Security assertions
    ([validation.md](validation.md) rows S-1…S-5): invalid signature, wrong
    device, expired TTL (with skew window), replay, altered payload all
    `rejected` **and provably never call the router** (spy on
    `platform_service`); duplicate returns recorded state without
    re-execution; revocation stops the loop and rejects refresh; log capture
    contains no token/key/ciphertext material; unpair leaves a seeded profile
    tree untouched; `ENTERPRISE_API_URL` unset ⇒ no task, no
    `DATA_DIR/device/` created; key file mode is 0600.
13. Docs: `docs/api.md` (three routes), `docs/architecture.md` (connector
    paragraph + dormancy), `.env`/compose examples for `ENTERPRISE_API_URL`
    and `BRAIN4ALL_ENROLLMENT_TOKEN` (the latter documented as
    injected-by-provisioner, not user-set). `make check` green.

## Track 2 — enterprise repo (Go, `brain4all-enterprise`)

Raw SQL migrations only — extend, never rewrite, the E01 migration history
(`docs/enterprise-extension.md`); **no ORM** anywhere.

### Phase 4 — fleet core

14. **`migrations/`** — new numbered migrations: `device_heartbeats`,
    `device_commands` (outbox: envelope, state, cursor ordering, attempts),
    `sandboxes`, `resource_classes`, `rollouts`, `rollout_devices`; add
    `runtime_type`, `runtime_version`, `capabilities` columns to E01's
    `devices` (architecture §7).
15. **`internal/fleet/`** — `dispatch.go` (command outbox → per-device
    long-poll delivery with cursor + TTL filtering; signing with the pinned
    control-plane key over canonical JSON — same test vectors as Phase 0),
    `heartbeats.go` (ingest + upsert + rolling history), `inventory.go`
    (fleet queries, stale/offline derivation), `revoke.go` (close stream,
    reject refresh — asserts on every poll/status, findings §7).
16. **Admin endpoints** in the existing API composition: the route table of
    architecture §7 (`/admin/v1/fleet...`, `/admin/v1/sandboxes...`,
    `/admin/v1/rollouts...`), admin-RBAC-gated via the E01 auth seam;
    `401/403/429` semantics per `docs/plans.md`.

### Phase 5 — Incus driver + provisioning

17. **`internal/incus/driver.go`** — the `Driver` interface (decision C):
    `CreateInstance`, `SetState(start|stop|restart)`, `DeleteInstance`,
    `EnsureProfile(class)`, `EnsureVolume(user)`, `AttachVolume`,
    `UpdateLimits`, `WaitOperation`. **`internal/incus/rest.go`** — REST
    implementation over unix socket / HTTPS :8443 (client per Phase-0
    verification); **`internal/incus/fake.go`** — in-memory fake for tests.
    (CLI-backed `cli.go` only if Phase 0 forces the stopgap.)
18. **`internal/fleet/provision.go`** — the provision sequence of
    architecture §6: plan validation against `docs/plans.md` sandbox counts,
    volume, one-time enrollment token mint (E01), instance create from pinned
    `brain4all:<version>` with env injection, first-heartbeat health gate,
    audit event.

### Phase 6 — replace/drain + rollout

19. **`internal/fleet/replace.go`** — replace-with-drain implementing the
    `docs/enterprise-extension.md` hardware-upgrade sequence (desired state →
    validate → drain → apply → restart/replace → verify via
    reconnect+heartbeat → audit). Used by restart, resize (PATCH sandbox),
    and rollout.
20. **`internal/fleet/rollout.go`** — staged rollout per decision E: canary N
    → health window → batch remainder; halt on gate failure; per-device state
    rows; canary rollback path.
21. **Go tests** — `internal/fleet/..._test.go` with the fake Incus driver
    and a fake device (Go-side client speaking the frozen contract slice):
    provision happy path + plan-limit rejection; replace preserves the volume
    (fake asserts no volume delete); drain waits for in-flight command;
    rollout halts when a canary fails its health gate; dispatch signs
    envelopes that the *Python* test vectors accept (cross-language vector
    file from Phase 0); revocation closes an open poll. Heartbeat ingest
    rejects fields outside the §4 allow-list (defense in depth).

## Phase 7 — end-to-end validation

22. Run the full [validation.md](validation.md) checklist on a staging Incus
    host with a released `brain4all:<version>` image and one PC-style install;
    capture evidence per row; tick E03 in
    [`../README.md`](../README.md)'s program checklist.
