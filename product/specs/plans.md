# XNOBrain — Editions & Plans: OSS Free, OSS Pro, Enterprise, and Cloud

How the product is packaged and sold, and how the tiers are enforced. Complements the
edition specs ([`oss.md`](oss.md), [`enterprise.md`](enterprise.md)) and revises the
current contract in [`../../docs/plans.md`](../../docs/plans.md) (see §10).

---

## 1. The model — two independent axes

**Edition** (what you can do) and **deployment** (whose hardware it runs on) are separate
decisions. Confusing them is the single biggest source of error in the old spec.

| | **Self-hosted** (your hardware) | **Cloud** (our servers) |
|---|---|---|
| **OSS** | Free · Pro | Cloud Free · Cloud Pro · Cloud Pro Max |
| **Enterprise** | Enterprise (self-hosted, incl. air-gapped) | Enterprise Cloud (managed) |

- **Edition** decides *capabilities*, and the editions are strictly cumulative:
  **Free ⊂ Pro ⊂ Enterprise.** Pro adds the skill marketplace, speech-to-text, and hosted
  skill/memory snapshot versions; **Enterprise is Pro plus the business layer** (orgs,
  oversight, governance) — every Pro capability is included for every seat, and no Pro
  capability is ever taken away.
- **Deployment** decides *resource limits*: self-hosted has none — it is the user's own
  CPU, RAM, and disk. Cloud has quotas, because it is our CPU, RAM, and disk.

> **The rule:** *editions gate features; deployment gates resources.* A cloud quota is
> never a feature paywall, and a feature flag is never a resource cap.

Two more rules shape everything below:

> **Free and Pro are the same product.** Pro adds exactly three capabilities — the
> **skill marketplace**, **speech-to-text**, and **hosted skill/memory snapshot
> versions**. No capability and no resource count differs otherwise on a self-hosted
> install. All three are the same kind of thing: a XNOBrain-operated service with a real
> marginal cost (a catalog, transcription compute, stored bytes).
>
> **Free and Pro are for one person; Enterprise is for a business.** Teamwork here means
> *between people*, not between agents — agent teams are a standalone feature and ship in
> Free.

## 2. What every standalone user gets (Free **and** Pro)

The complete single-user product. **Unlimited on a self-hosted install** — there is no
local quota, and no sign-in is required to use any of it. On cloud the same capabilities
are present but sized by plan (§7).

- **Agents** — create, configure, update, delete; per-agent model, approval mode,
  workspace, and MCP servers.
- **Conversations** — multiple conversations per agent, streaming, queueing, retry,
  file `@mention`.
- **Skills** — the built-in system skill library bundled with the runtime; enable or
  disable per agent.
- **Provider connections** — connect every supported provider, with **multiple accounts
  per provider**, priority ordering, and per-account testing. *(No **edition** limits the
  number of providers, accounts, or connection types — Free and Pro are identical. Cloud
  caps the count as a resource limit, §7.)*
- **Combos (model blends)** — named 9router strategies (fallback / round-robin / fusion)
  usable as an agent's model.
- **Agent teams** — orchestrator + workers, parallel or DAG workflows, run history.
- **Kanban** — a local task board; assign tasks to agents, attach skills, track runs.
- **Message channels** — connect an agent to Telegram, Discord, Slack, WhatsApp, Signal
  and other Hermes-supported channels, per agent.
- **Cron / scheduling** — recurring and one-off runs, with delivery targets.
- **Usage** — local analytics per agent, per model, over time; advisory budgets.
- **Workspace & files**, **portable profile export/import**, localization, theming.

Full functional detail: [`oss.md`](oss.md).

## 3. OSS Free

Everything in §2, unlimited, with no account required.

- **Skills:** the **built-in system skills only** — the library shipped with the runtime.
  No marketplace: cannot install third-party skills, cannot publish.
- **Speech-to-text:** ⛔ not available.
- **Skill & memory snapshot versions:** ⛔ local snapshots and manual portable-bundle
  export only; no retained history, nothing stored off the device.
- **Support:** community.
- **Account:** optional. A signed-out install is fully functional.

## 4. OSS Pro — *(paid, individual)*

Identical to Free in every respect except the three capabilities below. Same runtime, same
machine, same unlimited local resources.

- ➕ **Skill marketplace** — browse, **install** skills from the market (free and
  premium), and **publish** your own skills with versioning, ratings, and creator
  revenue share. See §5.
- ➕ **Speech-to-text** — dictate to an agent by voice; voice notes arriving over a
  message channel become transcribed messages. Available in the composer and in channel
  ingestion.
- ➕ **Skill & memory snapshot versions** — see §4.1.
- **Support:** priority.
- **Billing:** self-serve per-user subscription; requires a signed-in account.
- **Still single-user:** no organizations, no members, no shared workspaces, no RBAC.

### 4.1 Skill & memory snapshot versions

We keep a **version history of what makes an agent itself** — its skills and its memory —
on XNOBrain's servers, so it survives a lost disk, a bad edit, or a move to a new
machine. Free keeps local snapshots; Pro keeps *versions*, stored and restorable.

| | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Local snapshot before risky writes | ✅ | ✅ | ✅ |
| Manual portable-bundle export | ✅ | ✅ | ✅ |
| **Retained version history (skills + memory)** | ⛔ | ✅ | ✅ |
| **Diff two versions** | ⛔ | ✅ | ✅ |
| **Restore / roll back an agent** | ⛔ | ✅ | ✅ |
| **Restore onto a new machine** | ⛔ | ✅ | ✅ |
| Org retention policy + admin key escrow | ⛔ | ⛔ | ➕ |

- **Why it's paid:** it is storage we hold, indefinitely, per user. It is also the
  stickiest thing we can offer — an agent that has learned you for a year is only
  irreplaceable if it cannot be lost.
- **Encrypted client-side with a user-held key.** We store ciphertext and cannot read
  memory content, which is what keeps this compatible with the privacy floor. The
  trade-off — lose the key, lose the snapshots — must be stated at setup, not buried.
- **Quotas** (retained versions, total bytes) are in §7.2 and apply to Pro on *any*
  deployment, self-hosted included: unlike the §2 capabilities, these bytes sit on our
  disks wherever the runtime runs.
- Enterprise adds org-set retention and **admin key escrow**, so a departed member's
  agent can still be recovered — the one case where a user-held key is not enough.

**What Pro does *not* change:** provider connections, accounts per provider, number of
agents, teams, kanban boards, channels, cron jobs, MCP servers, conversations, or usage
history. Those are unlimited in both tiers on a self-hosted install; on cloud they are
sized by the cloud plan, not by Free-vs-Pro (§7).

## 5. Skill marketplace — *(Pro & Enterprise)*

A catalog where users discover, install, publish, and monetize skills.

| Marketplace action | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Use built-in system skills | ✅ | ✅ | ✅ |
| Browse the catalog | ✅ | ✅ | ✅ |
| **Install** skills from the market | ⛔ | ✅ | ✅ |
| **Publish** a skill | ⛔ | ✅ | ✅ |
| Premium skills / creator revenue share | ⛔ | ✅ | ✅ |
| **Private org catalog** + approval workflow | ⛔ | ⛔ | ➕ |

- Free can *see* the catalog — it just cannot install from it or publish to it. Browsing
  without installing is how Free discovers a reason to upgrade.
- Trust & safety: listing review, version signing, dependency/malware scanning, and the
  same **quarantine** applied to imported portable profiles. Evaluation and badging are
  specified in [`skill-evaluation.md`](skill-evaluation.md).
- Enterprise adds an org-internal catalog and an admin approval workflow on top.

## 6. Enterprise — *(organization, per-seat)*

> **Enterprise = Pro + the business layer.** Every seat includes the entire Pro product —
> all of §2, plus the marketplace, speech-to-text, and snapshot versions — and Enterprise
> then adds what a business needs on top. It never removes or downgrades a Pro capability;
> where it appears to change one (provider keys, voice keys, snapshot retention), it is
> adding *org control over* it, not taking it away.

The member's local runtime is unchanged; the control plane sits above it.

- **Organization & identity:** org tenant, org-provisioned accounts, OIDC/SAML/LDAP SSO,
  config-file RBAC, org-manager "view all" oversight, SCIM (E05).
- **Central usage & cost:** one PostgreSQL holding every member's token/cost/session
  metadata, admin dashboards, **enforced** budgets (Free/Pro budgets are advisory only),
  chargeback and cost centers, CSV/scheduled export (E02).
- **Enterprise boards:** one org-owned Kanban whose tasks are assigned **across
  accounts** and dispatched to each assignee's runtime (E07). Standalone Kanban stays
  per-user.
- **Cross-account agent teams:** teams that span members' machines, not just one
  install's agents.
- **Fleet management:** Incus provisioning + PC registration, device inventory, remote
  restart/drain, staged version rollout (E03).
- **Managed voice gateway:** org-held STT/TTS keys, entitlement-gated, minutes metered
  centrally — members never handle a key (E04).
- **Private marketplace:** org-internal skill catalog with an approval workflow.
- **Snapshot governance:** org-set retention for skill/memory version history, and
  **admin key escrow** so a departed member's agents remain recoverable.
- **Governance:** model/provider/blend allowlists, tool and MCP policy, skill approval,
  retention & residency, external secrets (Vault/KMS).
- **Audit & compliance:** append-only audit log, SIEM streaming, evidence exports,
  SOC 2 / ISO 27001 / GDPR program.
- **Operations:** managed encrypted backups, HA/DR, license & update management.
- **Hosting:** self-hosted (incl. air-gapped) **or** XNOBrain managed cloud.
- **Billing:** per seat, contract or invoice, org-managed.

Full functional detail: [`enterprise.md`](enterprise.md).

---

## 7. Cloud — XNOBrain-hosted deployment

The same product, running on **our** servers instead of the user's machine. A cloud
account gets a managed runtime (an Incus container) that we provision, patch, back up,
and pay the electricity for.

Both editions can be hosted:

| Cloud offering | Edition | Tenant | Plans |
|---|---|---|---|
| **Cloud Free** | OSS Free | Personal, 1 seat | Free, resource-limited |
| **Cloud Pro** | OSS Pro | Personal, 1 seat | Pro |
| **Cloud Pro Max** | OSS Pro | Personal, 1 seat | Pro Max (≈5× Pro) |
| **Enterprise Cloud** | Enterprise | Organization | Per-seat, contracted resources |

Cloud Free is permanent and provides the complete Free capability set with conservative
resource limits, aggressive idle suspension, and abuse controls. A time-boxed Pro trial
may temporarily raise its capabilities and resources, but the account returns to Cloud
Free when the trial ends without deleting data.

### 7.1 Why cloud has limits at all

Self-hosted has no quotas because the user is paying for the hardware. On cloud, every
agent, cron minute, and retained session consumes CPU, RAM, and disk that we pay for.
Cloud plans therefore size **resources**, never features:

> A cloud user on Pro can do **everything** an OSS Pro user can do. They can just do less
> of it at once. If a cloud plan ever hides a *capability*, that is a spec violation —
> capabilities belong to the edition axis.
> Cloud Free follows the same rule for the OSS Free capability set.

### 7.2 What we limit — and the recommended values

**Pro Max = 5× Pro** on every countable dimension, except depth/duration and
safety-sensitive settings, where scaling 5× buys nothing and risks runaway cost.

#### Compute (container sizing)
| Limit | Cloud Free | Cloud Pro | Cloud Pro Max | Why |
|---|---:|---:|---:|---|
| vCPU (cgroup cap, burstable) | 1 | 2 | 8 | Agent turns are bursty |
| RAM | 2 GB | 4 GB | 16 GB | Runtime and concurrent work |
| Disk (profile + workspace) | 5 GB | 20 GB | 100 GB | Workspace files dominate |
| Concurrent agent turns | 1 | 3 | 15 | Primary CPU governor |
| **Idle suspend** | after 10 min | after 20 min | after 20 min | Primary cost control |

#### Countable objects
| Limit | Cloud Free | Cloud Pro | Cloud Pro Max |
|---|---:|---:|---:|
| Agents | 3 | 20 | 100 |
| Agent teams / agents per team | 1 / 3 | 10 / 10 | 50 / 50 |
| Provider connections per provider (accounts) | 1 | 5 | 25 |
| MCP servers | 3 | 25 | 125 |
| Message channels per agent | 1 | 5 | 25 |
| Installed marketplace skills | 0 | 100 | 500 |
| Kanban boards / active tasks | 1 / 100 | 5 / 500 | 25 / 2,500 |

#### Scheduled execution (the metered dimension)
| Limit | Cloud Free | Cloud Pro | Cloud Pro Max |
|---|---:|---:|---:|
| Cron job definitions | 5 | 50 | 250 |
| **Agent run-minutes / month** | 200 | 3,000 | 15,000 |
| Parallel cron runs | 1 | 5 | 25 |
| Max duration of a single run | 10 min | 15 min | **30 min** *(not 5×)* |
| Max runs / day | 20 | 250 | 1,250 |

> **Recommendation — meter run-minutes, not cron runs.** A 30-second cron job and a
> 12-minute one cost us wildly different amounts. Bill the wall-clock **execution minutes
> of agent work** (cron, team runs, board tasks — recommended: interactive chat excluded,
> since it is self-limiting and quota anxiety would ruin the chat experience). One
> number the user can understand, directly proportional to our cost.

#### Storage & retention
| Limit | Cloud Free | Cloud Pro | Cloud Pro Max |
|---|---:|---:|---:|
| Chat session history (full content) | 30 days | 12 months | 24 months |
| Usage analytics — detailed | 30 days | 90 days | 180 days |
| Usage analytics — **daily rollups** | forever | forever | forever |
| Cron/team run logs | 7 days | 30 days | 90 days |
| Workspace files | 1 GB | 5 GB | 25 GB |
| Portable-bundle backups retained | 1 | 3 | 10 |
| **Skill/memory snapshot versions per agent** | 0 | 30 | 150 |
| **Skill/memory snapshot storage (total)** | 0 | 2 GB | 10 GB |
| Automatic hosted snapshot frequency | — | daily | daily |

> **Note — snapshot storage is the one quota that also applies to self-hosted Pro.**
> Everything else in §7 is cloud-only, because it is our container. Snapshot bytes sit on
> our disks no matter where the runtime runs, so the allowance travels with the Pro
> subscription rather than with the cloud plan. A self-hosted Pro user gets the Cloud Pro
> allowance (30 versions/agent, 2 GB); Pro Max sizing applies if they buy it.

> **Recommendation — tier retention, don't truncate it.** Aged-out sessions and usage
> rows should collapse into **pre-aggregated daily rollups** (per agent, per model:
> tokens, cost, session count) before deletion. Rollups are ~1/1000th the size, so we can
> keep them indefinitely — the user keeps their long-term usage chart and loses only
> per-message detail. This cuts storage far more than a shorter window does, and it is a
> feature ("2 years of trends") rather than a loss.

### 7.3 Enterprise Cloud

Resources are **contracted, not tiered**: per-seat container sizing, org-level run-minute
pooling (unused member minutes are shared across the org), negotiated retention within
the org's compliance requirement, and org-set residency. Enterprise Cloud also carries
the control plane itself as a managed service. Quotas are enforced through the same
entitlements service, with org-level rather than per-user counters.

### 7.4 Recommendations for cost control

Ordered by impact:

1. **Idle suspend/resume.** Most personal containers are idle >95% of the time. Freeze on
   idle, restore on the next request or cron tick (target: cold resume < 3 s). This is
   worth more than every quota in §7.2 combined. Cron wakes the container; the gateway
   for message channels is the one thing that must stay reachable — recommended: a shared
   webhook front-end that wakes the container on inbound message.
2. **Oversubscribe CPU, hard-cap RAM and disk.** CPU is burstable and reclaimable; RAM
   and disk are not. Set cgroup CPU as a *share with a ceiling*, and RAM/disk as hard
   limits.
3. **Retention rollups before retention cuts** (§7.2) — keeps the product feeling
   generous while cutting the storage bill.
4. **Overage instead of a wall.** When run-minutes are exhausted, offer a top-up block
   rather than stopping the user's automation dead. Hard-stop only on abuse thresholds.
   Recommended: warn at 80%, notify at 100%, queue (not drop) scheduled runs for 24 h,
   then pause the schedule.
5. **Never destroy on downgrade or lapse.** Over-limit objects go **read-only / paused**,
   never deleted; the user picks what to keep. Data loss is the fastest way to lose a
   cloud subscriber, and the storage saved is trivial next to the churn.
6. **Fair-use ceiling on top of quotas** — a per-container absolute cap (e.g. sustained
   CPU over N hours) that catches crypto-mining and runaway loops regardless of plan.
7. **Egress and model spend are the user's, not ours.** Provider keys stay the user's on
   cloud too, so LLM cost never lands on our bill — keep it that way; it is what makes
   these plans priceable.

---

## 8. Comparison checklist

**Legend:** ✅ included · 🟡 limited / different · ⛔ not available · ➕ tier-defining add

Capabilities by edition. **Resource counts are not shown here** — they depend on
deployment, not edition (self-hosted = unlimited; cloud = §7).

### Standalone capabilities (identical in Free and Pro)
| Capability | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Agents — create / configure / update | ✅ | ✅ | ✅ |
| Conversations per agent | ✅ | ✅ | ✅ |
| Built-in system skills | ✅ | ✅ | ✅ |
| Provider connections (all providers) | ✅ | ✅ | ✅ *(org-managed keys)* |
| Multiple accounts per provider | ✅ | ✅ | ✅ |
| Combos / model blends | ✅ | ✅ | ✅ *(+ org allowlist)* |
| Agent teams (multi-agent workflows) | ✅ | ✅ | ✅ *(+ cross-account)* |
| Kanban board | ✅ | ✅ | ✅ *(+ enterprise boards)* |
| Message channels per agent | ✅ | ✅ | ✅ *(+ channel policy)* |
| Cron / scheduling | ✅ | ✅ | ✅ |
| Usage analytics (local) | ✅ | ✅ | ✅ *(+ central)* |
| Workspace & files, MCP, portable profiles | ✅ | ✅ | ✅ |
| Budgets | 🟡 advisory | 🟡 advisory | ✅ enforced |

### The Pro delta (the only three differences)
| Capability | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| **Skill marketplace — install** | ⛔ | ➕ | ✅ |
| **Skill marketplace — publish + revenue** | ⛔ | ➕ | ✅ |
| **Speech-to-text** | ⛔ | ➕ | ✅ *(org gateway)* |
| **Skill & memory snapshot versions** (history, diff, restore) | ⛔ | ➕ | ✅ *(+ org retention & key escrow)* |
| Support | Community | Priority | Dedicated |

### The Enterprise delta (business layer — **added on top of all of Pro**)
| Capability | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Organizations & multiple members | ⛔ | ⛔ | ➕ |
| SSO (OIDC/SAML), LDAP, RBAC | ⛔ | ⛔ | ➕ |
| SCIM provisioning | ⛔ | ⛔ | ➕ |
| Org-manager "view all" oversight | ⛔ | ⛔ | ➕ |
| Central usage DB + admin dashboards | ⛔ | ⛔ | ➕ |
| Enforced budgets / chargeback / export | ⛔ | ⛔ | ➕ |
| Enterprise boards (cross-account Kanban) | ⛔ | ⛔ | ➕ |
| Cross-account agent teams | ⛔ | ⛔ | ➕ |
| Fleet management (Incus/PC, rollout) | ⛔ | ⛔ | ➕ |
| Managed voice gateway (org keys, metered) | ⛔ | ⛔ | ➕ |
| Private org skill catalog + approval | ⛔ | ⛔ | ➕ |
| Snapshot retention policy + admin key escrow | ⛔ | ⛔ | ➕ |
| Governance policy (models, tools, MCP, skills) | ⛔ | ⛔ | ➕ |
| Audit log, retention/residency, SIEM | ⛔ | ⛔ | ➕ |
| Managed backup / DR, license management | ⛔ | ⛔ | ➕ |
| Enterprise Cloud governance | ⛔ | ⛔ | ➕ |

### Tenancy & billing
| | Free | Pro | Enterprise |
|---|:--:|:--:|:--:|
| Tenant type | Personal | Personal | Organization |
| Seats | 1 | 1 | Many |
| Add members | ⛔ | ⛔ | ✅ |
| Sign-in required | No | Yes | Yes |
| Self-hosted | ✅ | ✅ | ✅ |
| Cloud (our servers) | ✅ Cloud Free | ✅ Pro / Pro Max | ✅ contracted |
| Billing | Free | Per-user subscription (+ cloud tier) | Per-seat contract |

---

## 9. How it is enforced (mechanism)

Three layers make the tiers real:

- **Tenant type = the individual-vs-team boundary.** Free and Pro own a **personal
  tenant** with exactly one seat and **no member management** — there is no "invite"
  action, so Pro structurally cannot become a team. Enterprise is an **organization
  tenant**. Upgrading Pro → Enterprise migrates the personal tenant into a new org tenant
  with the user as first owner.

- **Edition → capability flags.** An edition maps to on/off flags, not to resource
  quotas, and the sets nest (Free ⊂ Pro ⊂ Enterprise). The only flags that differ between
  Free and Pro are `marketplace.install`, `marketplace.publish`, `voice.stt`, and
  `snapshots.versions`. Enterprise inherits all four and adds the org flags. The
  entitlements service resolves them per request (Check / Reserve / Commit / Release;
  `-1` = unlimited, `0` = unavailable).
  - Unavailable feature → **403**; quota exhausted → **429**; not signed in → **401**.
  - **Self-hosted capabilities always resolve unlimited** — regardless of plan, account,
    or connectivity. An edition only governs marketplace, voice, and the enterprise
    control plane.

- **Deployment → resource quotas.** Cloud plans map to numeric limits (§7), enforced in
  two places: **admission** (creating the 21st agent on Pro → 429 at the API) and
  **execution** (cgroup CPU/RAM ceilings and a run-minute reservation taken before a
  scheduled run starts, committed on completion, released on failure). Self-hosted
  resolves every numeric limit to `-1`.

- **Billing.** Free — no charge, no account required. Pro — self-serve per-user
  subscription; cloud hosting is a separate line (Pro or Pro Max sizing) with optional
  run-minute top-ups. Enterprise — per-seat contract, seats provisioned by the org
  (invite / SCIM), cloud resources contracted.

**Metering note.** Speech-to-text is the one paid capability with a marginal cost. Pro
includes a monthly transcription allowance on the XNOBrain-hosted STT service and also
permits a bring-your-own provider key (unmetered). Enterprise routes all voice through
the org gateway with org-held keys, metered into the central usage database.

---

## 10. Engineering references

The shorter engineering contract is [`../../docs/plans.md`](../../docs/plans.md).
Implementations must also follow:

- [`../../docs/contracts/entitlements-v1.md`](../../docs/contracts/entitlements-v1.md)
  for capability and quota decisions;
- [`../../docs/contracts/device-command-v1.md`](../../docs/contracts/device-command-v1.md)
  for outbound commands and encrypted cross-runtime collaboration;
- [`../../docs/contracts/portable-bundle-v1.md`](../../docs/contracts/portable-bundle-v1.md)
  for agent import, export, and managed backup compatibility.

Recommended numeric cloud limits may change after cost testing. The edition boundaries,
single-user versus organization tenancy, self-hosted unlimited rule, privacy guarantees,
and no-data-loss downgrade behavior are product contracts and require an explicit spec
revision to change.
