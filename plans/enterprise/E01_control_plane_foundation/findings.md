# E01 — Findings

What the public-repo contracts already fix, so the implementation does not
re-decide it. Cross-links: [README.md](README.md),
[architecture.md](architecture.md), [approaches.md](approaches.md),
[implementation.md](implementation.md), [validation.md](validation.md).

All paths below are relative to the public repo
`/home/kim/Documents/xno/xnobrain-dev/xnobrain` unless prefixed with
`xnobrain-enterprise/`.

## 1. Device identity is already specified — `docs/contracts/device-command-v1.md`

The identity slice E01 implements is fixed by the contract, verbatim:

> "On first connection the runtime generates an Ed25519 key pair, registers
> the public key, proves possession through a challenge, and receives a
> device ID plus short-lived access token. Anonymous Free devices are
> possession identities and may later be claimed by an account without
> changing the device key."

and:

> "Private keys never leave `DATA_DIR/device/`. Tokens rotate, device
> revocation stops new commands, and server commands are signed by a pinned
> control-plane signing key."

Consequences E01 must honor:

- **Ed25519** is the device key algorithm — not configurable.
- Identity is **possession-based**: register + challenge-prove, no account
  needed. Anonymous devices are first-class (Free tier).
- **Claim does not rotate the key.** Claiming maps device → user → tenant;
  the keypair is untouched.
- Tokens are **short-lived and rotated**; refresh is "proof of key
  possession" (retired spec `docs/implementation/03-device-connector.md`:
  "Refresh short-lived credentials through proof of key possession").
- **Revocation stops token refresh** (contract acceptance test: "Revocation
  closes the stream and rejects token refresh"). E01 has no stream yet, so
  the testable half is: revoked device cannot refresh and its tokens stop
  working.
- Transport is **outbound TLS 443 only** from the device side — the control
  plane never dials user machines. E01 only serves HTTPS endpoints; the
  WebSocket/long-poll channel is out of scope (E03).
- Security acceptance from the contract that applies now: "Logs and spans do
  not contain the access token, ciphertext plaintext, prompt, or signing
  material." and "A fake server can drive the complete register, connect,
  dispatch, acknowledge, reconnect, and revoke sequence." — E01 inverts the
  latter: a **fake device** drives register → prove → refresh → claim →
  revoke against the real server (see [validation.md](validation.md)).

**Not** fixed by the contract (E01 must decide, see
[approaches.md](approaches.md)): the token *format* (JWT vs opaque), exact
TTLs, challenge storage, and the claim-authorization mechanics. The contract
constrains behavior, not encoding.

## 2. The entitlement document shape — `docs/contracts/entitlements-v1.md`

> "An entitlement response contains version, edition, plan ID,
> subject/tenant, issued/expiry times, revision/ETag, numeric limits,
> capability flags, and hardware class. Numeric `-1` is unlimited and `0` is
> unavailable. Unknown fields are ignored by older clients; unknown required
> capabilities fail closed."

Also fixed:

- Initial numeric resources (agents, cron definitions, cron concurrent runs,
  cron runs/day, cron runs/month, provider connections/type, teams,
  agents/team, MCP servers, storage bytes, …) — E01 serves the subset that
  `docs/plans.md` gives values for and may omit the rest (unknown fields are
  tolerated by design).
- Quota operations `Check`/`Reserve`/`Commit`/`Release` with idempotency
  keys, leases, and stable denial codes exist in the contract but are an
  explicit **E01 non-goal** — deferred to the later entitlements/billing
  plan. E01 serves the *document* only.
- "Telemetry and headers never serve as accounting state." — reinforced by
  `docs/enterprise-extension.md` ("The control plane is the billing source
  of truth; HTTP headers and traces describe decisions but are not
  accounting records.").
- HTTP presentation: `429` exhausted quota, `403` unavailable capability,
  `401` missing auth. E01 uses `401`/`403` on the endpoints it ships.

## 3. The plan matrix and retention tiers — `docs/plans.md`

Fixed facts E01 seeds into the `plans` table:

- Stable plan IDs: **`free`, `pro`, `promax`, `enterprise`**. `free` displays
  as **Basic**. "Newly authenticated testing accounts default to `free`."
- The numeric matrix (managed resources; local Hermes is always unlimited):

  | Resource (limit key) | free | pro | promax | enterprise |
  |---|---:|---:|---:|---:|
  | `agents` | 4 | 20 | 100 | -1 |
  | `sandboxes` | 1 | 1 | 5 | -1 |
  | `teams` | 1 | 10 | 50 | -1 |
  | `agents_per_team` | 2 | 10 | 50 | -1 |
  | `mcp_servers` | 5 | 25 | 125 | -1 |
  | `provider_connections_per_type` | 1 | 5 | 25 | -1 |
  | `cron_definitions` | 4 | 50 | 250 | -1 |
  | `cron_concurrent_runs` | 1 | 5 | 25 | -1 |
  | `cron_runs_per_day` | 10 | 250 | 1250 | -1 |
  | `cron_runs_per_month` | 200 | 5000 | 25000 | -1 |
  | `telemetry_events_per_day` | 100000 | 1000000 | 5000000 | -1 |
  | `telemetry_retention_days` | 7 | 90 | 180 | 365 |

  Enterprise cells are "Custom/unlimited" in the source; the **default
  seed** is `-1` (a contract-configured tenant overrides later — that
  mechanism is the deferred billing plan). Retention 365 for enterprise is
  explicit in the matrix, not `-1`.
- Capability flags at v1 seed: `managed_telemetry`, `trace_explorer`,
  `usage_dashboards` = true for all four plans (all ✅ in the matrix);
  `rbac`, `sso`, `audit_export` = **false for every plan** — the matrix
  marks them "Planned"/"Planned/contract", and an unshipped capability must
  read false (unknown required capabilities fail closed on the client).
- Enforcement rules that shape E01 behavior: "Self-hosted OSS services
  always resolve unlimited local limits, even when the Enterprise API is
  offline or the signed-in plan is Basic." — i.e. the OSS repo *never* needs
  this service; and "No service may use an ORM."

## 4. Repository and ownership rules — `docs/enterprise-extension.md` + `docs/repository-ownership.md`

- The control plane is a **thin private composition named
  `xnobrain-enterprise`** — Go + PostgreSQL. It "owns authentication, tenant
  and plan resolution, billing entitlements, distributed quota reservations,
  RBAC, audit events, secret management, managed container orchestration,
  and telemetry retention."
- **No ORM, either repo.** Raw SQL and migrations only. "Enterprise
  migrations may extend but must not rewrite the open-source migration
  history" (moot for a fresh repo, but it forbids ever renumbering).
- **Auth over gRPC:** "Enterprise principal resolution should call the
  existing external auth service over gRPC rather than migrating
  authentication code here." E01 therefore ships a `PrincipalResolver`
  **seam** (interface + gRPC client skeleton + dev stub), not an auth server.
- **PostgreSQL is the billing source of truth**; reservations (when they
  arrive) "need an idempotency key, tenant ID, resource name, quantity, UTC
  period, and final committed or released state." E01's schema must not
  paint us into a corner here (the deferred `quota_reservations` table can be
  a later migration; nothing in v1 blocks it).
- `pkg/edition.Policy`: "returns limits for the authenticated principal.
  Numeric `-1` means unlimited." The OSS docs *expect* this package to be
  implemented by the enterprise repo ("It … implements `pkg/edition.Policy`").
- Module-path rule: "If its binary imports this repository's `internal`
  composition packages, its Go module path must be nested under
  `github.com/vn-fin/xnobrain/`." Since the OSS repo is now Python, there is
  nothing to import — **prefer HTTP contracts**; the module path is free
  (decision E in [approaches.md](approaches.md)).
- `docs/repository-ownership.md`: `xnobrain-enterprise` owns "Accounts,
  tenants, plans, managed metadata, and hosted telemetry"; "The OSS server
  communicates with Enterprise only through the public HTTP and telemetry
  contracts and `ENTERPRISE_API_URL`."
- Tenancy shape (`docs/enterprise-extension.md`): "Cloud Free and Pro each
  resolve one authenticated member in one personal tenant with different
  quotas … Enterprise adds organizations, multiple members, RBAC, SSO …" —
  so v1 tenancy is **personal tenant per user**, auto-created; org tenants
  are schema-compatible but have no management surface yet.

## 5. Program frame — `plans/enterprise/README.md`

- E01 is plan package 1 of 4; "E02 is deliberately buildable with only the
  *device enrollment* slice of E01" — which is exactly why E01 must finish
  the identity slice cleanly and defer everything else.
- Program principles restated as binding: contracts in `docs/contracts/`
  (new ones versioned; E01 expects to add **none** — it implements existing
  ones); `-1` = unlimited; telemetry/headers never accounting state; Go +
  PostgreSQL, no ORM.

## 6. ⚠️ Retired-spec caveat

`docs/implementation/00…06-*.md` are **retired Go-era specs** written when
the OSS runtime itself was planned in Go. Per `plans/enterprise/README.md`:
"concepts and contracts stand; package paths do not." Concretely for E01:

- `03-device-connector.md` package names (`internal/device`,
  `services/deviceconnector`) describe the *OSS-side* connector and are
  dead as paths — the OSS connector will be Python (E02/E03 concern). Its
  *behavioral* list (anonymous possession identity, claim without key
  rotation, refresh by proof of possession, revocation semantics, fake-server
  test matrix) remains authoritative.
- `05-telemetry.md` allowed/forbidden attribute lists bind every byte that
  crosses the wire — relevant to E01 only as the logging redaction rule
  (never log tokens, keys, or nonces).
- Do **not** copy any `internal/...` layout from those files into
  `xnobrain-enterprise`; the layout in [architecture.md](architecture.md)
  is defined fresh.

## 7. What exists today

- `plans/enterprise/E01_control_plane_foundation/` — this plan (the program
  README already links to it).
- The `xnobrain-enterprise` repository **does not exist yet** — Phase 0
  creates it. There is no enterprise code anywhere in the public repo, and
  none may be added (`AGENTS.md`: "Do not add Go, PostgreSQL, an ORM, or
  another application API process" — that rule is about *this* repo, which is
  precisely why the control plane is a separate one).
- The contracts E01 implements (`device-command-v1`, `entitlements-v1`) are
  already published in `docs/contracts/`; no public-repo contract additions
  are expected from E01 (see [implementation.md](implementation.md) §
  public-repo deliverables).

## 8. Open questions

1. **Exact auth-service gRPC protocol.** `docs/enterprise-extension.md` says
   "call the existing external auth service over gRPC" but no proto, address,
   or claim shape is documented anywhere in the public repo ("verify" with
   the auth-service owners). E01 mitigates by shipping the
   `PrincipalResolver` interface + a dev-mode static resolver; the real gRPC
   client fills in behind the interface without touching handlers. The proto
   file, when obtained, lives in `xnobrain-enterprise` (it is not a
   cross-repo OSS contract).
2. **Admin auth story before SSO.** Nothing specifies how operators
   authenticate to `/admin/v1/*` pre-SSO. Decision D in
   [approaches.md](approaches.md): static bearer token from env, with the
   risk documented and an explicit migration path to RBAC (program function 6).
3. **Key-rotation cadence.** `device-command-v1` says "Tokens rotate" and the
   retired connector spec's test matrix includes "key rotation", but no
   contract defines *device key* rotation triggers or cadence. E01 ships
   token rotation only; device-key rotation is flagged as a `v1.x` contract
   question (would need a signed key-succession message — "verify"/defer).
4. **GitHub org / module path.** `github.com/vn-fin/xnobrain/` appears in
   `docs/enterprise-extension.md`; whether the private repo lives at
   `github.com/vn-fin/xnobrain-enterprise` must be confirmed at repo
   creation ("verify"). Nothing in the plan depends on the exact host path.
5. **Claim proof strength.** v1 claim requires the caller to present both a
   valid user bearer *and* a live device token (see
   [architecture.md](architecture.md) § claim). Whether a human-friendly
   out-of-band claim code (device shows code → user types it in a web
   console) is needed before E03's pairing UX — "verify" with product;
   designed as a compatible v1.1 addition, not a blocker.
6. **Serving port.** No document fixes the enterprise API port. The plan
   picks container port `8080`, compose-published as `8710` ("verify" /
   adjust freely; the OSS side only ever sees `ENTERPRISE_API_URL`).
