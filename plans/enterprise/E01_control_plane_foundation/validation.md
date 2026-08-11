# E01 — Validation

Acceptance checklist. Each item names its evidence; the implementer pastes
the evidence line (test name + result, or command + output) when checking a
box. Cross-links: [README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [approaches.md](approaches.md),
[implementation.md](implementation.md).

All test paths are in the private repo `brain4all-enterprise` unless noted.

## 1. Skeleton and migrations

- [ ] Compose stack boots: `docker compose up` starts `postgres:16` and
      `enterprise-api`; `/healthz` returns 200 immediately, `/readyz`
      returns 200 only after DB is reachable **and** migrations are current.
      Evidence: compose logs excerpt + two `curl -i` outputs.
- [ ] Migrations are clean on PostgreSQL 16: from an empty database,
      `enterprise-api migrate up` succeeds; `migrate down` returns to empty
      (zero user tables); `up → down → up` leaves version identical to a
      single `up`. Evidence: `internal/store/migrate_test.go` run output
      (`go test ./internal/store/ -run TestMigrate -v` all PASS).
- [ ] The binary refuses to serve traffic when the schema version is behind
      (readyz stays 503). Evidence: test or manual demo with migrations
      intentionally not run.
- [ ] No ORM anywhere: `grep -rE 'gorm|sqlboiler|"entgo|xorm|bun\.' brain4all-enterprise/`
      returns nothing; all SQL lives under `internal/store/`. Evidence:
      grep output (empty) + `grep -rl 'SELECT\|INSERT\|UPDATE\|DELETE' --include='*.go' | grep -v internal/store` (empty).

## 2. Device identity — the fake-device sequence

- [ ] **Flagship flow passes**: `internal/httpapi/fakedevice_test.go` drives
      register → prove → refresh → claim → revoke against a real httptest
      server + real Postgres and every step asserts state
      (per [implementation.md](implementation.md) Phase 6). Evidence:
      `go test ./internal/httpapi/ -run TestFakeDeviceFullFlow -v` PASS.
- [ ] Register is idempotent per public key (same pubkey twice → same
      device, fresh challenge); a malformed (≠32-byte) pubkey is a 400.
      Evidence: device_test.go cases.
- [ ] Prove enforces the challenge contract: valid signature → `device_id` +
      token; wrong key → `401 invalid_signature`; reused challenge →
      `409 challenge_consumed`; expired challenge → `410 challenge_expired`.
      Evidence: device_test.go table rows.
- [ ] Concurrent double-consume of one challenge admits exactly one winner.
      Evidence: `internal/store/challenges_test.go` race test PASS
      (run with `-race`).
- [ ] Refresh rotates: after a successful refresh the previous token is
      rejected (`401`) and the new one works. Evidence: fake-device test
      step + `tokens_test.go`.
- [ ] Claim maps ownership without touching the key: after claim,
      `devices.public_key` is byte-identical, `status='claimed'`,
      `user_id`/`tenant_id` set; same-user re-claim is a 200 no-op;
      different-user claim is `409 already_claimed`. Evidence:
      claim_test.go PASS including the key-bytes assertion.
- [ ] First-ever principal bootstraps tenancy: resolving a new auth subject
      creates personal tenant + user row + default `free` assignment in one
      transaction. Evidence: `users_test.go` / claim_test.go.
- [ ] **Revoked device cannot refresh**: after revoke, refresh phase 1
      returns `403 device_revoked`, prove on any outstanding challenge
      returns `403`, all existing tokens fail lookup, and re-registering the
      same public key returns `403`. Evidence: fake-device test tail +
      device_test.go rows. (Contract: `docs/contracts/device-command-v1.md`
      — "Revocation … rejects token refresh".)

## 3. Token handling

- [ ] Tokens are hashed at rest: `device_tokens` contains only
      `sha256(raw)`; a direct SQL dump during the fake-device test finds no
      substring of the raw token in any table. Evidence: assertion inside
      fakedevice_test.go (scans a pg_dump of the test DB for the raw token —
      zero hits outside nothing).
- [ ] **Tokens never logged**: captured log output (slog handler → buffer)
      from the full flow contains no `b4e_dt_` prefix, no challenge nonce
      base64, no signature base64, no public/private key bytes, and no
      `Authorization` header values. Evidence: log-grep assertion in
      fakedevice_test.go PASS.
- [ ] TTLs enforced: an expired device token (clock-advanced or short-TTL
      config) is rejected on any authenticated endpoint. Evidence:
      tokens_test.go / middleware test.
- [ ] Admin/API responses never expose secrets: serialized JSON of
      `/admin/v1/devices` and `/admin/v1/tenants` contains no `public_key`,
      `token`, `hash`, or `nonce` fields. Evidence: admin_test.go raw-body
      assertions.

## 4. Entitlements

- [ ] `GET /entitlements/v1/current` returns a document with all fields
      required by `docs/contracts/entitlements-v1.md`: `version`, `edition`,
      `plan_id`, `subject`, `issued_at`, `expires_at`, `revision`, `limits`,
      `capabilities`, `hardware_class`. Evidence: entitlements_test.go
      schema assertion.
- [ ] **The document matches `docs/plans.md` for all four plans**: golden
      tests compare `free`, `pro`, `promax`, `enterprise` documents
      field-by-field against the matrix in [findings.md](findings.md) §3
      (e.g. free: agents 4, cron_runs_per_month 200, retention 7; promax:
      agents 100, cron_runs_per_day 1250, retention 180). Evidence:
      `internal/entitlements/document_test.go` golden tests PASS.
- [ ] **`-1` semantics verified**: the `enterprise` document serializes every
      custom/unlimited numeric as literal `-1` while
      `telemetry_retention_days` is `365`; a test also asserts JSON number
      (not string) typing. Evidence: golden test row.
- [ ] Anonymous (enrolled, unclaimed) device resolves plan `free`; a claimed
      device resolves its owner's assigned plan. Evidence:
      entitlements_test.go + fake-device flow step.
- [ ] Revision/ETag work: response carries `ETag`; `If-None-Match` with the
      current tag returns `304` with empty body; bumping `plans.revision`
      changes the tag. Evidence: entitlements_test.go.
- [ ] Unauthenticated request → `401`; the error envelope uses stable codes.
      Evidence: entitlements_test.go.
- [ ] `pkg/edition` compiles standalone (`go build ./pkg/...`) and imports
      no `internal/` package. Evidence: build output + import grep.

## 5. Admin surface

- [ ] `GET /admin/v1/devices` requires the admin bearer (401 otherwise,
      constant-time compare), lists devices with `status` and `last_seen_at`,
      supports `status=` filter and paging. Evidence: admin_test.go PASS.
- [ ] After the fake-device flow, the admin list shows the device
      progressing `enrolled → claimed → revoked` with a recent
      `last_seen_at`. Evidence: fake-device test assertions.
- [ ] `GET /admin/v1/tenants` lists the auto-created personal tenant with
      user/device counts and plan id. Evidence: admin_test.go.
- [ ] Every admin request and every identity transition writes an
      `audit_events` row; `UPDATE`/`DELETE` on `audit_events` raise the
      append-only trigger error. Evidence: audit_test.go PASS.

## 6. Boundaries (program rules)

- [ ] The public OSS repo is untouched by code: `git status` in
      `/home/kim/Documents/xno/brain4all-dev/brain4all` shows only the
      `plans/enterprise/E01_control_plane_foundation/` docs (this plan).
      Evidence: `git status` output.
- [ ] No new cross-repo contract was created; `docs/contracts/` in the
      public repo is unchanged (or carries only a reviewed clarifying edit,
      per [implementation.md](implementation.md) § public-repo
      deliverables). Evidence: `git diff --stat docs/contracts/`.
- [ ] The OSS runtime remains fully functional with `ENTERPRISE_API_URL`
      unset and with the enterprise stack down (nothing in this plan touched
      it — assert by running the OSS `make test` unchanged). Evidence:
      OSS `make test` PASS at the same commit.
- [ ] `go vet ./...` and `make check` are green in `brain4all-enterprise`.
      Evidence: command output.

## Definition-of-done restatement

E01 is done when a device on a laptop can enroll anonymously, be claimed by
a user, refresh tokens, and appear in the admin device list; the
entitlements endpoint serves the `docs/plans.md` matrix for all four plans
with correct `-1` semantics; revocation is terminal; secrets are hashed at
rest and absent from logs; and migrations cycle cleanly on PostgreSQL 16 —
each proven by the evidence lines above, not by inspection.
