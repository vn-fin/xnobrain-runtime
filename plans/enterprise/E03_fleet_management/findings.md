# E03 findings — what exists, what the contracts fix, what is unverified

## 1. What `device-command-v1` already fixes

[`docs/contracts/device-command-v1.md`](../../../docs/contracts/device-command-v1.md)
is the governing contract for OSS-03, ENT-02, and ENT-03. Any incompatible
change creates `v2`. The pieces this plan implements verbatim:

**Transport and identity.** "The local runtime initiates outbound TLS over
port 443 using WebSocket or long polling with HTTP polling fallback. No
inbound listener is required. On first connection the runtime generates an
Ed25519 key pair, registers the public key, proves possession through a
challenge, and receives a device ID plus short-lived access token. Anonymous
Free devices are possession identities and may later be claimed by an account
without changing the device key." And: "Private keys never leave
`DATA_DIR/device/`. Tokens rotate, device revocation stops new commands, and
server commands are signed by a pinned control-plane signing key. Production
never disables TLS verification."

**The command envelope** (quoted from the contract):

```json
{
  "version": 1,
  "command_id": "cmd_01...",
  "device_id": "dev_01...",
  "type": "cron.execute",
  "agent_id": "abc123",
  "resource_id": "cron_01...",
  "reservation_id": "res_01...",
  "scheduled_at": "2026-07-20T02:00:00Z",
  "issued_at": "2026-07-20T02:00:01Z",
  "expires_at": "2026-07-20T02:15:00Z",
  "attempt": 1,
  "payload_ciphertext": "base64...",
  "payload_sha256": "hex...",
  "signature": "base64..."
}
```

"The signature covers every field except `signature` using canonical JSON. The
runtime validates version, device, signature, expiration, payload hash, agent
ownership, reservation, and command replay state before execution."

**Delivery semantics.** "Delivery is at-least-once. `command_id` is the
idempotency key. The local journal records `received`, `accepted`, `running`,
and terminal state before acknowledging transitions. A duplicate returns the
recorded state and never starts Hermes again." Statuses are `accepted`,
`running`, `completed`, `failed`, `expired`, `rejected`, `cancelled`. A
completion "never includes credentials or unrestricted profile content."

**Offline cursor.** "The connector supplies its last acknowledged cursor when
reconnecting. The server returns commands still inside their TTL and one
compact missed-occurrence summary." Misfire policies (`skip`, `notify`,
`run_latest`, `ask`, `replay_bounded`, default `notify`) exist in the contract
but are **cron machinery — out of scope for E03** (README non-goals). E03 only
sends the cursor and must tolerate receiving a missed-occurrence summary
(log-and-drop with a counter in v1).

**Security acceptance tests** (the contract lists them explicitly; they become
[validation.md](validation.md) rows 1:1): invalid signature / wrong device /
unknown agent / expired TTL / replay / altered payload rejected; reconnect
delivers no duplicate execution; revocation closes the stream and rejects
token refresh; logs and spans contain no access token, ciphertext plaintext,
prompt, or signing material; a fake server can drive register → connect →
dispatch → acknowledge → reconnect → revoke.

## 2. What `03-device-connector.md` requires — with the retired-spec caveat

⚠️ [`docs/implementation/03-device-connector.md`](../../../docs/implementation/03-device-connector.md)
is a **retired Go-era spec** (per [`../README.md`](../README.md) grounding
table). Its package paths (`internal/device`, `services/deviceconnector`) are
Go layout and do **not** apply — the OSS side is Python. Its *concepts* stand:

- Generate and protect the local device key; register anonymous, claim later.
- "Maintain one bounded outbound port-443 connection with exponential backoff
  and jitter."
- Refresh short-lived credentials through proof of key possession.
- "Route accepted commands to shared services, never direct filesystem
  functions."
- Heartbeats: version, capabilities, execution status, redacted diagnostics.
- "Revoke/unpair locally and erase only cloud credentials, never profile data."
- "A command is durably journaled before acceptance. Status reporting retries
  independently from execution and uses the same idempotency key."
- Bounded queues; backpressure must not create unbounded tasks or memory.
- UX: "The UI always exposes last connected time, queued commands, cloud
  endpoint, and Unpair." Local operation never requires pairing.
- Acceptance: works behind NAT and a standard HTTPS proxy; device offline
  never blocks local API; invalid commands cannot call Hermes or mutate
  profiles; no secrets in logs/traces/health APIs.

Python translation of the boundary: `brain4all/integrations/device_identity.py`
owns keys, tokens, signature verification, and transport DTOs;
`brain4all/services/device_connector.py` owns lifecycle, reconnect, dispatch,
and service adapters (see [implementation.md](implementation.md)).

## 3. The hardware-upgrade sequence (`docs/enterprise-extension.md`)

The "Container hardware upgrades" section defines the exact orchestration
sequence E03 reuses for **every** restart/replace/resize:

> Store a desired resource-class ID on the managed sandbox, validate the plan,
> drain active runs, apply the new CPU/RAM/storage limits through the
> container platform, restart or replace the worker, verify health, and emit
> an audit event. Keep profile data on a persistent volume so replacement does
> not move agent memory or skills.

Also fixed there: separate versioned images — `brain4all:<version>` is the
public self-contained runtime, `brain4all-enterprise:<version>` the private
control plane; "Enterprise deployments may pull the public runtime image from
the open-source release. The reverse dependency is forbidden." Orchestration
"may select CPU, memory, storage, and accelerator classes without rebuilding"
the API image. Neither project may introduce an ORM. And: "The local edition
reports host/container resources but does not resize its own container" — so
resize is purely control-plane-side; the connector only reports.

`docs/plans.md` fixes the counts the provisioner must respect: **managed
sandboxes 1 / 1 / 5 / custom-unlimited** for free/pro/promax/enterprise, and
"Cloud Free and Pro each resolve one authenticated member in one personal
tenant with different quotas and hardware classes" — hardware classes are a
per-plan attribute, which E03 models as resource-class tiers
([architecture.md](architecture.md)).

## 4. What `LocalRuntimeManager` actually exposes (verified by reading)

[`brain4all/integrations/runtime.py`](../../../brain4all/integrations/runtime.py)
(read 2026-07-25) — class `LocalRuntimeManager`, one public method
`detail()` reading cgroup v2 and proc files, no shelling out. It returns:

- `info`: `id` (**= `socket.gethostname()`**), `status`, `type: "container"`,
  `image` (env `HERMES_RUNTIME_IMAGE`), `created_at`, `gateway`
  (`healthy`, `port` from `API_SERVER_PORT`, default 8642), `resources`
  (`cpus`, `memory`, `root_size` as formatted strings).
- `metrics`: `cpu_percent`, `vcpu_time_ns`, `uptime_seconds`, `memory_bytes`,
  `memory_limit_bytes`, `memory_available_bytes`, `disk_usage_bytes`,
  `disk_total_bytes`, `net_rx_bytes`, `net_tx_bytes` — all numeric.
- `system`: `cpu_percent` plus `os` (**hostname**, os name, os_version,
  kernel_version from `/etc/os-release` and `platform.release()`).
- `health`: `healthy`, `status_code`, `endpoint`.

**Consequence for heartbeats:** the connector must consume the numeric
`metrics` block and the `health`/`gateway` booleans, and must **drop**
`info.id`, `system.os.hostname`, `info.image`, and OS strings — hostnames and
image names are identifying strings, and
[`docs/implementation/05-telemetry.md`](../../../docs/implementation/05-telemetry.md)
forbids "user-entered names" and file paths while allowing only
service/version, hashed identifiers, runtime class, and similar. The heartbeat
payload allow-list is specified in [architecture.md](architecture.md).

It is already wired: `brain4all/app.py` constructs `LocalRuntimeManager` and
passes it into `PlatformService` (`self.runtime`,
`brain4all/services/platform.py`), so the connector reaches it through the
service layer, not by constructing its own.

## 5. Existing patterns the OSS track reuses

- **Lifespan background task.** `brain4all/app.py` `register()` wraps the
  upstream lifespan and starts the kanban dispatcher with
  `asyncio.create_task(dispatcher_loop(), name="brain4all-kanban-dispatcher")`,
  cancelling it on shutdown with `suppress(asyncio.CancelledError)`. The
  connector copies this pattern exactly (decision B in
  [approaches.md](approaches.md)).
- **Settings tab pattern.** `src/src/features/system/SystemView.tsx` renders a
  `tabs` array (`profiles`, `vm`, `connectors`, `blends`) keyed by
  `SettingsSection` and conditionally renders section components; plan 012
  added `BlendsSection.tsx` this way. The Enterprise panel adds one more tab
  and one more section component.
- **Existing safe operations for command routing.** Verified surface:
  `PlatformService.runtime.detail()` for `runtime.ping`. For `usage.flush`,
  the E02 reporter (planned in `plans/enterprise/E02_usage_collection/`) will
  expose a flush method; until it lands, `usage.flush` completes with
  `completed` + `noop: true` result. **Gap found:** there is *no existing
  restart primitive* in the Python codebase today — no supervisor client, no
  process-restart service (grep for `restart` hits only team-runs staleness
  handling in `brain4all/services/team_runs.py`). `runtime.restart_gateway`
  therefore maps to a *graceful self-termination after journaling the terminal
  state and acking*, relying on the container/service restart policy to bring
  the process back — and is **refused** (`rejected`, error code
  `unsupported_capability`) when the runtime does not advertise the
  `supervised_restart` capability. Details and the Phase-0 verification in
  [architecture.md](architecture.md) and [approaches.md](approaches.md).

## 6. Incus primitives needed (enterprise repo) — verify against Incus 6.x docs

Per [`AGENTS.md`](../../../AGENTS.md): "Incus and cloud runtime packaging are
maintained outside this repository" — everything below lands in
`brain4all-enterprise`. All specifics below are from general Incus knowledge
and are **Phase-0 "verify against Incus 6.x docs / a live host"** items:

- **Images**: import the OCI/`brain4all:<version>` image and alias it
  (`incus image import` / remote alias). *Verify:* whether the published
  Docker/OCI image runs directly under Incus's OCI support or needs conversion
  to an Incus image; which Incus version introduced stable OCI container
  support.
- **Instances**: create/start/stop/restart/delete
  (`POST /1.0/instances`, `PUT /1.0/instances/{name}/state`). *Verify:*
  request shapes and async-operation polling in the 6.x REST API.
- **Profiles / limits**: resource classes as Incus profiles carrying
  `limits.cpu`, `limits.memory`, and a root-disk `size` device. *Verify:*
  exact keys, live-update semantics (which limits apply without restart), and
  root-device size handling per storage driver.
- **Storage volumes**: one custom storage volume per user for `DATA_DIR`,
  attached as a disk device (`source=<volume>`, `path=/opt/data`), surviving
  instance deletion. *Verify:* custom-volume attach syntax, ownership/idmap
  behavior for the container user writing `DATA_DIR`.
- **Config/env injection**: `environment.BRAIN4ALL_ENROLLMENT_TOKEN` and
  `environment.ENTERPRISE_API_URL` instance config keys. *Verify:* that
  `environment.*` keys reach the container init process in OCI mode, and
  whether they are readable via the Incus API afterwards (they are — which is
  why the token must be one-time and discarded; decision D in
  [approaches.md](approaches.md)).
- **Exec**: `POST /1.0/instances/{name}/exec` for last-resort diagnostics
  only; normal operation never needs exec because the device channel exists.
- **API access**: local unix socket (`/var/lib/incus/unix.socket`) or HTTPS
  :8443 with trusted client certificate. *Verify:* socket path and trust
  bootstrap on the target host OS.

## 7. Risks

- **Long-poll vs WebSocket behind proxies.** Corporate proxies and LBs kill
  idle WebSockets and long-lived requests unpredictably. Mitigation: start
  with long-poll (25–30 s server timeout, well under common 60 s proxy
  idle limits), jittered backoff, and treat frequent early disconnects as
  normal (decision A). The contract explicitly allows "long polling with HTTP
  polling fallback."
- **Container replace vs data volume.** Replace must never delete the
  `DATA_DIR` volume. Mitigation: volume is a separate Incus storage volume
  attached by reference; the replace sequence deletes only the instance;
  provisioner refuses forced-delete paths that could cascade; validation
  includes a replace-preserves-profile test.
- **Clock skew on TTLs.** `expires_at` checks on the device fail if the
  device clock drifts. Mitigation: the connector compares against server time
  learned from response headers/heartbeat acks (offset estimate), allows a
  bounded skew window (±2 min, configurable), and reports drift in the
  heartbeat so the fleet view can flag it. Incus containers share the host
  clock, so this mainly affects PC installs.
- **Token refresh vs revocation race.** A revoked device with a still-valid
  short-lived token could receive one more poll's worth of commands.
  Mitigation: server checks revocation on every poll and every status post,
  not just at token issue (E01 requirement; asserted in validation).
- **Self-restart command on unsupervised installs.** A bare `python -m` PC
  install would just die on `runtime.restart_gateway`. Mitigation: capability
  gating (§5) — the command is rejected unless the runtime is supervised.
- **Journal growth.** At-least-once + per-command files can accumulate.
  Mitigation: bounded journal with pruning of terminal entries (age + count
  caps), per `03-device-connector.md` bounded-resources rule.

## 8. Open questions (answer in Phase 0)

1. Exact E01 endpoint paths and request/response DTOs for register / prove /
   refresh / poll / ack / status — E03 freezes its **used slice** of
   `device-command-v1` against them before Phase 1 code.
2. What does the runtime advertise as `supervised_restart`? Proposed: true
   when running inside the shipped Compose/Incus image (detectable via an env
   set by the image, e.g. `BRAIN4ALL_SUPERVISED=1`), false otherwise. Confirm
   with the image owner.
3. Canonical-JSON definition for signature verification (key order, number
   formatting) — must match the Go signer byte-for-byte; freeze test vectors
   in the contract directory.
4. Resource-class tier values (vCPU/RAM/disk per class, class-per-plan
   mapping) — product decision; architecture carries placeholders.
5. Incus 6.x verifications from §6 (OCI image support, env injection,
   custom-volume attach, live limit updates).
6. Does the control plane want heartbeat-carried `net_rx/net_tx` counters in
   v1, or are they noise? Default: include (they are plain numbers and already
   computed).
