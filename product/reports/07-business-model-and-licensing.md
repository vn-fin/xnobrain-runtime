# 07 · Business Model & Licensing

> The plan — open-source a personal edition, sell a proprietary enterprise/cloud
> edition firms can self-host or run in cloud with protected source — is a proven
> **open-core** pattern. This file benchmarks precedents, then gives concrete license
> recommendations. External claims linked; recommendations are marked as opinion.

## 1. Open-core precedents (the pattern is well-trodden)

The open-core model = **open-source core (MIT/Apache) + proprietary enterprise
features via paid subscription**
[[open-core overview](https://viprasol.com/blog/open-source-business-model/)]. Your
`brain4all` (public) + `brain4all-enterprise` (private) split is textbook.

| Company | Core license | Commercial model | Lesson for you |
|---|---|---|---|
| **GitLab** | started MIT, proprietary Enterprise Edition | open-core; free CE, paid EE for security/compliance/DevOps | the canonical open-core success; feature-tiering works |
| **HashiCorp** | moved MPL → **BSL/BUSL** (2023) | source-available; blocks competitors from reselling as managed service | [[InfoQ](https://www.infoq.com/news/2023/08/hashicorp-adopts-bsl/)] — BSL stops hyperscalers, but forked **OpenTofu** — community backlash is real |
| **Elastic** | Apache → **SSPL** → back to AGPL | source-available then re-opened | over-restricting can cost community goodwill |
| **n8n** | **fair-code** ("internal use free, resale prohibited") | open-core + cloud | closest to your shape; lets firms self-host free, blocks resale |
| **Sourcegraph** | went closed-source | enterprise sales | pure-enterprise is viable but loses the OSS funnel |

**Pattern that fits Brain4All best:** **permissive/fair-code core + closed enterprise
control plane** (like n8n / GitLab), *not* a restrictive relicense of the core (you
don't own Hermes' license anyway — it's MIT and stays MIT).

## 2. The two licensing decisions you actually face

### Decision A — the license for *your own* OSS repo (`brain4all`)
The README says it outright: *"Choose and add a license before public distribution."*
This is currently **unset** and blocks public launch. Options (opinion):

| Option | Effect | When to pick |
|---|---|---|
| **MIT / Apache-2.0** | maximally open; anyone (incl. competitors) can use/host it | if adoption/funnel > protection; matches Hermes' MIT; simplest |
| **AGPL-3.0** | copyleft; anyone offering it as a *network service* must open their modifications | deters a hyperscaler from running a closed hosted fork, while still "open source" |
| **BSL / fair-code (n8n-style)** | source-available; free to self-host, **resale/managed-service prohibited** for N years then converts | best *commercial* protection; **but not OSI "open source"** — manage messaging |

**Recommendation (opinion):** **Apache-2.0 or AGPL-3.0 for the OSS core**, and keep
**all the moat (certification, registry, identity, royalty, billing) in the closed
`brain4all-enterprise` repo.** Rationale:

- The moat is **engines 05–07 & 09**, which are *already* private by design — so you
  don't need a restrictive core license to protect the valuable part. That lets the
  core stay genuinely open (better adoption, community, trust), while the money lives
  behind the enterprise boundary.
- **AGPL** adds a mild deterrent against a closed hosted clone of the core without
  scaring self-hosting firms (they use it internally, not redistribute).
- Avoid BSL/SSPL unless you specifically fear a hyperscaler reselling the *core* —
  and note HashiCorp/Elastic show the community cost.
- ⚠️ **License compatibility:** if you pick AGPL for the core, confirm compatibility
  with Hermes (MIT — fine, MIT is AGPL-compatible) and every bundled dependency, and
  keep an attribution/NOTICE inventory.

### Decision B — the *personal (Python + Go)* vs *enterprise (Go)* framing
The stated intention describes open-source as "Python + Go" and enterprise as "Go." The
**repo today** is: **OSS = Python + React; Go = enterprise only** (the old Go/Postgres OSS path was
retired — see [01-current-state.md](01-current-state.md) §4). Reconcile deliberately:

- **Recommended (opinion): keep OSS Python + React; Go stays enterprise.** Simplest,
  matches the code, one clean boundary. Drop "Python + Go OSS" from the pitch unless
  you have a specific reason.
- **Alternative:** if you genuinely want a Go surface in OSS (e.g. a small local
  daemon/agent/CLI for edge/self-host installs), scope it tightly and treat it as a
  *new* OSS component — don't resurrect the retired Fiber/Postgres server.

## 3. Deployment & revenue model (matches your intent)

Your intent — *install on a firm's server, or deploy in cloud, source code
protected* — maps cleanly onto the existing edition contract
(`docs/enterprise-extension.md`):

| Edition | Where it runs | Data plane | Control plane | Revenue |
|---|---|---|---|---|
| **Self-hosted Free (OSS)** | firm's server / laptop | atomic files | none | $0 (funnel) |
| **Enterprise self-hosted** | firm's server | files + local enforcement | **private Go control plane** (RBAC, SSO, audit, quotas, billing) — source protected | license/subscription |
| **Cloud (Free / Pro / Enterprise)** | your cloud (Incus/managed) | shared private PostgreSQL | managed | seat + usage |

Twin-Terminal-era additions (Phase 3+): **per-installed-twin fees + royalty
settlement + certification fees** (the vision's "seat fee + per-twin fee + royalty").
The billing/entitlement plumbing already lives in enterprise; **lineage & royalty
(engine 09) is the net-new build.**

**Source-code protection when self-hosted on a firm's server:** the honest constraint
— software delivered to a customer's machine can be inspected. Real protection comes
from: (1) keeping the control plane/moat as a **service the firm calls**
(`ENTERPRISE_API_URL`) rather than code they run, where feasible; (2) license terms +
BSL-style resale prohibition; (3) shipping compiled Go binaries, not source. Don't
promise "unbreakable" source protection for on-prem — promise *licensed, supported,
and legally protected*.

## 4. Concrete next actions on this file's topic

1. **Pick and commit the OSS core license** (recommend Apache-2.0 or AGPL-3.0) — this
   is a launch blocker per the README.
2. **Add NOTICE / third-party-license inventory** for Hermes (MIT) + router + deps.
3. **Decide the Python-only-vs-Python+Go OSS question** and update the README/pitch so
   the story matches the code.
4. **Draft enterprise EULA + resale/BSL-style terms** for the enterprise binaries.
5. **Draft craft-ownership + fidelity-liability contract templates** (the vision's two
   red boxes — also gating for [06-certification-moat.md](06-certification-moat.md)).
