# 002 — Kanban and list product experience

Priority: P1. Requires the plan 001 API and event contract.

## Goal

Replace the current in-memory draft with a calm, accessible task experience
that works for non-developers without hiding agent execution detail when it is
needed. Board and list are two views over the same records and operations.

## Product principles

- Start simple: a title and clear worker description are required; assignee is
  optional.
- Use familiar language: “Needs input” is primary; an internal `blocked` state
  may appear in supporting detail.
- Reveal execution controls only when requested.
- Never imply that a drag succeeded until the backend accepts the legal
  transition.
- Always explain waiting, failure, or inability to move in an actionable way.
- Keep board and list behavior, filters, selection, and URLs consistent.

Visual references may be taken from established Kanban products such as Jira,
Trello, Linear, and Lark for interaction patterns, but the implementation must
use the existing Brain4All design system and must not copy protected visual
assets.

## Information architecture

### Left navigation

The primary rail contains only:

- **Agents** — agent list, conversation history, new conversation, and the
  selected agent workspace.
- **Kanban** — default board, other boards, saved filters, board/list view.

A Settings gear remains anchored at the bottom as a utility action. Do not
present Settings as a third daily-work destination.

Move existing destinations into Settings:

| Current destination | Settings section |
| --- | --- |
| Agent details, profile, model | Agents |
| Skills | Agents > Skills |
| Runtime and 9router | Runtime |
| Connections | Connections |
| Teams | Agents > Teams |
| Data/import/export | Data and backup |
| Workspace and memory | Agents > Data |
| Cron defaults | Automations |
| General application options | General |

Keep existing deep links working with redirects or compatibility routing.
Settings sections may use internal tabs/search, but they must not reappear as
primary sidebar items.

### Kanban header

Include:

- board selector with a clearly marked default board;
- Board/List segmented view toggle;
- search;
- compact filter button with active-count badge;
- “Add task” primary action;
- overflow menu for board settings, archived tasks, diagnostics, and advanced
  actions.

Persist the selected view and non-sensitive display preferences locally. Keep
filter state in the URL so it is shareable and survives refresh.

## Board view

The Current view renders exactly four columns, in order:

1. Backlog
2. Todo
3. In Progress
4. Done

Archived work appears in a separate Archived view selected from the Kanban
header. It is not a fifth board column or a drag/drop target.

Each current-work column shows a count and an inline add affordance where
creation makes sense. The layout supports horizontal scrolling at narrower
desktop widths and uses a list-first layout on small screens.

Cards show only scannable information:

- title;
- assignee/avatar or “Unassigned”;
- priority only when non-default;
- up to two tags plus an overflow count;
- dependency, comment, and attachment counts when nonzero;
- schedule/next-run badge for an automation template;
- worker progress/heartbeat when active;
- state-detail badge when it explains waiting or action required.

Do not place raw model names, database statuses, IDs, cron expressions, or long
descriptions on cards by default.

### Drag and move

- Pointer drag provides a clear placeholder and valid-target indication.
- Keyboard users can pick up, move between columns/positions, and drop with
  announcements.
- Every card also has a “Move to…” menu; drag is never the only way.
- Archive is a distinct detail action with a product-styled confirmation; it is
  never offered in the move menu or as a drag/drop target.
- Optimistic placement remains visually pending until the API confirms.
- Invalid/stale moves restore the card and explain what changed.
- Moves that terminate active work or create a reopened follow-up require a
  preview/confirmation.

Within-column order must have an upstream-supported durable representation
before manual reordering is offered. Otherwise sort by a clear selected rule
and do not simulate persistence.

## List view

Use the same query and mutation layer as the board. Columns:

- task;
- status;
- assignee;
- priority;
- due/next run;
- updated;
- compact indicators for dependencies and worker state.

Support sorting, row selection, bulk operations allowed by plan 001/004,
keyboard navigation, and a mobile card-list rendering. Clicking a row opens the
same task drawer as a Kanban card.

## Task creation and editing

### Quick create

The modal or inline composer asks for:

- title;
- worker description;
- assignee (optional);
- schedule (optional, opens the automation flow in plan 003).

Default status is Todo for a ready piece of work or Backlog when explicitly
saved as an idea. Preserve the user’s unfinished input after recoverable API
errors.

After an agent is selected, show that agent's enabled skills as a checklist.
All enabled skills start selected; the user may disable any before creation.
Disabled or uninstalled skills are not offered.

### Task drawer

The drawer is deep-linkable and contains:

- title, description, fixed product status, assignee, priority, and tags;
- why the task is waiting and the next valid actions;
- dependencies/linked tasks;
- comments and attachments;
- automation schedule and occurrence history, when applicable;
- active worker summary, progress/heartbeat, and stop/reclaim controls;
- completed run summary and safe log access;
- activity timeline;
- advanced execution: profile, skills, workspace mode, goal mode, and
  model/provider override when supported.

Assigned tasks use the agent's durable profile workspace by default. Guidance
installed in each profile tells workers not to create proof, log, or demo files
for simple answers and to place explicitly requested deliverables in that
durable workspace.

Use plain-language help and safe defaults. Advanced fields are collapsed.

## Filters and bulk actions

Initial filters: fixed status, assignee, priority, tag, automated/manual,
active/needs-input, and archived. Search covers title and safe indexed metadata;
do not expose or log full prompt contents through an analytics path.

Initial bulk actions: assign, move when every selected transition is legal, and
archive. Show a preview with skipped items/reasons rather than partially
changing records without explanation.

## States and recovery

Design and test:

- first-use empty board with a short guided task;
- empty filtered result;
- skeleton loading without layout jumps;
- transient network error with retry;
- stale task updated by a worker;
- dispatcher degraded while manual organization remains available;
- individual board corruption/recovery diagnostics;
- no eligible assignee;
- worker heartbeat stale/failed;
- attachment upload progress/failure;
- event-stream disconnected/reconnecting.

Do not seed fake production tasks. Demo content belongs in tests or an explicit,
reversible onboarding action.

Remove all existing mock/demo and smoke-hook Kanban data paths from the
production bundle. Automated smoke and browser tests must create their own
records through the real Brain4All API, verify those records in Hermes SQLite,
and clean up only the test records they created. There must be no fallback from
a failed API request to in-memory sample data.

## Accessibility and localization

- Full operation without drag, mouse, hover, or color alone.
- Visible focus, logical focus return, and no keyboard trap in drawer/modals.
- Semantic headings, lists/tables, buttons, labels, descriptions, and error
  association.
- Polite live announcements for moves and worker updates; avoid noisy
  heartbeat announcements.
- Respect reduced motion and browser zoom; maintain usable touch targets.
- Test contrast for priority/status/badge combinations in light and dark
  themes.
- Put every string, date, relative time, count, and schedule description through
  the existing i18n system and update every shipped locale.

## Frontend implementation sequence

1. Add typed API models/client and replace `src/api/kanban.ts` memory state.
2. Refactor `useKanban` into query/mutation/event concerns with rollback and
   cache invalidation.
3. Establish the fixed status types and shared state-detail labels.
4. Rework app routing and sidebar/settings information architecture.
5. Implement the common task drawer and quick create.
6. Implement board view, move controls, and accessible drag/drop.
7. Implement list view and shared filters/selection.
8. Add comments, dependencies, attachments, worker/run, archive, and
   diagnostics panels as their APIs become available.
9. Remove mock/demo-only status, board, and cron UI paths.
10. Remove smoke-test data injection hooks; update smoke coverage to use the
    real API and persisted Hermes records.

Split components by behavior and reuse existing design primitives. Avoid a
single growing `KanbanView.tsx`.

## Tests and acceptance criteria

Use an API test double for focused UI states and the real stack for browser
acceptance:

- API serialization and all five status/detail mappings;
- board/list query parity and URL filter persistence;
- quick create, edit, assign, move success/rollback/stale conflict, and archive;
- keyboard move and task-drawer focus lifecycle;
- comments, dependencies, attachments, and worker/run rendering;
- navigation redirects and every Settings destination;
- empty/loading/error/reconnect states;
- responsive widths, reduced motion, and accessibility scan;
- every locale builds without missing keys;
- `npm test` and `npm run build`;
- the relevant journeys in `plans/VERIFICATION.md`.

The plan is complete when a non-technical user can create, assign, follow,
review, finish, find, and archive a task without needing to understand Hermes
statuses or CLI commands.
