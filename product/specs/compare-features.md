# Brain4All — OSS vs Enterprise Feature Comparison

Side-by-side checklist of the OSS (open-core, self-hosted, single-user) and Enterprise
(multi-user, control-plane) editions. Specs: [`oss.md`](oss.md) · [`enterprise.md`](enterprise.md).

**Legend:** ✅ full · 🟡 limited / different · ⛔ not available · ➕ enterprise-only

---

## 1. Feature matrix

### Core agent experience (runs locally in both)
| Feature | OSS | Enterprise | Common / Different |
|---|:--:|:--:|---|
| Assistants (Hermes profiles, isolated `state.db`) | ✅ | ✅ | **Common** |
| Streaming chat & conversations (tabs, queue, retry) | ✅ | ✅ | **Common** |
| Model selection & blends (9router strategies) | ✅ | 🟡 | Enterprise adds an **org model allowlist** |
| Provider connections (multi-provider, multi-account) | ✅ | 🟡 | Enterprise: **org-managed keys**, read-only to member |
| Skills (library, per-agent enable) | ✅ | 🟡 | Enterprise adds **skill/plugin approval** policy |
| Workspace & files (browse, upload) | ✅ | ✅ | **Common** |
| Portable profiles (export/import `.zip`) | ✅ | ✅ | **Common** (+ managed backups in Enterprise) |
| MCP servers per assistant | ✅ | 🟡 | Enterprise adds **MCP/tool policy** |
| Localization & theming | ✅ | ✅ | **Common** |
| Local runtime works offline for chat | ✅ | ✅ | **Common — never restricted by enterprise** |

### Productivity surfaces
| Feature | OSS | Enterprise | Common / Different |
|---|:--:|:--:|---|
| Kanban — local per-user board | ✅ | ✅ | **Common** |
| Kanban — cross-account **Enterprise boards** | ⛔ | ➕ | **Enterprise-only** (assign across accounts/agents/teams) |
| Agent teams (multi-agent workflows) | ✅ | ✅ | **Common** (+ cross-account teams in Enterprise) |
| Scheduling / cron tasks | ✅ | ✅ | **Common** |
| Voice I/O (TTS/STT) | ⛔ | ➕ | **Enterprise-only** (central gateway, org keys, metered) |

### Usage, cost & accounting
| Feature | OSS | Enterprise | Common / Different |
|---|:--:|:--:|---|
| Usage analytics (per-agent, time range/bucket) | ✅ | ✅ | **Common** (local view in both) |
| Central usage database (all members) | ⛔ | ➕ | **Enterprise-only** — one PostgreSQL, exactly-once |
| Admin dashboards (org / team / member / model) | ⛔ | ➕ | **Enterprise-only** |
| Budgets | 🟡 | ✅ | OSS = **advisory only, never blocks**; Enterprise = **hard or advisory** |
| Chargeback / cost centers / invoice export | ⛔ | ➕ | **Enterprise-only** |
| CSV / scheduled usage export | ⛔ | ➕ | **Enterprise-only** |

### Identity, access & multi-user
| Feature | OSS | Enterprise | Common / Different |
|---|:--:|:--:|---|
| User accounts / sign-in | ⛔ | ✅ | OSS = **single local user, no sign-in** |
| Organizations & org-created accounts | ⛔ | ➕ | **Enterprise-only** |
| SSO (OIDC / SAML), LDAP, custom auth | ⛔ | ➕ | **Enterprise-only** |
| RBAC (config-file roles + assignments) | ⛔ | ➕ | **Enterprise-only** |
| Org-manager "view all" oversight | ⛔ | ➕ | **Enterprise-only** |
| SCIM provisioning / deprovisioning | ⛔ | ➕ | **Enterprise-only** |

### Fleet, deployment & operations
| Feature | OSS | Enterprise | Common / Different |
|---|:--:|:--:|---|
| Self-hosted deployment | ✅ | ✅ | **Common** |
| Managed cloud hosting | ⛔ | ➕ | **Enterprise-only** |
| Air-gapped deployment | ✅ | ✅ | **Common** (Enterprise: control plane w/ local auth) |
| Device / fleet inventory | ⛔ | ➕ | **Enterprise-only** |
| Provision Incus containers + register PCs | 🟡 | ➕ | OSS runs one local runtime; Enterprise manages a **fleet** |
| Remote lifecycle (restart / drain) | ⛔ | ➕ | **Enterprise-only** |
| Staged version rollout | ⛔ | ➕ | **Enterprise-only** |
| Backup / DR (managed) | 🟡 | ➕ | OSS = manual export; Enterprise = **managed, encrypted, HA/DR** |

### Governance, security & compliance
| Feature | OSS | Enterprise | Common / Different |
|---|:--:|:--:|---|
| Credentials confined (never logged/returned) | ✅ | ✅ | **Common** |
| Conversation content stays on the machine | ✅ | ✅ | **Common — absolute in both** |
| Model / capability policy (allowlists) | ⛔ | ➕ | **Enterprise-only** |
| Data retention & residency controls | ⛔ | ➕ | **Enterprise-only** |
| External secrets (Vault / KMS) | ⛔ | ➕ | **Enterprise-only** |
| Append-only audit log | ⛔ | ➕ | **Enterprise-only** |
| SIEM / log streaming | ⛔ | ➕ | **Enterprise-only (follow-on)** |
| Entitlements enforcement | ⛔ | ➕ | **Enterprise-only** |
| Compliance artifacts (SOC 2 / ISO / GDPR) | ⛔ | ➕ | **Enterprise-only** |

---

## 2. What's common to both editions

- ✅ The **entire local agent experience**: assistants, streaming chat, conversations,
  skills, workspace, MCP, teams, local Kanban, scheduling, model blends, provider
  connections, localization, theming.
- ✅ **Local-first execution** — every agent runs on the user's machine/container.
- ✅ **Usage analytics** as a local, on-demand view.
- ✅ **Privacy floor** — conversation content, prompts, responses, tool args and files
  never leave the machine; credentials are never logged or returned.
- ✅ **Self-hosted** deployment (incl. air-gapped).
- ✅ Built on the same OSS engines (Hermes Agent + 9router).

## 3. What's different (behavioral, not just add-ons)

| Aspect | OSS behavior | Enterprise behavior |
|---|---|---|
| **Users** | One local user, no sign-in | Orgs, org-created accounts, SSO, RBAC |
| **Provider keys** | User configures locally in 9router | **Org-managed**, read-only to members |
| **Budgets** | Advisory warnings, spend never blocked | Hard **or** advisory caps, enforced via entitlements |
| **Usage data** | Local `state.db` only | **Pushed** to one central PostgreSQL (metadata only) |
| **Kanban** | Per-user local board | Local board **plus** org-owned cross-account boards |
| **Models** | Any connected model | Constrained by **org allowlist** |
| **Hosting** | Self-hosted only | Self-hosted **or** managed cloud |
| **Backups** | Manual profile export | Managed, encrypted, with DR |

## 4. What's Enterprise-only (➕ net-new)

- ➕ Organizations, accounts, SSO/SAML/LDAP, config-file RBAC, org-manager oversight, SCIM.
- ➕ Central usage database, admin dashboards, chargeback, CSV/scheduled export.
- ➕ Fleet management: device inventory, Incus provisioning, remote lifecycle, staged rollout.
- ➕ Voice I/O via central gateway with org keys, metered.
- ➕ **Enterprise boards** — cross-account Kanban dispatched to members' runtimes.
- ➕ Governance: model/capability policy, retention/residency, external secrets.
- ➕ Audit log, SIEM streaming, entitlements enforcement, compliance program.
- ➕ Managed backup/DR, license & update management, managed cloud.

## 5. Non-negotiable guarantees (both editions)

- 🔒 **Enterprise never restricts local operation** — a control-plane outage or lost
  connection never blocks local chat or agents.
- 🔒 **Content never centralizes** — only counts and metadata cross the wire; prompts,
  responses, tool arguments, titles, files, and credentials stay on the member's machine.
- 🔒 **Push-only** — deployments connect outbound; nothing reaches inward to a member's
  machine except idempotent commands they chose to enroll for.
- 🔒 **Exactly-once accounting** — snapshot upserts make usage/task counts immune to
  double-counting.

---

*Editions map to the enterprise plan program: E01 control plane · E02 usage collection ·
E03 fleet · E04 voice · E05 orgs/auth/RBAC · E07 enterprise boards
(see `plans/enterprise/`).*
