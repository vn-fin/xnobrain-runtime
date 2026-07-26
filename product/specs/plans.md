# Brain4All — Subscription Plans: Free, Pro, Enterprise

How the product is packaged and sold, and how the tiers are enforced. Complements the
edition specs ([`oss.md`](oss.md), [`enterprise.md`](enterprise.md)) and revises the
current contract in [`../../docs/plans.md`](../../docs/plans.md) (see §7).

---

## 1. The model — deployment vs. plan (two independent axes)

**Deployment** and **subscription plan** are separate decisions:

- **Deployment** — *where the runtime runs.* Self-hosted (the open-core OSS runtime you
  run yourself) **or** Brain4All managed cloud. Self-hosting never limits local features.
- **Plan** — *what your authenticated account is entitled to* for cloud/managed and
  publishing features: **Free**, **Pro**, or **Enterprise**.

The one rule that shapes everything below:

> **Free and Pro are for a single person. Enterprise is for a team / business.**

This is enforced structurally by **tenant type**, not just by feature flags — see §2.

| | Free | Pro | Enterprise |
|---|---|---|---|
| Who it's for | One person, getting started | One power user, individually | A team / organization |
| Tenant type | Personal (1 seat) | Personal (1 seat) | Organization (many seats) |
| Can add members? | ❌ | ❌ | ✅ |
| Billing | Free | Self-serve subscription (per user) | Per-seat contract |

---

## 2. How we manage it (the mechanism)

Three layers make the tiers real and enforceable:

- **Tenant type is the individual-vs-team boundary.**
  - Free and Pro accounts each own a **personal tenant** with exactly **one seat**. A
    personal tenant has **no member management** — there is literally no "invite" action,
    so Pro cannot become a team. This is why *"Pro only works for users, not teams or
    business."*
  - Enterprise is an **organization tenant**: multiple members, roles, RBAC, SSO.
  - Upgrading Pro → Enterprise = migrating a personal tenant into a new org tenant
    (the user becomes the first member/owner).

- **Plan → entitlement set.** Each plan maps to a set of **capability flags** (on/off
  features like `marketplace.publish`, `voice.stt`, `teams`) and **quotas** (numeric
  limits). The control plane's **entitlements service** resolves these per request
  (Check / Reserve / Commit / Release; `-1` = unlimited, `0` = unavailable).
  - Unavailable paid feature → **403**; quota exhausted → **429**; not signed in → **401**.
  - Self-hosted **local** Hermes features always resolve **unlimited**, regardless of
    plan or connectivity — a plan only governs cloud/managed and publishing features.

- **Billing / subscription.**
  - **Free** — no charge; default for a new authenticated account.
  - **Pro** — self-serve monthly/annual subscription tied to the one user (card/checkout).
  - **Enterprise** — per-seat, contract or invoice, managed by the org admin; seats are
    provisioned by the org (invite / SCIM), each seat entitled at the Enterprise level.

So the answer to *"how can we manage — subscription and compare?"*: **plan = entitlement
set, tenant type = individual vs team, billing = per-user (Pro) vs per-seat (Enterprise).**

---

## 3. Free (Basic)

For one person to try Brain4All and run agents.

- **Tenant:** personal, single seat; no member management.
- **Core agent experience:** assistants, streaming chat, conversations, skills (install),
  local Kanban, workspace/files, MCP, model blends — all available (unlimited when
  self-hosted; quota-limited on managed).
- **Marketplace:** browse and **install free / community** skills & agents. **No
  publishing.**
- **Analytics:** local usage view; managed telemetry with short retention (7 days).
- **Voice / speech-to-text:** ❌ not included.
- **Agent teams (multi-agent workflows):** ❌ not included on managed (available only on
  self-hosted OSS locally).
- **Support:** community.
- **Quotas:** low (e.g. few managed agents, 1 provider connection type, capped cron).

## 4. Pro — for individuals  *(logged in, subscription)*

Everything a serious solo user needs. **Single user only — no teamwork, no org.**

- **Tenant:** personal, single seat; **cannot** add members or create an organization.
- **Everything in Free**, with higher quotas and longer telemetry retention.
- **Marketplace — publish:** ➕ **publish your own skills and agents** to the market;
  access **premium** items; creator **revenue share**; manage your listings & versions.
- **Speech-to-text / voice I/O:** ➕ personal STT + TTS (bring-your-own key or
  Brain4All-hosted), metered to the individual.
- **Advanced usage:** more managed agents, more provider connections, advanced blends,
  more scheduling/cron, priority routing.
- **Analytics:** longer retention, fuller history.
- **Explicitly NOT in Pro:**
  - ⛔ **Agent teams / multi-agent orchestration** (managed) — a business capability.
  - ⛔ Organizations, multiple members, shared workspaces, RBAC, SSO.
  - ⛔ Central org usage database, org budgets/chargeback, fleet, audit, governance.
- **Billing:** self-serve per-user subscription. *(Optional higher individual tier "Pro
  Max" = same features, ~5× quotas — still single-user.)*

## 5. Enterprise — for teams & business  *(organization, per-seat)*

Everything in Pro, made multi-user and governable. This is the only tier with teamwork.

- **Tenant:** organization; many seats, org-created accounts, RBAC, SSO.
- **Everything in Pro** for every member (marketplace, voice, advanced usage).
- **Teamwork:** ➕ **agent teams / multi-agent workflows** (managed) **and**
  ➕ **Enterprise boards** — cross-account Kanban assigning tasks to any member, agent,
  or team (see [`enterprise.md`](enterprise.md) §2.7 / plan E07).
- **Organization & identity:** orgs, accounts, OIDC/SAML/LDAP SSO, config-file RBAC,
  org-manager "view all", SCIM (E05).
- **Central usage & cost:** one PostgreSQL of all members' chats/tokens, admin
  dashboards, **enforced** budgets, chargeback, CSV/scheduled export (E02).
- **Fleet:** Incus provisioning + PC registration, versions, staged rollout, remote
  lifecycle (E03).
- **Managed voice gateway:** org-held keys, entitlement-gated, metered (E04).
- **Private marketplace:** org-internal catalog + skill/agent approval workflow.
- **Governance & compliance:** model/capability policy, retention/residency, external
  secrets, append-only audit log, SIEM streaming, backup/DR, license management.
- **Hosting:** self-hosted (incl. air-gapped) **or** managed cloud.
- **Billing:** per-seat, contract/invoice, org-managed.

---

## 6. Skill / Agent Marketplace  *(new — Pro & Enterprise)*

A catalog where users share, discover, install, and monetize **skills** and **pre-built
agents**.

**Capabilities**
- **Browse & search** the catalog by type, category, rating.
- **Install** an item into your library / as a new assistant.
- **Publish** a skill or agent you built, with versioning and a description.
- **Premium items** — paid skills/agents; **creator revenue share** on sales.
- **Trust & safety** — listing review, version signing, and the same **quarantine** of
  imported code that portable profiles already use; per-assistant sandboxing.

**Tier gating**
| Marketplace action | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Browse catalog | ✅ | ✅ | ✅ |
| Install **free** items | ✅ | ✅ | ✅ |
| Install **premium** items | ⛔ | ✅ | ✅ |
| **Publish** skills / agents | ⛔ | ✅ | ✅ |
| Creator revenue share | ⛔ | ✅ | ✅ |
| **Private org catalog** + approval | ⛔ | ⛔ | ➕ |

> Publishing is **Pro or Enterprise only** — the marketplace is a paid-tier capability.
> Free can consume free items; it cannot publish.

---

## 7. Plan comparison (checklist)

**Legend:** ✅ included · 🟡 limited / lower quota · ⛔ not available · ➕ tier-defining add

### Individual features
| Feature | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Assistants, chat, conversations | 🟡 low quota | ✅ | ✅ |
| Skills (install) | ✅ | ✅ | ✅ |
| Local Kanban (own board) | ✅ | ✅ | ✅ |
| Model blends, provider connections | 🟡 1 type | ✅ 5 | ✅ custom |
| Workspace & files, MCP | ✅ | ✅ | ✅ |
| Usage analytics (local view) | ✅ | ✅ | ✅ |
| Telemetry retention | 7 days | 90 days | 365 days |
| **Speech-to-text / voice** | ⛔ | ✅ | ✅ (org gateway) |
| **Marketplace — install premium** | ⛔ | ✅ | ✅ |
| **Marketplace — publish + revenue** | ⛔ | ✅ | ✅ |
| Support | Community | Priority | Dedicated |

### Team / business features (Enterprise-defining)
| Feature | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| **Agent teams (multi-agent, managed)** | ⛔ | ⛔ | ➕ |
| **Enterprise boards (cross-account Kanban)** | ⛔ | ⛔ | ➕ |
| Organizations & multiple members | ⛔ | ⛔ | ➕ |
| SSO (OIDC/SAML), LDAP, RBAC | ⛔ | ⛔ | ➕ |
| Org-manager "view all" oversight | ⛔ | ⛔ | ➕ |
| SCIM provisioning | ⛔ | ⛔ | ➕ |
| Central usage DB + admin dashboards | ⛔ | ⛔ | ➕ |
| Enforced budgets / chargeback / export | 🟡 advisory | 🟡 advisory | ✅ enforced |
| Fleet management (Incus/PC, rollout) | ⛔ | ⛔ | ➕ |
| Private org marketplace + approval | ⛔ | ⛔ | ➕ |
| Audit log, governance, retention policy | ⛔ | ⛔ | ➕ |
| Backup / DR, license management | ⛔ | ⛔ | ➕ |
| Managed cloud hosting | 🟡 | ✅ | ✅ |

### Tenancy & billing
| | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Tenant type | Personal | Personal | Organization |
| Seats | 1 | 1 | Many |
| Add members | ⛔ | ⛔ | ✅ |
| Billing | Free | Per-user subscription | Per-seat contract |
| Enforcement | quota (429) / feature (403) | quota / feature | org entitlements + RBAC |

---

## 8. What's common vs different (summary)

- **Common to all three:** the local agent experience (assistants, chat, skills, local
  Kanban, workspace, MCP, blends), local usage analytics, the privacy floor
  (content/credentials never leave the machine), and marketplace browsing/free installs.
- **Pro adds over Free (individual):** marketplace **publishing** + premium + revenue,
  **speech-to-text/voice**, higher quotas, longer retention, priority routing.
- **Enterprise adds over Pro (team/business):** **teamwork** (agent teams + cross-account
  Enterprise boards), organizations, SSO/RBAC/SCIM, central usage DB, enforced
  budgets, fleet, managed voice gateway, private marketplace, governance, audit,
  backup/DR — all under a multi-seat org tenant.
- **The hard line:** teamwork and organization features exist **only** at Enterprise.
  Pro is deliberately single-user.

---

## 9. Divergence from current `docs/plans.md` (proposed revisions)

This spec proposes three changes to the existing contract; apply to `docs/plans.md` if
approved:

1. **Consolidate to three plans** (Free / Pro / Enterprise). The current `promax` becomes
   an optional higher-quota **Pro Max** variant of Pro (still single-user), or is retired.
2. **Move managed *teams* out of Pro into Enterprise.** Current table gives Pro
   `Managed teams = 10`; per the individual-only rule, set **Pro managed teams = 0** and
   keep teams as an Enterprise capability. (Self-hosted local teams remain unlimited.)
3. **Add marketplace and voice/STT** rows: publishing + premium + revenue and voice are
   **Pro+**; private org catalog is Enterprise-only.

All other invariants in `docs/plans.md` stand: self-hosted local features stay unlimited;
telemetry is never a billing source of truth; content never centralizes; `429`/`403`/`401`
enforcement semantics unchanged.
