# XNOBrain — Enterprise System Specification

**Edition:** Enterprise (commercial, for businesses), multi-user, self-hosted **or** managed cloud
**Builds on:** the OSS edition ([`oss.md`](oss.md)) at the **Pro** level — unchanged local runtime
**Status:** planned — see plan packages `plans/enterprise/E01`–`E07`

---

## 1. System description

XNOBrain Enterprise is a **multi-user, organization-managed** edition built as a
separate **control plane** on top of the OSS product. Each member still runs the full
OSS runtime on their own machine or container; the control plane sits *above* those
runtimes to provide identity, central visibility, governance, fleet management, voice,
and cross-account coordination.

> **Enterprise = Pro + the business layer.**

**Every member gets the complete Pro product** — every standalone capability in
[`oss.md`](oss.md) §2 (assistants, conversations, combos, provider connections with
multiple accounts, agent teams, Kanban, message channels, cron, usage) plus all three Pro
capabilities ([`oss.md`](oss.md) §3: skill marketplace, speech-to-text, skill/memory
snapshot versions). Enterprise **only adds**; it never removes or downgrades a capability
a Pro user has. Where a Pro capability appears to change under Enterprise — provider keys,
voice keys, snapshot retention — the org is gaining *control over* it, not taking it away.

What Enterprise adds is the layer a *business* needs and an individual does not: other
people, oversight of them, and control over what they can do.

- **Control plane:** a Go service backed by **PostgreSQL** (the one billing-grade
  database). Runs **self-hosted** on the firm's servers — including **air-gapped** — or
  as **Enterprise Cloud** on XNOBrain's servers, where member runtimes are managed Incus
  containers with contracted resources ([`plans.md`](plans.md) §7.3).
- **Deployments (members' runtimes):** unchanged OSS instances, enrolled to the control
  plane by an outbound-only device identity. They **push** metadata to the center; the
  center never needs an inbound connection (NAT-safe).
- **Privacy boundary:** the control plane stores metadata and counts — tokens, cost,
  task/session status, device health — but not plaintext conversation content.
  Cross-account collaboration and hosted snapshots may cross the wire only as
  client-side ciphertext under the rules in §2.8 and §2.12.

### Governing invariants
- **Enterprise never restricts local operation.** A control-plane outage or lost
  connection never blocks a member's local chat or agents.
- **Push, not pull.** Every deployment connects *outbound* over TLS; the frozen
  `device-command-v1` contract carries commands and `usage-ingest-v1` carries metadata.
- **Exactly-once accounting.** Cumulative **snapshot upserts** (at-least-once delivery +
  idempotent write) make usage and task status immune to double-counting across retries
  and offline catch-up.
- **Credentials stay central or local, never in between.** Org provider keys live in the
  org's vault / central store; member machines never receive them.

---

## 2. Functional specification — what Enterprise adds

Each subsection is **additive** over the Pro product. Nothing here changes how a member's
local runtime behaves.

### 2.1 Organizations, accounts & tenancy  *(E05)*
- Provision accounts **by the organization** (email invite, CSV import, SCIM) — open
  self-signup is disabled for org tenants.
- Model tenancy: on cloud, a user either **belongs to an organization** or stands alone
  in a **personal tenant**; on self-hosted, all users belong to the org.
- Account lifecycle: invite → accept → active → suspended → deprovisioned (SCIM maps to
  the same lifecycle).
- Org-manager oversight: a built-in **`org_manager`** role can **view all** members'
  usage, devices, and dashboards without member-management power.

### 2.2 Authentication & RBAC  *(E05)*
- Pluggable auth backends per deployment: **local accounts** (argon2id), **OIDC**,
  **SAML 2.0**, **LDAP**, or a **custom external auth service**.
- **Config-file RBAC:** roles and permissions declared in a versioned `auth.yaml`
  (role *shapes* in file, per-user *assignments* in DB); hot-reloadable.
- Built-in roles: `org_admin` (manage members/roles/policies), `org_manager`
  (read-all oversight), `member` (self-scope), `auditor` (audit read).
- IdP group → role mapping; SCIM provisioning/deprovisioning; session policies
  (MFA via IdP, token TTLs); opaque, hashed, revocable session tokens.
- Break-glass local admin for lockout recovery; air-gapped operation with local/LDAP
  backends (zero external egress).

### 2.3 Central usage collection  *(E02 — flagship)*
- Each member's runtime reads its own local `state.db` (read-only) and **pushes
  session snapshots** (tokens, cost, model, counts) to the control plane.
- The center stores them as **idempotent upserts** into one PostgreSQL — the single
  source of truth for chats and tokens across all users.
- Offline outbox: snapshots spool locally and reconcile on reconnect; the server acks a
  watermark before the client advances its cursor.
- Admin dashboards (Grafana-style, same controls as local analytics): scope to all or
  selected members/agents, pick range/bucket, group by member/team/model/device.
- CSV / scheduled export for billing.

### 2.4 Budgets, chargeback & entitlements  *(E02 + E01)*
- Per-user / team / org budgets with **hard or advisory** caps (unlike OSS's
  advisory-only), cost-center tags, and invoice/chargeback export.
- Entitlement checks (Check/Reserve/Commit/Release, `-1` = unlimited) gate metered
  capabilities; telemetry is never used as accounting state.

### 2.5 Fleet management  *(E03)*
- Provision **per-user Incus containers** (pre-enrolled at boot, persistent data volume)
  and register **PC-installed** runtimes in the same fleet.
- Device inventory: identity, owner, kind (container/PC), status, version, heartbeats.
- Remote lifecycle over the outbound command channel: restart, drain, drain-then-replace.
- **Staged version rollout** (canary → stable) with compatibility gates against the
  pinned OSS runtime.

### 2.6 Managed voice gateway  *(E04)*
- Speech-to-text itself is a **Pro** capability that ships in the OSS runtime
  ([`oss.md`](oss.md) §3.2). Enterprise does not add the feature — it adds **who holds
  the keys and who pays**.
- Central **voice gateway** holding **org-managed** STT/TTS provider keys — members never
  see, configure, or receive them; no key distribution to endpoints.
- The same voice UI as Pro, entitlement-gated per member by the org.
- Voice minutes metered into the same central usage database, chargeable to a cost center.

### 2.7 Enterprise boards — cross-account Kanban  *(E07 — new)*
- Org-owned boards whose tasks can be assigned across **accounts, agents, and
  agent-teams** (unlike OSS's per-user board with fixed stages).
- An authorized board manager can define stage names, order, colors, allowed
  transitions, stage-level permissions, approval gates, work-in-progress limits, and
  automation rules. The product ships a simple default template.
- Deleting a stage requires an explicit destination for its open tasks. Published stage
  changes are versioned and audited.
- Admin creates boards and assigns tasks; the control plane **dispatches** each task to
  the assignee's runtime (`board.task.dispatch` over device-command-v1), which
  materializes it into the target agent's **local** board and runs it locally.
- Status/progress flow back as idempotent snapshots; the admin watches all assignees
  move through the board's configured stages with "runs on <member>·<device>"
  provenance.
- Offline-safe (spooled dispatch + status); RBAC-gated (`boards.assign`); **no
  conversation content is centralized** — task titles/descriptions are admin-authored
  board metadata only.

### 2.8 Cross-account agent teams  *(program)*
- Agent teams themselves ship in Free ([`oss.md`](oss.md) §2.9) — one orchestrator and
  workers **inside one install**. Enterprise adds teams whose workers live on **different
  members' runtimes**.
- The orchestrator dispatches each step over the same outbound `device-command-v1`
  channel used by enterprise boards; each worker runs locally on its owner's machine.
- Cross-account task inputs, attachments, and results are end-to-end encrypted for the
  enrolled sender and recipient runtimes. The control plane may relay ciphertext and
  store delivery metadata, but cannot read collaboration content.
- Run history, per-step status, and token/cost roll up to the org dashboards with
  "ran on <member>·<device>" provenance.
- RBAC-gated: composing a cross-account team requires an explicit permission; a member's
  runtime only accepts steps for teams its owner is enrolled in.
- Every dispatch has a signed identity, content hash, expiration, replay key,
  cancellation state, and size limit. Offline delivery is retried idempotently and
  expired work is never started.
- Before implementation, freeze a `collaboration-payload-v1` contract for the encrypted
  inner payload. `device-command-v1` remains the delivery envelope and must not be
  reinterpreted incompatibly.

### 2.9 Private skill catalog  *(program)*
- An **org-internal marketplace** alongside (or instead of) the public one: skills
  authored inside the company, never published externally.
- **Approval workflow:** a skill — public-marketplace or internal — must be approved by an
  admin before any member's runtime may install it; approvals are content-hash-bound and
  logged to the audit trail.
- Org policy can restrict members to the private catalog only, or to an allowlist of
  public listings.

### 2.10 Governance & policy  *(program)*
- **Model & capability policy:** org allowlists of models/providers/blends; tool and
  MCP-server policy; skill/plugin approval for the fleet (§2.9).
- **Message-channel policy:** which channels members may connect an agent to, and whether
  channel credentials are member-supplied or org-supplied.
- **Data retention & residency:** org-set retention for centrally held metadata; region
  pinning on cloud.
- **External secrets:** org keys held in the org's Vault/KMS for firms that require it.

### 2.11 Audit & compliance  *(E01/E05 + program)*
- **Append-only audit log** of every login, role change, device op, policy change, and
  export — with actor, org scope, and export endpoint.
- SIEM/log streaming (follow-on); evidence exports.
- Compliance program artifacts (SOC 2 / ISO 27001 / GDPR DPA) as an organizational
  workstream.

### 2.12 Snapshot governance  *(program)*
Skill & memory snapshot versioning is a **Pro** capability ([`oss.md`](oss.md) §3.3) and
every seat has it. Enterprise adds the org's stake in it:

- **Org retention policy** — minimum and maximum retained versions per agent, and a
  per-agent storage allowance drawn from an org pool rather than a personal one.
- **Admin key escrow** — snapshots stay client-side encrypted, but the data key is
  additionally wrapped to an **org escrow key** held in the org's Vault/KMS. This is the
  one case a user-held key cannot serve: recovering the agents of a member who has left,
  been deprovisioned, or lost their key.
- Escrow is **visible, not silent** — members are shown that org escrow is enabled, and
  every escrow-key use is written to the audit log with actor and justification.
- **Restore across seats** — an admin can restore a departed member's agent onto another
  member's runtime, subject to `snapshots.restore_other` RBAC permission.
- Snapshot events (created, restored, aged out, escrow-unwrapped) flow to the audit log;
  snapshot bytes count toward the org's storage line, never toward chargeback for tokens.

### 2.13 Backup, DR & licensing  *(program)*
- Managed, encrypted profile-bundle backups (portable-bundle format) and control-plane
  HA + DR runbooks.
- Fleet license/update management; offline license keys for air-gapped installs.

---

## 3. Non-functional requirements

- **Deployment:** self-hosted (incl. air-gapped) **or** managed cloud; same binary/compose.
- **Tenancy:** multi-user organizations; personal tenants on cloud; strict org isolation.
- **Database:** one PostgreSQL as the billing-grade source of truth; ClickHouse for
  high-volume trace telemetry only.
- **Connectivity:** deployments connect outbound-only (TLS 443, NAT-safe); no inbound to
  member machines.
- **Availability:** control-plane outage never degrades local runtimes.
- **Privacy:** the control plane receives metadata/counts and may relay encrypted
  collaboration payloads. Conversation content, prompts, responses, tool args, titles,
  files, and credentials are never stored there as plaintext. Skill/memory snapshots
  (§2.12) and cross-account collaboration payloads (§2.8) leave a runtime only as
  client-side ciphertext. Snapshot ciphertext can be decrypted only through an explicit,
  audited org-escrow operation; collaboration ciphertext is readable only by enrolled
  sender and recipient runtimes.
- **Security:** Ed25519 device identity; hashed/rotating tokens; credentials in
  vault/9router, never logged or returned.
- **Accounting integrity:** exactly-once via snapshot upserts; idempotent commands.

---

## 4. Relationship to OSS Free and Pro

Everything in the OSS spec ([`oss.md`](oss.md)) — §2 standalone capabilities **and** §3
Pro capabilities — remains available to each member, unchanged and locally executed.
Enterprise is strictly **additive**: it never removes a local capability, only adds the
organization layer.

| A member's capability | Where it comes from |
|---|---|
| Assistants, conversations, combos, providers & multi-account, agent teams, Kanban, message channels, cron, usage, MCP, workspace | OSS §2 — same in Free |
| Skill marketplace (install + publish), speech-to-text, skill/memory snapshot versions | OSS §3 — the Pro delta, included for every seat |
| Orgs, SSO/RBAC/SCIM, central usage, enforced budgets, enterprise boards, cross-account teams, fleet, voice gateway, private catalog, snapshot escrow, governance, audit, backup/DR, managed cloud | Enterprise §2 — the business layer |

The dividing line is **people, not power**: an individual on Pro is not missing agent
features, they are missing colleagues. Full side-by-side checklist:
[`compare-features.md`](compare-features.md) · packaging and enforcement:
[`plans.md`](plans.md).
