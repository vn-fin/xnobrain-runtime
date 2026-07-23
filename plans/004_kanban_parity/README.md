# 004 — Hermes Kanban feature parity

Priority: P2/P3. Begin after the foundation, primary experience, and automation
plans are stable. Deliver this plan in small vertical slices; it is not one
large release.

## Goal

Expose the useful breadth of Hermes Kanban without turning Brain4All into a
developer-only dashboard. Reuse pinned public Hermes operations and preserve
the fixed five-state product model.

“Parity” means the supported Hermes capability is either:

- available through an understandable Brain4All experience;
- intentionally located in an Advanced/Diagnostics area; or
- explicitly documented as CLI-only because a safe, stable upstream API or a
  broadly useful GUI does not exist.

It does not mean proxying every private dashboard route.

All CLI interoperability and upstream contributions in this plan target the
current `hermes_cli` command tree and documented extension mechanisms. Legacy
CLI code is out of scope.

## Feature inventory

The inventory below combines the documented CLI/tools and current upstream
dashboard implementation. Reconfirm it at the pinned revision.

| Area | Hermes capability | Brain4All treatment | Priority |
| --- | --- | --- | --- |
| Boards | init, list, create, switch, settings, delete | Default board first; management in board menu/settings | P2 |
| Tasks | create, list, show, edit, archive, bulk actions | Core in plans 001/002; bulk in this plan | P1/P2 |
| Assignment | assign, reassign, claim, assignees | Simple picker; eligibility/reason details | P1 |
| Lifecycle | promote, schedule, dispatch, complete, block, unblock, review | Five-state intent mapping; detail badges | P1 |
| Links | link, unlink, dependencies | Drawer editor and dependency explanation/graph | P1/P2 |
| Discussion | comments and worker notes | Human-readable timeline/comments | P1 |
| Files | upload, list, download, remove | Safe drawer attachments | P1 |
| Workers | active workers, heartbeat, progress, terminate, reclaim | Compact live state; destructive controls in Advanced | P2 |
| Runs | run history, inspect, summaries, metadata, tail/watch | Summary first; sanitized logs on demand | P2 |
| Workspaces | scratch, directory, worktree | Presets and plain-language risk/help | P2 |
| Execution | skills, profile, model/provider override, priority, tags | Advanced task editor with defaults | P2 |
| Goals/spec | goal mode, specify, manual decompose | Preview before mutation; show generated children | P2 |
| Automation | scheduled task/cron linkage | Plan 003 default-board automation | P1 |
| Diagnostics | stats, log, diagnostics, GC, recovery | Settings > Diagnostics with guided repair | P2 |
| Notifications | subscriptions/notifications | Opt-in task/board controls if delivery exists | P2 |
| Profiles | descriptions/auto-description | Settings and assignee explanation | P2 |
| Orchestration | auto-decompose, swarm/workflow fields | Advanced, feature-flagged, stable API only | P3 |
| Tenants | grouping/filtering where supported | Optional local organization, not access control | P3 |

## Slice A — Board management and power organization

- Protect a stable default board and label it clearly.
- Create, switch, rename, configure, archive/delete boards using upstream
  validation and safety checks.
- Define what happens to automation links before board deletion; automation
  templates remain on the default board.
- Add saved filters, deterministic sort, search, and filter URLs.
- Add bulk assign, legal move, tagging, and archive with a preview and per-item
  outcome.
- Add useful counts: active, needs input, overdue/stale worker, completed
  recently. Avoid vanity statistics.

Acceptance: two boards remain isolated in the shared Hermes storage, board
switching agrees with the CLI, bulk partial failures are transparent, and
default-board history cannot be accidentally deleted.

## Slice B — Dependencies, review, and audit

- Visualize parent/child and blocking links in the drawer; add a compact graph
  only when it improves understanding.
- Show why a task is waiting and which upstream operation can resolve it.
- Support upstream block kinds and required-input metadata using plain labels.
- Support request review, approve/requeue, approve/complete, and reviewer
  summaries without introducing new columns.
- Build an activity timeline from durable comments, task changes, worker
  lifecycle, schedule events, and run summaries.
- Never fabricate activity by comparing browser snapshots.

Acceptance: dependency cycles are rejected, linked completion releases eligible
work, blocked/review detail is understandable, and every destructive/review
decision has durable attribution.

## Slice C — Execution configuration

- Profile/assignee picker explains eligibility and uses profile descriptions.
- Skills picker shows installed skills and preserves native resolution.
- Workspace presets explain:
  - scratch: isolated temporary work;
  - directory: work in a selected permitted path;
  - worktree: isolated Git worktree with cleanup policy.
- Add goal mode, priority, tags, model/provider override, and other stable
  upstream execution fields under Advanced.
- Validate paths beneath permitted profile/workspace roots and reject traversal
  and symlink escape.
- Show effective configuration before dispatch; do not expose environment
  variables or provider keys.

Acceptance: a task configured through the UI produces the same effective
upstream task as the equivalent supported CLI command and respects approvals,
profiles, workspaces, and model routing.

## Slice D — Worker operations and run inspection

- Show active worker, start time, safe progress summary, last heartbeat, and
  whether recovery is needed.
- Stream invalidations rather than raw tool output.
- Add run history and completion summaries to the task drawer.
- Add sanitized log tail/watch only on demand with bounded size and explicit
  reconnect state.
- Put terminate and reclaim behind confirmation that explains the resulting
  task state and possible side effects.
- Use upstream diagnostics/recovery/GC operations; do not implement process
  killing or database repair independently.

Acceptance: active state updates without refresh, stale workers are detected
using upstream rules, terminate/reclaim is auditable, log responses are bounded
and redacted, and restart recovery does not double-run a task.

## Slice E — Specification and decomposition

- Offer “Break into tasks” for a complex backlog/todo item.
- Preview proposed children, dependencies, profiles, and skills before creating
  them unless explicitly running upstream automatic decomposition.
- Support manual specification and auto-decomposition settings per board using
  upstream limits.
- Make parent/child progress readable without creating a new workflow status.
- Provide cancel/retry and partial-result behavior.

Acceptance: decomposition is idempotent, respects board/profile settings and
limits, never loses the original task, and produces an understandable
dependency structure.

## Slice F — Notifications and advanced orchestration

Only implement when the pinned public API and delivery model are stable:

- task/board subscriptions and native notification targets;
- advanced dispatcher settings;
- swarm/workflow orchestration fields;
- local tenant/group labels if useful for organization.

Keep these in Settings/Advanced. They must not complicate quick create or the
five-column board. A local tenant label is not an authorization boundary.

## Security and reliability checklist

- No API or event payload leaks stored paths, secrets, prompts, request bodies,
  tool arguments/output, or unredacted logs.
- All board, attachment, workspace, and profile identifiers use upstream or
  Brain4All path validation.
- Upload size/type/name rules and download content disposition are tested.
- Bulk actions have bounds, preconditions, and per-record outcomes.
- Destructive board/worker/GC actions have exact targets and confirmations.
- Dispatcher, optional notification delivery, and diagnostics failures do not
  restrict unrelated local use.
- Native Hermes CLI/tool changes and Brain4All UI changes remain immediately
  consistent through the one database.

## Documentation

Before the final parity release:

- document common workflows for non-developers;
- document the five-state projection and detail badges;
- document default-board automation and legacy cron migration;
- document advanced worker/recovery controls;
- document CLI interoperability for developers;
- update architecture/API/development references;
- remove or explicitly supersede obsolete mock Kanban and standalone cron docs.

## Definition of done

Each slice has focused backend/UI tests and a real-stack browser journey. The
full plan is done only when the master checklist and `plans/VERIFICATION.md`
pass, all exposed features use supported pinned Hermes operations, and every
excluded Hermes capability has a recorded rationale and safe CLI alternative.
