# Brain4All — Simple Edition Comparison

This is the short product contract. Detailed behavior belongs in:

- [OSS specification](oss.md)
- [Enterprise specification](enterprise.md)
- [Plans and cloud limits](plans.md)
- [Entitlements contract](../../docs/contracts/entitlements-v1.md)
- [Device command contract](../../docs/contracts/device-command-v1.md)
- [Portable bundle contract](../../docs/contracts/portable-bundle-v1.md)

## Product rule

Edition controls **features**. Deployment controls **resources**.

- Self-hosted runs on the customer's hardware and does not limit local resources.
- Cloud runs on Brain4All hardware and limits CPU, RAM, storage, objects, and run time.
- Free and Pro are personal products for one user.
- Enterprise includes Pro and adds organization features for many users.
- Losing a subscription or exceeding a cloud limit never deletes user data.

## Feature comparison

| Area | OSS Free | Personal Pro | Enterprise | Note |
|---|---|---|---|---|
| Users | One | One | Many | Only Enterprise has organizations and member management |
| Agents and chat | Included | Included | Included | Same Hermes runtime |
| Providers and model blends | Included | Included | Included | Enterprise may apply organization allowlists |
| Built-in skills | Included | Included | Included | Bundled with the runtime |
| Public marketplace browse | Included | Included | Included | Browsing does not install code |
| Public marketplace install | — | Included | Included | Installed code is scanned and explicitly approved |
| Publish marketplace skills | — | Included | Included | Includes versioning and listing management |
| Skill and memory local snapshots | Included | Included | Included | Created before risky persistent mutations |
| Hosted version history and restore | — | Included | Included | Client-side encrypted |
| Speech-to-text | — | Included | Included | Composer and channel voice notes |
| MCP connections | Included | Included | Included | Per agent; Enterprise adds organization policy |
| Cron and scheduled jobs | Included | Included | Included | Local scheduling remains available offline |
| Agent teams on one runtime | Included | Included | Included | Personal multi-agent work is not an Enterprise feature |
| Cross-user agent teams | — | — | Included | End-to-end encrypted between enrolled runtimes |
| Personal Kanban | Fixed stages | Fixed stages | Fixed stages | Backlog, Todo, In Progress, Done, plus Archived |
| Organization Kanban | — | — | Custom stages | Cross-user assignment, approvals, automation, WIP limits |
| Usage analytics | Local | Local | Local and central | Central storage contains organization-safe metadata |
| Budgets | Advisory | Advisory | Advisory or enforced | Enterprise supports cost centers and chargeback |
| SSO, SCIM and RBAC | — | — | Included | OIDC, SAML, LDAP, lifecycle and scoped permissions |
| Admin user management | — | — | Included | Invite, suspend, deprovision and oversight |
| Audit log | — | — | Included | Append-only, exportable and retention-controlled |
| Private skill catalog | — | — | Included | Organization approval workflow |
| Fleet management | — | — | Included | Devices, Incus runtimes and staged rollout |
| Governance | — | — | Included | Model, tool, MCP, skill, channel and retention policy |
| Backup and disaster recovery | Manual export | Manual export + hosted versions | Managed backup and DR | Enterprise can use audited key escrow |

## Deployment comparison

| Deployment | Edition | Login | Resources | Billing |
|---|---|---|---|---|
| Self-hosted Free | OSS Free | Optional | Unlimited local resources | Free |
| Self-hosted Pro | Personal Pro | Required for paid services | Unlimited local resources | Per-user subscription |
| Cloud Free | OSS Free | Required | Small managed runtime | Free |
| Cloud Pro | Personal Pro | Required | Managed Pro limits | Subscription |
| Cloud Pro Max | Personal Pro | Required | Larger managed limits | Subscription |
| Enterprise self-hosted | Enterprise | Required; air-gap options | Contract/customer hardware | Per-seat contract |
| Enterprise Cloud | Enterprise | Required | Contracted managed resources | Per-seat contract |

Exact cloud limits are recommendations in [plans.md](plans.md#72-what-we-limit--and-the-recommended-values)
and may be tuned after cost testing without changing edition capabilities.

## What Pro adds

Pro adds only three service groups:

1. Public skill marketplace installation and publishing.
2. Speech-to-text.
3. Hosted skill and memory version history, diff, and restore.

Pro remains single-user. It does not add invitations, shared workspaces, custom Kanban,
RBAC, SSO, audit, or organization administration.

## What Enterprise adds

Enterprise includes every Pro capability and adds the business layer:

1. Organizations, users, SSO, SCIM, RBAC, and administration.
2. Cross-user boards and cross-user agent teams.
3. Custom Kanban stages, transition rules, approvals, automation, and WIP limits.
4. Central usage, enforced budgets, cost centers, and exports.
5. Private skills, approval policy, managed secrets, and organization governance.
6. Fleet management, audit, retention, residency, backup, and disaster recovery.

The Enterprise control plane is implemented in Go with PostgreSQL in the separate
`brain4all-enterprise` repository. It consumes released OSS runtime contracts and must
not be required by a signed-out self-hosted installation.

## Privacy and collaboration note

Normal conversations, prompts, responses, tool arguments, files, memory, and credentials
stay in the runtime that owns them.

Enterprise collaboration is the explicit exception to “nothing leaves the runtime”:

- task inputs, attachments, and results may travel to another enrolled runtime;
- collaboration payloads are end-to-end encrypted;
- the control plane relays ciphertext and stores delivery/audit metadata;
- each command is signed, expires, and has replay protection;
- only enrolled sender and recipient runtimes can read collaboration content.

Hosted skill and memory snapshots are also client-side encrypted. Personal Pro uses a
user-held key. Enterprise may additionally wrap the key to an organization escrow key;
every escrow operation requires permission, justification, and an audit event.

## Reference notes

- Quota operations use `Check`, `Reserve`, `Commit`, and `Release`; see
  [entitlements v1](../../docs/contracts/entitlements-v1.md).
- Cross-runtime commands use outbound TLS, signed envelopes, idempotency, and replay
  protection; see [device command v1](../../docs/contracts/device-command-v1.md).
- Freeze a separate `collaboration-payload-v1` inner-payload contract before building
  cross-user teams; do not silently change the frozen device-command envelope.
- Agent export/import excludes credentials and device identity; see
  [portable bundle v1](../../docs/contracts/portable-bundle-v1.md).
- The OSS runtime stays Python/FastAPI/Hermes/9router. Go belongs to the separate
  Enterprise control plane.
