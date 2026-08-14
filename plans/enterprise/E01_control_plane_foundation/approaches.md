# E01 — Approaches

Decisions with options considered. Cross-links: [README.md](README.md),
[findings.md](findings.md), [architecture.md](architecture.md),
[implementation.md](implementation.md), [validation.md](validation.md).

## Decision A — Device auth: signed-challenge JWT vs opaque hashed tokens

**Chosen: opaque random tokens, SHA-256-hashed at rest, short TTL (30 min),
rotated on refresh.**

| | Option A1 — JWT (server-signed, claims: device_id, tenant, exp) | Option A2 — opaque token, hashed server-side (chosen) |
|---|---|---|
| Revocation | Needs a denylist anyway (short-lived JWTs still outlive a revoke) — so the DB round-trip returns | Instant: token row + device status checked on every use |
| At-rest exposure | Signing key is a crown jewel; a leaked JWT is self-contained | DB leak exposes only hashes; a raw token exists once, in one response |
| Statelessness | Saves one DB read per request | E01 is a single service in front of the same Postgres; the "saved" read is the point of the accounting DB |
| Contract fit | `device-command-v1` says only "short-lived access token" — both fit | Same — both fit; opaque is the simpler honest reading |
| Complexity | Key management, alg pinning, clock skew, JWKS later | `rand(32)` + `sha256` + one indexed table |

Rationale: the contract requires short-lived rotating tokens whose refresh is
**proof of key possession** — the Ed25519 challenge already carries the
cryptography. Adding a second signed-artifact system (JWT) buys nothing but a
revocation gap and a signing-key liability. Revocation must be immediate
("Revocation … rejects token refresh"), which forces a DB check regardless.
Opaque + hashed is contract-compatible and the smallest correct thing.
Migration path: if a future multi-service deployment needs local validation,
introduce JWTs *derived from* an opaque session at that time — nothing in the
wire contract changes shape (the token stays an opaque string to clients).

Sub-decision (refresh proof transport): server-issued challenge (two-phase
refresh reusing `prove`) over client-signed timestamps. Signed timestamps
save a round trip but import clock-skew tolerance and a replay-window cache;
the challenge table already exists and is strictly replay-proof (single-use,
60 s TTL). One extra round trip per ~30 min per device is negligible.

## Decision B — Migrations tool

**Chosen: `golang-migrate/migrate` (library mode, embedded via `embed.FS`),
plain `NNNN_name.up.sql` / `.down.sql` files under
`xnobrain-enterprise/internal/store/migrations/`.**

Options:

1. **golang-migrate** (chosen) — de-facto standard; pure SQL files (satisfies
   the no-ORM rule *and* keeps migrations reviewable as SQL); library mode
   embeds migrations in the binary so the Docker image is self-contained;
   CLI available for ops. Sequential numeric prefixes match the "extend,
   never rewrite" rule in `docs/enterprise-extension.md`.
2. **goose** — comparable; allows Go-func migrations, which is a feature we
   explicitly do *not* want (SQL-only keeps the no-ORM discipline sharp).
3. **tern / atlas / dbmate** — fine tools; atlas is declarative-diff driven
   (schema-as-desired-state), which hides the migration history we are
   required to preserve verbatim. dbmate adds a runtime dependency shape
   similar to golang-migrate with a smaller community.
4. **Hand-rolled runner** (a `schema_migrations` table + sorted file loop) —
   ~100 lines, zero deps, but re-implements dirty-state detection and
   locking that golang-migrate already gets right (advisory locks prevent
   two replicas racing migrations — relevant the moment E03 scales the API).

Rationale: plain SQL files are non-negotiable (no ORM, reviewable diffs);
among SQL-file runners golang-migrate has the strongest Postgres advisory-
lock story and the least surprise. Verify the pinned version at
implementation time ("verify": latest v4.x).

## Decision C — Single binary vs multiple services

**Chosen: one modular binary (`cmd/enterprise-api`) now.**

Options:

1. **Single binary, modular internal packages** (chosen) — one deploy unit,
   one compose service, one health surface. Internal seams
   (`internal/httpapi` / `store` / `deviceauth` / `entitlements` /
   `authseam`) keep extraction cheap later.
2. **Separate services now** (identity-api, entitlements-api, admin-api) —
   premature: they would all share one Postgres and one release cadence;
   we'd pay service-mesh/config/observability tax with zero isolation
   benefit at E01 scale (fleet of laptops, not hyperscale).
3. **Modular monolith with multiple `cmd/` entrypoints sharing packages** —
   a reasonable future shape (e.g. a dedicated `cmd/ingest-worker` in E02+);
   nothing chosen now precludes it, because packages, not the binary, are
   the unit of structure.

Rationale: `docs/enterprise-extension.md` calls the enterprise repo "a thin
private composition". E02 (ingest) and E03 (orchestrator) may add
entrypoints; the store and auth packages are already shaped to be shared.
The rule that keeps this honest: **`internal/httpapi` never contains SQL,
`internal/store` never contains HTTP** — enforced in review and by a small
`go vet`-style grep in CI.

## Decision D — Admin auth before SSO

**Chosen: single static admin bearer token from `ADMIN_TOKEN` env, compared
constant-time; documented as a known risk with a forced migration point.**

Options:

1. **Static env token** (chosen) — trivially deployable (compose secret /
   env), no extra moving parts, honest about its maturity. Risks, stated
   plainly: one shared credential, no per-operator identity, no audit
   attribution beyond `actor_type='admin'`, rotation = redeploy. Mitigations
   in E01: constant-time comparison, admin endpoints are read-only except
   `revoke`, every admin call writes an `audit_events` row with the request
   id, token never logged, and `readyz` fails if `ADMIN_TOKEN` is unset in
   non-dev mode (prevents accidentally-open admin).
2. **Route admin auth through the external gRPC auth service now** — the
   auth service's protocol is an open question (findings.md §8.1); blocking
   the admin slice on it couples E01's critical path to an external
   unknown. Also admin ≠ end-user identity; role semantics don't exist yet.
3. **mTLS client certs for admin** — strong, but certificate issuance and
   distribution is real operational machinery, disproportionate to two
   read-only endpoints.
4. **Basic-auth user table in Postgres** — invents a second identity system
   we are explicitly told not to build ("call the existing external auth
   service … rather than migrating authentication code here").

Rationale: the admin surface in E01 is two list endpoints plus revoke; the
program's function 6 (RBAC/SSO) is the designated replacement. The static
token is a placeholder with a documented eviction date: when RBAC lands, the
`ADMIN_TOKEN` path is deleted, not layered over.

## Decision E — Module path and repo relationship

**Chosen: standalone private module `github.com/vn-fin/xnobrain-enterprise`
("verify" the org at repo creation); HTTP contracts only, zero Go imports of
OSS code.**

`docs/enterprise-extension.md` requires nesting under
`github.com/vn-fin/xnobrain/` **only if** the enterprise binary imports the
OSS repo's internal Go packages. The OSS repo is Python — there is nothing
to import, and the program frame says to prefer HTTP contracts regardless.
A standalone module keeps the private repo's release cadence independent and
makes the "OSS never depends on enterprise" direction structurally obvious.
If a shared Go SDK ever emerges, it would be a new public module, not an
import edge into either repo.

## Decision F — Where challenges live

**Chosen: `device_challenges` table in Postgres (single-use, 60 s TTL).**

Alternatives: in-memory map (lost on restart, breaks with >1 replica),
stateless HMAC-encoded challenge (no single-use guarantee without a replay
store — which is just the table again). Postgres rows are the only option
that is simultaneously replay-proof, restart-safe, and replica-safe, and the
volume (one row per enroll/refresh) is trivial.

## Decision G — ID scheme

**Chosen: prefixed ULIDs generated in Go (`dev_`, `ten_`, `usr_`, `tok_`,
`chl_`, `pas_`), text columns.** Matches the id style already visible in the
contracts (`cmd_01…`, `dev_01…`, `res_01…` in `device-command-v1`), sortable
by creation time, unambiguous in logs and audit rows. `audit_events` uses a
plain bigint identity instead — it is append-only, internal, and
sequence-ordered by definition.
