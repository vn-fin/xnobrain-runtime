# Brain4All — Feature Comparison: OSS Free · OSS Pro · Enterprise

Side-by-side checklist of the three editions. **Free and Pro are the same self-hosted OSS
build**; Enterprise is that build plus a control plane for businesses.
Specs: [`oss.md`](oss.md) · [`enterprise.md`](enterprise.md) · packaging: [`plans.md`](plans.md).

**Legend:** ✅ full · 🟡 limited / different · ⛔ not available · ➕ tier-defining add

> **This is a *capability* matrix.** Resource counts (agents, provider connections,
> cron run-minutes, retention windows) are set by **deployment**, not edition:
> self-hosted is unlimited, Brain4All Cloud is sized by plan. See [`plans.md`](plans.md)
> §7 for the cloud limits.

---

## 1. Feature matrix

### Core agent experience (runs locally in all three)
| Feature | Free | Pro | Enterprise | Common / Different |
|---|:--:|:--:|:--:|---|
| Assistants — create / configure / update / delete | ✅ | ✅ | ✅ | **Common** |
| Streaming chat & conversations per agent (tabs, queue, retry) | ✅ | ✅ | ✅ | **Common** |
| Model selection & combos/blends (9router strategies) | ✅ | ✅ | 🟡 | Enterprise adds an **org model allowlist** |
| Provider connections — all providers | ✅ | ✅ | 🟡 | **No tier limit on count or type.** Enterprise: **org-managed keys**, read-only to member |
| Multiple accounts per provider (priority, rotation) | ✅ | ✅ | ✅ | **Common — identical in all tiers** |
| Built-in system skills (bundled library) | ✅ | ✅ | ✅ | **Common** |
| Workspace & files (browse, upload) | ✅ | ✅ | ✅ | **Common** |
| Portable profiles (export/import `.zip`) | ✅ | ✅ | ✅ | **Common** (+ managed backups in Enterprise) |
| MCP servers per assistant | ✅ | ✅ | 🟡 | Enterprise adds **MCP/tool policy** |
| Localization & theming | ✅ | ✅ | ✅ | **Common** |
| Works offline, signed out | ✅ | ✅ | ✅ | **Common — never restricted by plan or control plane** |

### Productivity surfaces
| Feature | Free | Pro | Enterprise | Common / Different |
|---|:--:|:--:|:--:|---|
| Kanban — local per-user board | ✅ | ✅ | ✅ | **Common** |
| Kanban — cross-account **Enterprise boards** | ⛔ | ⛔ | ➕ | **Enterprise-only** (assign across accounts/agents/teams) |
| Agent teams (multi-agent workflows) | ✅ | ✅ | ✅ | **Common — a standalone feature, not a business one** |
| Agent teams **spanning accounts** | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Message channels per agent (Telegram/Discord/Slack/WhatsApp/Signal) | ✅ | ✅ | 🟡 | **Common**; Enterprise adds **org channel policy/approval** |
| Cron / scheduling + delivery targets | ✅ | ✅ | ✅ | **Common** |
| **Skill marketplace — install** | ⛔ | ➕ | ✅ | **Pro delta** — Free uses built-in skills only |
| **Skill marketplace — publish + revenue share** | ⛔ | ➕ | ✅ | **Pro delta** |
| Private org skill catalog + approval | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| **Speech-to-text** (composer + channel voice notes) | ⛔ | ➕ | ✅ | **Pro delta**; Enterprise routes it through the org gateway |
| Text-to-speech / read-aloud | ⛔ | 🟡 | ✅ | Same voice surface; org keys & metering in Enterprise |
| Local snapshot before risky writes | ✅ | ✅ | ✅ | **Common** |
| **Skill & memory snapshot versions** (history, diff, restore, new machine) | ⛔ | ➕ | ✅ | **Pro delta** — hosted, client-side encrypted |
| Snapshot retention policy + admin key escrow | ⛔ | ⛔ | ➕ | **Enterprise-only** — recover a departed member's agent |

### Usage, cost & accounting
| Feature | Free | Pro | Enterprise | Common / Different |
|---|:--:|:--:|:--:|---|
| Usage analytics (per-agent, time range/bucket) | ✅ | ✅ | ✅ | **Common** (local view in all three) |
| Central usage database (all members) | ⛔ | ⛔ | ➕ | **Enterprise-only** — one PostgreSQL, exactly-once |
| Admin dashboards (org / team / member / model) | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Budgets | 🟡 | 🟡 | ✅ | Free/Pro = **advisory only, never blocks**; Enterprise = **hard or advisory** |
| Chargeback / cost centers / invoice export | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| CSV / scheduled usage export | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Telemetry retention (managed) | 7 days | 90 days | 365 days | Storage cost, not a capability |

### Identity, access & multi-user
| Feature | Free | Pro | Enterprise | Common / Different |
|---|:--:|:--:|:--:|---|
| Sign-in / account | Optional | Required (subscription) | Required (org seat) | Free is fully usable signed out |
| Organizations & org-created accounts | ⛔ | ⛔ | ➕ | **Enterprise-only** — personal tenants have **no invite action** |
| SSO (OIDC / SAML), LDAP, custom auth | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| RBAC (config-file roles + assignments) | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Org-manager "view all" oversight | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| SCIM provisioning / deprovisioning | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Shared workspaces between people | ⛔ | ⛔ | ➕ | **Enterprise-only** |

### Fleet, deployment & operations
| Feature | Free | Pro | Enterprise | Common / Different |
|---|:--:|:--:|:--:|---|
| Self-hosted deployment | ✅ | ✅ | ✅ | **Common — unlimited resources, your hardware** |
| Brain4All Cloud (our servers) | 🟡 trial | ✅ Pro / Pro Max | ✅ contracted | No permanent free container; cloud is resource-tiered |
| Air-gapped deployment | ✅ | 🟡 | ✅ | Pro's three paid features need connectivity; §2 does not |
| Device / fleet inventory | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Provision Incus containers + register PCs | 🟡 | 🟡 | ➕ | OSS runs one local runtime; Enterprise manages a **fleet** |
| Remote lifecycle (restart / drain) | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Staged version rollout | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Backup / DR (managed) | 🟡 | 🟡 | ➕ | OSS = manual export; Enterprise = **managed, encrypted, HA/DR** |

### Governance, security & compliance
| Feature | Free | Pro | Enterprise | Common / Different |
|---|:--:|:--:|:--:|---|
| Credentials confined (never logged/returned) | ✅ | ✅ | ✅ | **Common** |
| Conversation content stays on the machine | ✅ | ✅ | ✅ | **Common — absolute in all three** |
| Imported-code quarantine + scan gate | ✅ | ✅ | ✅ | **Common** (applies to marketplace installs too) |
| Model / capability policy (allowlists) | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Skill / plugin approval for the fleet | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Data retention & residency controls | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| External secrets (Vault / KMS) | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| Append-only audit log | ⛔ | ⛔ | ➕ | **Enterprise-only** |
| SIEM / log streaming | ⛔ | ⛔ | ➕ | **Enterprise-only (follow-on)** |
| Entitlements enforcement | ⛔ | 🟡 | ➕ | Pro: the four paid flags only; Enterprise: full org entitlements |
| Compliance artifacts (SOC 2 / ISO / GDPR) | ⛔ | ⛔ | ➕ | **Enterprise-only** |

### Support & billing
| | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Support | Community | Priority | Dedicated |
| Tenant type | Personal (1 seat) | Personal (1 seat) | Organization (many seats) |
| Billing | Free | Per-user subscription | Per-seat contract |

---

## 2. What's common to all three tiers

- ✅ The **entire standalone agent experience**: assistants, conversations, built-in
  skills, workspace, MCP, **agent teams**, local Kanban, **message channels**,
  **cron/scheduling**, model combos, **provider connections with multiple accounts each**,
  usage analytics, localization, theming.
- ✅ **No metering on your own hardware** — none of the above is counted or capped by
  edition when self-hosted. Free is not a crippled runtime; it is the full runtime.
  (Cloud sizes these quantities by plan — a hosting cost, never a hidden capability.)
- ✅ **Local-first execution** — every agent runs on the user's machine/container.
- ✅ **Privacy floor** — conversation content, prompts, responses, tool args and files
  never leave the machine; credentials and channel tokens are never logged or returned.
- ✅ **Self-hosted** deployment (incl. air-gapped for §2 capabilities).
- ✅ Built on the same OSS engines (Hermes Agent + 9router) — same binary for Free and Pro.

## 3. What separates Free from Pro (exactly three things)

| | Free | Pro |
|---|---|---|
| **Skills** | Built-in system library only. Can browse the marketplace, cannot install or publish. | Install marketplace skills (free + premium) and publish your own with revenue share. |
| **Speech-to-text** | ⛔ Entitlement-gated (403). | ✅ Dictation in the composer and transcription of channel voice notes. |
| **Skill & memory snapshot versions** | Local snapshots + manual export only; no retained history. | ✅ Hosted, client-side-encrypted version history: diff, roll back, restore onto a new machine. |

Nothing else. Same agents, same providers, same accounts per provider, same teams, same
boards, same channels, same cron, same usage. Both are single-user and cannot add members.

All three deltas share one property: each is a **Brain4All-operated service with a real
marginal cost** — a catalog, transcription compute, stored bytes. Nothing is withheld from
Free that costs us nothing to provide.

## 4. What separates Enterprise (the business layer)

**Enterprise = Pro + this layer.** Every seat includes the complete Pro product; the rows
below are additions, not substitutions. Where a row looks like a change (provider keys,
voice keys, snapshot retention), Enterprise is adding *org control over* a Pro capability,
never removing it.

| Aspect | Free / Pro behavior | Enterprise behavior |
|---|---|---|
| **Users** | One person, personal tenant, no invite action | Orgs, org-created accounts, SSO, RBAC, SCIM |
| **Provider keys** | User configures locally in 9router | **Org-managed**, read-only to members |
| **Budgets** | Advisory warnings, spend never blocked | Hard **or** advisory caps, enforced via entitlements |
| **Usage data** | Local `state.db` only | **Pushed** to one central PostgreSQL (metadata only) |
| **Kanban** | Per-user local board | Local board **plus** org-owned cross-account boards |
| **Agent teams** | Agents within one install | Teams spanning members' runtimes |
| **Skills** | Public marketplace (Pro) | + private org catalog with approval workflow |
| **Voice** | Per-user STT, own or Brain4All key (Pro) | Central gateway, org-held keys, minutes metered |
| **Snapshots** | Per-user history, user-held key (Pro) | + org retention policy and admin **key escrow** |
| **Models** | Any connected model | Constrained by **org allowlist** |
| **Hosting** | Self-hosted only | Self-hosted **or** managed cloud |
| **Backups** | Manual profile export | Managed, encrypted, with DR |

### Enterprise-only (➕ net-new)
- ➕ Organizations, accounts, SSO/SAML/LDAP, config-file RBAC, org-manager oversight, SCIM.
- ➕ Central usage database, admin dashboards, enforced budgets, chargeback, CSV/scheduled export.
- ➕ **Enterprise boards** — cross-account Kanban dispatched to members' runtimes.
- ➕ Cross-account agent teams.
- ➕ Fleet management: device inventory, Incus provisioning, remote lifecycle, staged rollout.
- ➕ Managed voice gateway with org keys, metered.
- ➕ Private org skill catalog and approval workflow.
- ➕ Snapshot retention policy and admin key escrow for departed-member recovery.
- ➕ Governance: model/capability policy, tool & MCP policy, retention/residency, external secrets.
- ➕ Audit log, SIEM streaming, org entitlements enforcement, compliance program.
- ➕ Managed backup/DR, license & update management, managed cloud.

## 5. Non-negotiable guarantees (all tiers)

- 🔒 **A plan never restricts local operation** — being on Free, being signed out, or
  losing the control plane never blocks local chat, agents, teams, channels, or cron.
- 🔒 **Cloud quotas size resources, never capabilities** — a cloud plan may limit *how
  many* agents or *how many* run-minutes, but never removes a feature the edition grants.
- 🔒 **Exceeding a cloud quota never destroys data** — over-limit objects are paused or
  made read-only, never deleted; the user chooses what to keep.
- 🔒 **Content never centralizes** — only counts and metadata cross the wire; prompts,
  responses, tool arguments, titles, files, and credentials stay on the user's machine.
  The sole exception is an opt-in skill/memory snapshot, which is **encrypted client-side
  with a key we never hold** — we store ciphertext, not content.
- 🔒 **Push-only** — deployments connect outbound; nothing reaches inward to a member's
  machine except idempotent commands they chose to enroll for.
- 🔒 **Exactly-once accounting** — snapshot upserts make usage/task counts immune to
  double-counting.

---

*Enterprise features map to the enterprise plan program: E01 control plane · E02 usage
collection · E03 fleet · E04 voice gateway · E05 orgs/auth/RBAC · E07 enterprise boards
(see `plans/enterprise/`). Standalone features map to the local program: 005 messaging
channels · 006 voice/STT · 008 cron delivery · 009 usage analytics · 010 team runs ·
011 provider connections · 012 model blends (see `plans/LOCAL_FEATURES_CHECKLIST.md`).*
