# E05 — Validation

Acceptance checklist. Every line needs recorded evidence (test name, query
output, or capture file) — no line is checked by assertion. Maps to the
[README.md](README.md) definition of done plus the security items.
Cross-links: [findings.md](findings.md), [architecture.md](architecture.md),
[approaches.md](approaches.md), [implementation.md](implementation.md).

## 1. Definition-of-done scenario (self-hosted)

Run on a single-host compose stack (the same `docker-compose.yaml` the
managed cloud uses — fixed requirement 3), with `auth.yaml` defining the
built-in roles plus one custom role.

- [ ] **Boot from file.** Stack starts with `AUTH_CONFIG_PATH` pointing at
      a reviewed `auth.yaml`; `/readyz` reports the expected
      `config_hash`. Evidence: readyz body + file sha256sum match.
- [ ] **Org + invites.** The bootstrap admin (file `bindings`) creates the
      org and invites two members by email (one with roles `[member]`, one
      `[member]`); with `SMTP_URL` unset the API returns invite links
      (air-gapped mode). Evidence: API transcripts; `invitations` rows
      show `token_hash` only.
- [ ] **Mixed-backend sign-in.** Invitee 1 accepts and signs in via
      **OIDC** (fake IdP fixture or real Keycloak); invitee 2 accepts and
      signs in via **local password**. Both memberships become `active`.
      Evidence: `org_memberships` states + `auth_identities` rows on two
      different `backend_id`s.
- [ ] **Manager sees both.** A user granted `org_manager` calls
      `GET /admin/v1/usage` and receives both members' E02 usage
      (by_user contains both), plus `GET /orgs/v1/members` lists both with
      status/roles. Evidence: response bodies; numbers cross-checked
      against E02 rollups.
- [ ] **Member sees only self.** Each `member`'s `GET /admin/v1/usage` is
      server-forced to `user=self` — the other member's ids/totals appear
      nowhere in the response even when the query asks (`?user=<other>`
      ⇒ 403 or self-scoped, per the pinned behavior). Evidence: response
      diff.
- [ ] **Auditor reads the trail.** A user holding only `auditor` retrieves
      via `GET /orgs/v1/audit` the events for: both invites, both accepts,
      both logins, every role grant — each with `actor_id`, `org_id`,
      `request_id`; `GET /orgs/v1/audit/export` streams the same as
      JSONL. The same user gets 403 on `/admin/v1/usage` and
      `/orgs/v1/members` mutations. Evidence: export file + 403
      transcripts.

## 2. Definition-of-done scenario (managed cloud / standalone)

- [ ] **Standalone user works without any org.** A fresh user self-signs
      up (personal tenant auto-created per E01 bootstrap), claims a
      device, sees their own usage and entitlement document — with **zero
      org rows in the database**. Evidence: `orgs` count unchanged; user's
      `GET /auth/v1/session` shows `org: null`; entitlements doc subject
      has personal `tenant_id`.
- [ ] **Org tenants refuse self-signup.** No API path lets an
      unauthenticated or uninvited user create a membership in an existing
      org (OIDC JIT creates the user + personal tenant but attaches no org
      membership without a matching invite/provisioned row). Evidence:
      Phase-7 JIT test.
- [ ] **Capability gating.** A `free`-plan personal tenant calling an
      RBAC/audit-export surface gets `403 capability_unavailable`; the
      `enterprise`-plan org tenant succeeds (entitlements-v1 capabilities
      `rbac`/`sso`/`audit_export`). Evidence: paired transcripts.

## 3. RBAC engine correctness

- [ ] **Catalog matrix test green.** The Phase-4 table test covering every
      (built-in role × catalog permission) pair against the documented
      matrix in [architecture.md](architecture.md) §3. Evidence: test run.
- [ ] **Org isolation.** A manager of org A calling every org-scoped
      endpoint with org B identifiers (query params, path ids, forced
      headers) gets 403/404 and zero org-B data bytes. Evidence: the
      isolation test iterates the full endpoint table.
- [ ] **Role change without restart.** Editing `auth.yaml` (add a
      permission to the custom role) + SIGHUP (or
      `POST /admin/v1/authcfg/reload`) changes the holder's next request
      from 403 to 200 with **no process restart** (the reload decision,
      [approaches.md](approaches.md) A). A DB role grant/revoke likewise
      takes effect on the next request with no new login. Evidence: before
      /after transcripts + unchanged process PID.
- [ ] **Group mapping is live.** Removing the user's IdP group (fake IdP)
      flips org-manager access to 403 at the next token refresh at the
      latest; the pinned bound is documented and met. Evidence: Phase-7
      test.

## 4. Security evidence

- [ ] **argon2id verified.** `local_credentials.password_hash` rows all
      match `$argon2id$v=19$…` with parameters ≥ the configured policy;
      a legacy/weak-param hash is transparently rehashed on successful
      login. Evidence: SQL check + rehash test.
- [ ] **Tokens hashed at rest, never logged.** `user_sessions`,
      `invitations`, and `scim_tokens` contain only sha256 hashes; a test
      captures all log output across the full login/refresh/invite/SCIM
      flows and greps for `b4e_at_`, `b4e_rt_`, invite-token, and
      SCIM-token prefixes plus raw passwords — zero hits (E01 discipline).
      Evidence: the never-logged test.
- [ ] **Suspension cuts access within the access-token TTL.** Suspending a
      member kills their live session on the **next request** (server-side
      revocation, [findings.md](findings.md) risk 5) — measured cutoff ≤
      access TTL (15 min) in all cases, ≈ immediate in practice; refresh
      also fails; deprovision additionally revokes device claims (device
      token refresh fails). Evidence: timed test.
- [ ] **Every auth/role/policy mutation lands in `audit_events` with an
      actor.** The Phase-6 lifecycle test asserts one row per action from
      the vocabulary in [architecture.md](architecture.md) §7, each with
      non-empty `actor_type`/`actor_id` and correct `org_id`; the
      append-only trigger still rejects UPDATE/DELETE. Evidence: test +
      trigger probe.
- [ ] **`ADMIN_TOKEN` is gone.** Repo-wide grep in `brain4all-enterprise`
      finds no `ADMIN_TOKEN`; every former static-token endpoint returns
      401 without a session and enforces its catalog permission
      ([findings.md](findings.md) §1.2 table). Evidence: grep output +
      Phase-5 matrix test.
- [ ] **Break-glass works and is loud.** With `auth.yaml` deliberately
      broken (no reachable `org.manage`), `enterprise-api break-glass`
      mints a working 15-minute recovery session, the fix is applied, and
      the `breakglass.used` audit row is visible to `auditor`. Evidence:
      scripted lockout drill transcript.

## 5. Lifecycle & SCIM

- [ ] **Invite lifecycle.** invited → accept → active → suspend →
      reactivate → deprovision walk passes with correct state gates
      (deprovisioned is terminal; illegal transitions rejected). Evidence:
      Phase-6 test.
- [ ] **Invite token hygiene.** Single-use (second accept ⇒ 409), expiry
      (410), revocation, email binding. Evidence: Phase-6 token tests.
- [ ] **SCIM lifecycle test green.** POST(active) → PATCH(active:false) →
      PATCH(active:true) → DELETE maps to the internal lifecycle exactly
      per the [findings.md](findings.md) §6 table, with session/device
      revocation on suspend/delete and audit rows throughout; wrong-org
      SCIM token is isolated. Evidence: Phase-9 test.
- [ ] **CSV import.** A 3-line CSV produces three invitations with the
      requested roles; malformed lines are reported, not silently
      dropped. Evidence: Phase-6 test.

## 6. Deployment / air-gapped / OSS

- [ ] **Air-gapped: zero external egress.** Run the full self-hosted
      scenario (§1) with backends `local` only, `SMTP_URL` unset, on a
      network-namespaced host with packet capture (tcpdump/nftables
      counters) on all non-loopback, non-cluster interfaces: **zero
      packets** leave the deployment for the entire run. Repeat with
      `ldap` against an in-network directory. Evidence: capture summary
      attached to the plan.
- [ ] **Same artifacts both modes.** The image digest + compose file used
      for the self-hosted run and the cloud-mode run are identical; only
      `auth.yaml` and env differ. Evidence: digest comparison.
- [ ] **OSS dormancy.** With `ENTERPRISE_API_URL` unset: no enterprise
      auth routes do network work, `/api/brain/v1/limits` returns the original
      static payload, no new background tasks. With it set but the server
      down: local features and latency unchanged (`AGENTS.md`), limits
      falls back to the static payload, login fails gracefully. Evidence:
      `brain4all/tests/test_enterprise_auth.py` + latency comparison.
- [ ] **OSS limits payload.** Signed in, `/api/brain/v1/limits` carries
      `org` + `capabilities` per `docs/contracts/enterprise-auth-v1.md`
      and still `local_features_unlimited: true`; the Enterprise settings
      tab shows the signed-in card. Evidence: API transcript + UI
      screenshot; `npm run build` clean.
- [ ] **HA drift visibility.** Two replicas with mismatched `auth.yaml`
      hashes are detectable from `/readyz` and from consecutive
      `authcfg.reloaded` audit rows ([findings.md](findings.md) risk 1).
      Evidence: two-replica drill transcript.

## Sign-off

- [ ] All Go tests green in `brain4all-enterprise` CI (incl. dockerized
      Postgres suites); `make check` green in this repo.
- [ ] `docs/contracts/enterprise-auth-v1.md` published and matched by both
      sides; `docs/authcfg-schema.md` + `config/auth.yaml.example`
      byte-consistent with [architecture.md](architecture.md) §2.
- [ ] Program README checklist updated: E05 accepted with links to the
      evidence above.
