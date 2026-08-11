# 005 — Approaches & decisions

Three decisions drive the design. Each lists options, trade-offs, and the
choice. See `findings.md` for the facts these rest on and `architecture.md` for
the resulting contract.

## Decision 1 — Gateway topology: multiplexed vs per-agent

Brain4All maps each agent to a Hermes profile, and channels are per-profile.
How do the profiles' channels get *served*?

### Option A — Single multiplexed gateway (CHOSEN)

Enable `gateway.multiplex_profiles` on the **default** profile. One gateway
process owns the shared `:8642` listener and serves every agent's channels via
the `/p/<profile>/` prefix (verified: `gateway/config.py` l.897,
`web_server.py::_multiplex_port_binding_conflict` l.9469).

- **Pros:** one gateway process — aligns with the "one FastAPI/Hermes process"
  constraint (`AGENTS.md`); client-type channels (Telegram, Discord, Slack,
  Signal, Matrix, WhatsApp-web) multiplex cleanly per agent; least resource use;
  matches how the native dashboard already behaves.
- **Cons:** *port-binding* channels (`webhook, api_server, msgraph_webhook,
  feishu-webhook, wecom_callback, bluebubbles, sms, whatsapp_cloud, line`) can
  only run on the **default** agent — secondary agents get a 409. This is a
  genuine Hermes constraint, not a Brain4All limitation.

### Option B — Per-agent gateway process

Each profile runs its own `hermes gateway start`. Full isolation; every agent
can bind ports.

- **Pros:** no port-binding restriction; strong per-agent isolation.
- **Cons:** N gateway processes for N agents — heavy, and stretches the
  single-process spirit; each binds a distinct port (port management burden);
  more failure surface and lifecycle complexity. Overkill for the flagship use
  case (phone chat over client-type channels, which never bind ports).

### Choice

**Option A — single multiplexed gateway.** On first channel enablement, the
service ensures `gateway.multiplex_profiles=true` on the default profile (with a
one-time user-visible notice), then serves all agents' client-type channels
from one gateway. Port-binding channels are honestly restricted to the default
agent by mirroring Hermes' existing **409 guard** — never bypassed, never
hidden. This keeps one process, rides Hermes' own multiplex machinery, and
copies nothing. (Option B may be revisited later behind a flag if a real
multi-agent port-binding need appears; it is out of scope here.)

## Decision 2 — Credential storage: profile config vs Brain4All file

Where do bot tokens live?

### Option A — Profile `.env` + `config.yaml` (Hermes-native) (CHOSEN)

Tokens → profile `.env` (`save_env_value`); enabled flag →
`config.yaml` `platforms.<id>.enabled` (`write_platform_config_field`);
home-channel → `config.yaml`. Exactly where the running gateway already reads
them.

- **Pros:** the gateway reads them natively with zero glue; single source of
  truth; native dashboard and `hermes` CLI see the same state; no sync; no new
  format. Brain4All's mandated snapshot-before-mutation still applies (snapshot
  `.env` + `config.yaml`).
- **Cons:** tokens sit in the profile `.env` alongside other secrets — but
  that is already Hermes' security model and is covered by existing redaction.

### Option B — Brain4All credential file under `DATA_DIR`

Store tokens in a Brain4All-owned atomic file, inject into the gateway at start.

- **Pros:** Brain4All controls the format and snapshot lifecycle directly.
- **Cons:** duplicates Hermes' credential storage; requires re-injection glue
  and risks drift between the Brain4All file and what the gateway actually
  reads; a second place secrets can leak; violates "ride Hermes' config, don't
  invent a store" (`findings.md` §5). No benefit over A.

### Choice

**Option A — Hermes-native storage.** No Brain4All credential file. Reads return
redacted values only; writes snapshot first (reusing the
`integrations/config.py` snapshot mechanism) then call the public
`hermes_cli.config` writers. This is the direct analog of the Kanban rule
"Hermes SQLite is authoritative; add no second store."

## Decision 3 — Adapter mechanism: in-process Python vs loopback HTTP

How does `integrations/gateway.py` reach Hermes?

### Option A — In-process public Python APIs (CHOSEN for reads + config writes)

Import and call the public modules directly (as the Kanban adapter imports
`hermes_cli.kanban_db`): `gateway.config`, `hermes_cli.config`,
`gateway.pairing.PairingStore`, `gateway.status`, `gateway.drain_control`,
scoped to the target profile.

- **Pros:** no auth seam (loopback would have to pass Hermes' dashboard auth
  gate and risks 401); no serialization round-trip; matches the established
  Kanban precedent; these APIs are genuinely public (no underscore).
- **Cons:** the exact per-platform *state projection* and the *multiplex check*
  are implemented in the private `web_server` helpers, so Brain4All re-derives a
  thin version from public primitives (`load_gateway_config`,
  `_is_platform_connected`, `read_runtime_status`, `platform_binds_port`). That
  projection lives in the Brain4All **service** (policy), which is allowed — it
  is not copying the dispatch loop, SQL, or an adapter.

### Option B — Loopback HTTP to native `/api/messaging|gateway|pairing` endpoints

Have the adapter `httpx` the native endpoints on `127.0.0.1:8642`.

- **Pros:** rides the exact native payloads verbatim (including the private
  projection and multiplex check) — least re-derivation.
- **Cons:** must satisfy Hermes' auth middleware from within the same process
  (cookie/token) — fragile and easy to get wrong; adds an in-process HTTP hop;
  couples to endpoint shapes that are dashboard-internal rather than a stable
  Python contract.

### Choice — a pragmatic split

- **Reads, config writes, pairing, status, drain:** Option A (in-process public
  Python). The thin state projection + multiplex guard live in
  `services/channels.py` using public primitives.
- **Gateway start/stop/restart *process* control:** use the public gateway
  lifecycle path — the `hermes gateway {start,stop,restart}` CLI subcommand
  driven by a controlled subprocess spawn (the same public mechanism Hermes' own
  endpoint uses via `_spawn_hermes_action`), **or** the native `/api/gateway/*`
  endpoint if Phase 0 confirms an in-process caller can reach it. Brain4All must
  **not** import the private `_spawn_hermes_action`. Phase 0 confirms which
  public path is cleanest; if neither is reachable without private helpers,
  upstream a small public `hermes_cli.gateway.start_profile(...)` hook rather
  than copy the spawn logic (`findings.md` §7 Q2, `plans/001_kanban_foundation`
  Phase-0 §3 precedent).
- **Guided Telegram/WhatsApp onboarding:** ride the native
  `/api/messaging/*/onboarding/*` endpoints — they wrap an external setup
  service and re-implementing them would mean copying Hermes' onboarding client.
  If in-process loopback auth is a problem, the adapter calls the underlying
  onboarding helper functions if public, else proxies the native route. Decide
  in Phase 0.

### Rationale

This mirrors the Kanban program exactly: import Hermes' public data/runtime
APIs, keep product policy in the Brain4All service, and drive lifecycle through
Hermes' public control path without copying its internal loops. It avoids the
loopback-auth fragility for the high-frequency read/write paths while still
riding native endpoints for the two flows (lifecycle spawn, external onboarding)
where Hermes owns real machinery Brain4All must not duplicate.
</content>
