# HTTP API

FastAPI generates the authoritative interactive contract at
`/xnobrain/api/runtime/swagger_docs` and JSON schema at `/xnobrain/api/runtime/openapi.json`.
Hermes CLI native routes remain available under `/api`; XNOBrain's
application API is exclusively namespaced beneath `/xnobrain/api/runtime/v1` and is
grouped by feature:

- `/xnobrain/api/runtime/v1/agents`, `/profiles`, config, skills, memory, workspaces,
  MCP, providers, and cron
- `/xnobrain/api/runtime/v1/conversations` for history and structured SSE runs
- `/xnobrain/api/runtime/v1/analytics` for UTC usage series and per-agent weekly budgets
- `/xnobrain/api/runtime/v1/teams`, `/bundles`, `/notifications`, limits, and deployment
- `/xnobrain/api/runtime/v1/sandboxes` for local runtime information/setup

JSON responses use `{success,data,message,status_code}`. SSE sends structured
Hermes lifecycle objects and terminates with `data: [DONE]`.

The namespace and current version are defined once in
`xnobrain/routes/definition.py`. Each feature owns a route module in
`xnobrain/routes/` and a matching operation module in
`xnobrain/handlers/operations/`; `routes/setup.py` only assembles those
groups. Business logic remains in the feature services under
`xnobrain/services/`.

Runtime statistics are available as a snapshot at
`/xnobrain/api/runtime/v1/sandboxes/detail` and as one-second SSE updates at
`/xnobrain/api/runtime/v1/sandboxes/detail/stream`.

Agent execution activity is available as a compatibility snapshot at
`/xnobrain/api/runtime/v1/agents/activity`. UI clients should use the
`/xnobrain/api/runtime/v1/agents/activity/stream` SSE endpoint, which emits an
`activity` event initially and whenever an agent changes between `idle` and
`running`. The runtime caches the database-backed Kanban portion for ten
seconds so status monitoring does not continuously scan every board.

Agent budgets use `weekly_usd` at
`GET|PUT /xnobrain/api/runtime/v1/analytics/agents/{agent_id}/budget`. The
minimum configured limit is USD 1; clearing the value restores the USD 20
default. Weeks run Sunday 00:00 through the following Sunday 00:00 UTC, and
the response returns both boundaries as RFC3339 timestamps. A new conversation
run or team task is accepted only while every participating agent has
`spend_usd < weekly_usd`. The check happens once at top-level execution
acceptance; event streams, ordinary API routes, and internal team/sub-agent
calls do not repeat it. Rejection uses HTTP `429` with code
`weekly_budget_exceeded`. An accepted execution is never stopped mid-run when
it takes usage over the limit.

Runtime analytics read only the current profiles' Hermes `state.db` ledgers.
Runtime does not open a router SQLite database or call router management usage
APIs. Central personal/organization limits and durable usage history are served
by Control from the centralized router data path.

Provider connections, credentials, connection tests, and organization limits
are administered through the authenticated Control API. Runtime exposes no
provider-connection mutation routes and its API key cannot be used with
router management paths.

`GET /providers` and `GET /providers/{provider_id}/models` are read-only views
of the model catalog authorized for the current workload. Runtime obtains that
catalog from the centralized router's OpenAI-compatible `GET /models` endpoint.
`GET /providers/{provider_id}/models/{model}/reasoning` returns those same
provider/model-specific values plus `default_reasoning`; model IDs containing
`/` are supported. `default_reasoning` is derived from the live ordered model
catalog: the second supported level is preferred, the only level is used when
there is one, and models without reasoning metadata return `auto`. Agent and
Smart Route reasoning may be stored as `auto`; concrete model execution
resolves it through this metadata instead of a hard-coded effort.
Smart Route blends resolve at model-inference boundaries rather than only once
for an entire agent run. The initial user turn is classified once and reused;
later model continuations after tool results, goal-continuation prompts, and
delegated tasks are classified independently. Mixed-difficulty delegation
batches are grouped into cost-homogeneous execution waves, while explicit
delegation provider/model overrides remain authoritative. If a later routing
classification is temporarily unavailable, the active model continues instead
of failing the run. Supported reasoning values come from provider model
metadata and may include `none`, `minimal`, `xhigh`, `max`, and `ultra` in
addition to `low`, `medium`, and `high`.

## Safe marketplace package export

`POST /xnobrain/api/runtime/v1/marketplace/agents/{agent_id}/export` builds a
typed, deterministic publication package from the selected existing local agent.
The request is `{"license":"MIT"}`; the response data contains the Control
candidate fields `definition`, `requested_permissions`, `compatibility`, and
`license`, plus `schema_version`, `source_agent_id`, a canonical `sha256:`
`digest`, an ordered file manifest, enforced limits, and an exclusion report.
The digest covers the definition, requested permissions, compatibility, and
license using sorted compact UTF-8 JSON, matching marketplace version digest
material.

The export allowlist contains required `SOUL.md` and `workspace/AGENTS.md`,
enabled skill `SKILL.md` files, and UTF-8 regular files in each skill's
`references/`, `scripts/`, and `assets/` directories with approved extensions.
Only display name, description, model slot, and reasoning effort are copied
from profile configuration/metadata. Tool and MCP names are declarations; MCP
URLs, commands, headers, environment and other connection details are not
exported. Runtime grants no requested permission as a side effect of export.

The export is bounded to 202 files, 1,000,000 bytes per file, 10,000,000 total
bytes, 100 skills, 100 safe skill assets, and path depth 12. It rejects missing
required definition files, invalid UTF-8, non-regular files, symlinks anywhere
in the skills tree, path escapes, unsafe/case-colliding names, oversized input,
and common credential patterns in otherwise public content. It never traverses
or exports `.env`, credentials, provider/MCP connection values, `USER.md`,
memories, conversations/history/sessions, state databases, logs, caches, or
private workspace files. This endpoint is intentionally separate from the full
portable profile export and does not modify marketplace installation flow.

Portable profile example:

```bash
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/export \
  -H 'Content-Type: application/json' \
  -d '{"agent_ids":["agent-id"]}' -o profiles.zip
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/inspect \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/dry-run \
  -F file=@profiles.zip
curl -fsS -X POST http://localhost:5173/xnobrain/api/runtime/v1/bundles/apply \
  -F file=@profiles.zip
```

All application endpoints are local; the project does not expose Enterprise
proxy, device-pairing, dashboard, or observability routes.


## Immutable conversation ownership

`POST /sessions?agent=<agent-id>` (and the hidden legacy `/conversations` alias)
accepts an optional typed `ownership_context`. Omission creates an explicit Personal
binding. Non-Personal bindings require the Control-verified private facade identity;
direct callers fail closed. The immutable context is returned by create, list, detail,
goal/subgoal, and durable run responses. Existing unlabeled Hermes sessions are
atomically backfilled Personal on first access. Run bodies cannot override owner,
organization, context, payer, or sponsor grant. See
[`contracts/conversation-ownership-v1.md`](contracts/conversation-ownership-v1.md).

## Unified workspace event stream

`GET /xnobrain/api/runtime/v1/events/stream` is the preferred browser SSE
subscription for shared workspace state. It multiplexes three event families:

- `agent.activity` — `{agents, updated_at}` activity snapshots;
- `workspace.stats` — the existing sandbox detail/VM status projection;
- `kanban.connected` and `kanban.task` — default-board connection and task events.

The Runtime starts one concurrent producer for each family and fans them into a
bounded queue. Disconnecting the SSE response cancels every producer. The
legacy `/agents/activity/stream`, `/sandboxes/detail/stream`, and
`/kanban/boards/{board_slug}/events/stream` endpoints remain available for
backward compatibility, but new browser code should use the unified stream.

## Organization artifact workspace adapter

The private Runtime exposes inspect, approved publish, and verified import operations under `/xnobrain/api/runtime/v1/agents-workspaces/{agent_id}/organization-artifacts`. Transfer capabilities are supplied ephemerally by Control. Imports reject unsafe paths/protected profile areas, stage bytes, enforce size/SHA-256, and atomically rename into the selected workspace. Publish requires `approved: true` and never returns a local absolute path.

### Automatic model routing

Selecting **Auto** for a provider randomizes the workload-scoped connected model
catalog for that provider. Selecting the system **Auto** blend randomizes all
connected models across providers. The first candidate is used for the run and
the remaining unique candidates are attached to the embedded agent's bounded
fallback chain over the same authenticated router transport. Model-not-found,
quota/rate-limit, malformed-response, and supported transient provider failures
can therefore advance to another candidate. Explicit models and user-created
blend strategies retain their existing behavior.

## Agent Maker trusted blueprint lifecycle

Runtime exposes owner-profile-local Agent Maker records under
`/xnobrain/api/runtime/v1/agent-blueprints`; every route requires
`?agent=<creator-agent-id>`. `GET` lists that creator's records newest-first,
`POST` creates a draft without creating a child profile, and
`GET /{blueprint_id}` resumes the exact persisted revision. `PATCH /{blueprint_id}`
uses `expected_revision`, invalidates prior approval, and rejects stale writes with
`blueprint_revision_conflict`. Records and mutable lifecycle operations use atomic
profile-local JSON below `.xnobrain/agent-blueprints`.

Personal drafts use `work_context_id: "personal"`. Any other work context must equal
the independently signed conversation ownership context supplied by Control; an
unsigned or mismatched context is rejected.

`POST /{blueprint_id}/approvals` accepts `approve` or `deny`, the current revision,
and the exact canonical digest. The actor always comes from Control's verified
principal headers; caller-supplied `approved_by` is forbidden. Approval binds
content, permissions, model, context, target profile, and revision. Denial is
terminal and records the authenticated actor and optional reason without creating a
profile.

The remaining trusted operations require the verified actor plus
`expected_revision`, `canonical_digest`, an `idempotency_key`, and the exact
operation `decision` (`scaffold`, `activate`, or `cancel`):

- `POST /{blueprint_id}/scaffold` requires current approval. It stages and
  atomically publishes exactly one generated child at the server-reserved target.
  The child is `draft` and paused, with manual protected-write approval, cron and
  MCP disabled, no state database/sessions/logs, no provider credentials or custom
  base URL, and only blueprint-pinned skills/memory seeds. It never clones the
  creator profile or global skills. An occupied or drifted target fails closed.
- `POST /{blueprint_id}/activate` remains a separate authenticated decision, but
  currently fails closed with HTTP 409 `blueprint_certification_required` for a
  scaffolded child. Approval and repeated activation requests cannot substitute for
  child-path certification. The blueprint and child remain unchanged and paused;
  cron and MCP remain disabled. Bounded child-path certification and digest-bound
  activation remain pending. Previously recorded activation operations retain their
  idempotent read behavior; this does not retroactively modify existing profiles.
- `POST /{blueprint_id}/cancel` prevents future blueprint steps and retains any
  paused draft profile for explicit cleanup. It never deletes files implicitly; an
  active profile must use the separate profile lifecycle.

A replay with the same operation key, revision, and digest returns the persisted
result. Reusing a completed lifecycle with different idempotency material returns
`blueprint_idempotency_conflict`. Digest, revision, approval, target collision, and
profile-drift conflicts fail without widening permissions or activating a profile.

## Skill lifecycle usage

`GET /xnobrain/api/runtime/v1/agents/{agent_id}/skills/usage` accepts the
existing `days` or `from`/`to` UTC range parameters plus additive `context`,
`cursor`, and `limit` (1–100) filters. Omitting the additive filters preserves
the personal-context behavior and historical `skill_view` fallback when no
lifecycle event log exists.

New embedded runs write only allowlisted metadata beneath
`profiles/<agent-id>/skill-usage/v1/events/`: stable event ID, lifecycle type,
agent/context/run/session IDs, skill ID and `sha256:` digest, attribution,
timestamp, duration/outcome, and tool name. Prompt text, skill content, tool
arguments/results, credentials, and absolute paths are never written. Stable
event IDs make duplicate delivery idempotent. The response distinguishes
requested, loaded, reference-read, tool-invoked, completed, and failed events;
tool provenance is explicitly multiple or unattributed where it is not
singular. Coverage reports the source, selected range/context,
instrumentation version, event count, and unattributed tool count. Historical
`state.db` inference remains labeled `observed_load_only` and does not
manufacture tool or error metrics.

### Historical skill-usage correction

`GET /agents/:agent/skills/usage` distinguishes historical assistant requests
from measured lifecycle events. Historical rows now use coverage attribution
`historical_requests`, `instrumented: false`, and item attribution `estimated`.
`requested_count` counts `skill_view` requests; `distinct_sessions` counts their
sessions. `loaded_count` and `distinct_runs` are nullable and are **null** for
history without corroborated completion/run identities, never fabricated zeros.
The date window applies to message timestamps, not session start times. No tool
payloads are returned. Event-backed metrics retain their measured numeric fields.
Consumers must handle nullable metrics and the new attribution values; older UI
versions suppress uninstrumented history, so coordinate the updated UI for the
historical request display. This does not establish mixed-version verification.

### Concurrent composer request collection (FT0010 foundation)

Chat requests accept optional `capabilities`, a unique array drawn from `todo`,
`delegate`, and `goal` (maximum three). All eight subsets are valid. Runtime
normalizes order to Todo, Sub-agents, Goal. The legacy scalar `feature` remains
supported, including historical Learn/Maker/Optimize values. If both fields are
supplied, the array must contain exactly the scalar selection; ambiguity,
duplicates and unknown capabilities are rejected rather than silently dropped.
An absent/null array uses legacy behavior; an empty array explicitly selects none.

Preparation retains the complete collection in one parent request. The embedded
runner combines Todo/Delegate guidance in that parent's initial turn and creates
the existing goal when selected. No new per-selection parent dispatch is added.
This is a contract/runner foundation, not full FT0010: UI negotiation/selection,
shared child budget accounting, linked revision-safe todos, cancellation-tree and
real execution/reconnect compatibility tests remain required. Old servers reject
this new field; clients must not silently retry by discarding selections.

New run records also persist `composer_selection` with `schema_version: 1`, the
legacy `feature` (nullable), and normalized `capabilities`. The collection is copied
before scheduling, so later caller mutations cannot alter queued work. Reads of
older records may omit this additive field. Validation occurs before the budget
check and creation of a run record; selecting three tools still invokes the existing
parent budget check once. This is not shared child-budget enforcement.

Selecting Goal in a composer request creates a goal only when none exists (or the
prior goal was cleared). Existing objectives, turn counters, subgoals and paused/
terminal states are preserved. Replace/resume a goal through its explicit lifecycle
API rather than implicitly replacing it when selecting combined work tools.

New marketplace installation profiles use the full bounded installation-ID suffix,
not its former 24-character truncation. Existing truncated profiles are recognized
by persisted installation binding and are never silently renamed or duplicated.
Consumers must use returned `local_profile_id`, not derive it from installation ID.

## Calendar schedule preview (Time Control foundation)

`POST /xnobrain/api/runtime/v1/cron/schedule-preview` accepts a five-field `schedule`,
IANA `timezone`, optional offset-aware `after`, and `count` (1–20, default 5).
Returns UTC/local occurrences and `dst_policy=skip_gap_earlier_fold`. It performs
no job creation or timezone mutation. `executor_parity_verified=false` explicitly
means local scheduler integration is still pending; preview is not execution proof.

Cron blueprint instantiation (`POST /xnobrain/api/runtime/v1/cron/blueprints/instantiate`)
accepts optional `timezone` (available IANA identifier, default `Etc/UTC`). Calendar
blueprints persist the versioned per-job timezone/DST binding before native first-run
computation, matching direct cron creation. Interval/absolute schedules retain native
semantics. Ambiguous names such as CST reject before blueprint filling. The workspace
or organization default and explicit preview/confirmation UI remain separate work.

Direct cron creation rejects simultaneous `interval_minutes` and `schedule` with
HTTP 422 instead of silently preferring the interval. Calendar expression input is
bounded to 256 characters. Existing interval-only and schedule-only shapes remain.

Explicit-zone calendar evaluation rejects random (`R`) and hashed (`H`) cron fields:
preview and execution must use deterministic calendar expressions. Ordinary named
months/weekdays remain supported. This restriction does not rewrite legacy unbound
native schedules.

Timezone-scoped creation requires an explicit UTC offset on absolute one-shot
input (`2026-10-25T02:30:00+02:00` or a `Z` instant). Naive local timestamps and
bare dates reject instead of inheriting a conflicting process timezone. Relative
interval/delay inputs and existing unscoped native parsing remain unchanged.
Local one-shot timezone/disambiguation UI is not yet implemented.

Cron job readback includes additive `schedule_kind`: `cron`, `interval`, `once`, or
null for unknown persisted kinds. It derives from native stored schedule metadata,
not human display text. Consumers must retain unknown/mixed-version handling.

Cron readback also includes `interval_minutes`: a positive persisted integer for
interval schedules, otherwise null. Consumers should prefer this over display-text
parsing; absence on older servers permits compatibility handling, null is unknown.

## Private Runtime Time Control adapter (FT0013)

Control and its node gateway call these fixed Runtime-local endpoints:

- `GET /xnobrain/api/runtime/v1/system/time-control`
- `POST /xnobrain/api/runtime/v1/system/time-control/schedule-migration-preview`
- `POST /xnobrain/api/runtime/v1/system/time-control/apply`
- `POST /xnobrain/api/runtime/v1/system/time-control/schedule-migrate`

Every call requires `X-XNOBrain-Time-Token` equal to the workspace-scoped
`RUNTIME_INTERNAL_SERVICE_TOKEN`. Control derives that value with its
`RuntimeServiceToken`; the private gRPC relay injects the already-authenticated
Runtime token only for these fixed paths. Browser credentials and request-body
workspace fields do not grant access.

Observation reads the actual atomic `DATA_DIR/time-control/timezone.json` setting,
reports current UTC and zone-local observations, and identifies the Runtime and
per-job adapters. Apply binds the exact operation ID, positive maintenance fence,
`sha256:` plan hash, target zone, and expected persisted revision. It writes only
Runtime configuration beneath `DATA_DIR`, performs an immediate disk readback, and
journals the terminal result. It does not invoke a shell, alter the host clock,
write `/etc/localtime`, or change process-wide `TZ`; existing processes may need to
be reopened while Runtime scheduling reads explicit persisted zones.

Calendar schedules created by Runtime now carry an authoritative revision plus a
digest of their schedule definition. Inventory marks interval, absolute one-shot,
legacy timezone-less, externally changed, and unsupported-policy records ineligible
instead of guessing authority. Preview uses the same bounded calendar evaluator as
the executor and returns five target-zone occurrences. Migration revalidates each
selected revision while holding Hermes' native job-store lock, snapshots `jobs.json`,
changes only the calendar timezone binding/revision and future `next_run_at` after the
explicit UTC cutoff, and preserves run claims, in-flight execution ledger, completed
history, delivery records, repeat/misfire state, and output artifacts. Cross-item
atomicity is not claimed: every item returns `succeeded`, `stale`, `unsupported`, or
`failed`, and any non-success makes the operation partial.

`DATA_DIR/time-control/operations` and `fence.json` are atomically written journals.
An exact retry replays a terminal result; reuse of an operation ID with another
fence/hash/target/revision conflicts. A newer fence supersedes an older one, and stale
workers fail with `409`. Startup validates incomplete journals and leaves them
resumable by the exact Control retry; already committed settings and per-item schedule
operation markers make recovery idempotent without replaying execution history.

## Personal Agent Custom Page v1 (FT0015, in progress)

> Release exposure is controlled by the coordinated root flags `FT_ENABLE_UI_CUSTOMIZATION` and `FT_ENABLE_AGENT_CUSTOM_PAGE`; both default off. Disabled backend paths fail closed and retained data is not deleted.



The additive `/xnobrain/api/runtime/v1/agents/{agent_id}/custom-page` family serves
verified Personal workspace owners only. Managed requests use the existing Control
proxy and verified-principal facade. `agent_id` selects a resource, never identity.
The closed manifest schema, query inputs and activation models are exposed by
FastAPI OpenAPI through `xnobrain/models/custom_page.py`.

Stored queries are `POST .../queries/{query_id}` with bounded `parameters`, `search`,
`offset`, `limit`, optional `draft_revision`, and optional `expected_revision`.
The browser always supplies the latter; Runtime returns `409` rather than data
from a changed manifest. No query calls inference, collects sources or accepts SQL.
Group queries with `sort == group_by` honor ascending/descending label order;
otherwise groups sort by descending count. The `activity` tab ID is reserved.

`POST .../actions/{action_id}` requires an exact active revision, `RUN {action_id}`
confirmation, a verified owner's existing Personal `conversation_id`, bounded
`timeout_seconds`, and an idempotency key. It uses the existing budget admission
and durable parent-run service. The trusted dataset scope and page revision are
part of the run fingerprint and persisted record. The real engine tool registry
checks that scope, live run state and current app revision on every scoped call;
no activate/migrate/delete tool is exposed.

Draft ingestion, query validation, activation, maintenance admission and bounded
renderer behavior have focused source evidence. Scheduled updates, lifecycle job
shutdown and complete feature acceptance are still in progress; these routes do
not imply public sharing, organization apps or executable custom components.

### Explicit app-owned scheduled updates

`POST .../custom-page/schedules/preview` is a read-only preview of an active named
`action_id` and the owner's existing Personal `conversation_id`. It requires
`expected_revision`, a five-field `schedule`, explicit IANA `timezone`,
`payer_kind: "personal"`, `timeout_seconds` (10–300), and `max_runs` (1–100).
The result binds the action/page digests and verified owner, returns five UTC/local
occurrences and the existing `skip_gap_earlier_fold` policy. It also discloses a
20-model-turn maximum per occurrence and existing Personal budget admission.

`POST .../schedules` uses those same selected fields plus `digest`, `idempotency_key`
and exact `confirmation: "SCHEDULE {digest}"`. There are at most 100 retained
schedule bindings per app; deletion of the archived app removes them. Installation
first stores approval, then a paused inert native cron record, then arms it after
binding commit. An interrupted install reuses the exact record, never another job.

`GET .../schedules` returns approval/state/attempts/last status and the linked run,
not worker PIDs or credentials. `POST .../schedules/{schedule_id}/stop` requires
`confirmation: "STOP {schedule_id}"`; it revokes future work, pauses native cron,
and cancels only its linked conversation run. `409 custom_page_jobs_active` means
shutdown is not yet proved; retry status/Stop rather than delete data prematurely.
Another live process or uncertain executor keeps lifecycle operations fenced.

Native cron owns claims, recurrence, DST, output and execution history. Reserved
`xcp_` jobs are `no_agent` with no script, so absence of the adapter cannot spawn an
unrestricted agent. Only an existing signed owner-bound app record authorizes the
executor. Native/manual edits cannot change datasets, page revision, timezone,
payer or occurrence caps. The adapter rechecks these at dispatch and the app tools
recheck them during work. Failed/interrupted claims are not a retry queue. Successful
work remains subject to existing budget admission on every new occurrence.
Scoped actions expose only inspect/query/write and established web search/extract
tools; no terminal, file, code, delegate, cron or self-approval tools. General app
preparation continues through the explicitly requested interactive agent run.

App archive durably stops future schedule bindings and pauses native jobs; active
or uncertain app execution blocks archive/delete/migration. App export includes
schedule approvals and retained status without process metadata. These are source
contracts; remote deployment and full FT0015 acceptance remain separate gates.

## Control-mediated layout assistance (FT0009)

`POST /xnobrain/api/runtime/v1/ui-assistance` accepts the bounded `AssistLayout`
contract (schema 1, request/layout IDs and base revision, context, agent and existing
conversation, deadline, approved catalog, current declarative layout). The facade
requires verified subject/tenant; Runtime checks the persisted conversation creator,
active owner context and matching Personal/org scope before run admission. The
original payer remains unchanged; no conversion of organization data into a Personal
page. This operation stages layouts only; it never updates Control preferences.

`GET /ui-assistance/{id}` returns bound identifiers/run state and a staged layout
only after successful completion. `POST /ui-assistance/{id}/cancel` keeps a tombstone
before cancelling its exact run, preventing a delayed start from executing after
cancellation. Observing does not redispatch; repeat start reuses the same receipt.
Staging lives in `DATA_DIR/ui-assistance/<owner-hash>/`, capped at 100 requests/owner
and 64 KiB/read, expiring in 24 hours. These are not distributable profile assets.

Native tools expose only `ui_layout_catalog` and `ui_layout_propose` to the bound
parent; shell/file/web/delegate/activation tools are not injected. Proposal schema
and catalog are bounded, ID/slot bindings validated, and every tool call rechecks
live run, scope, expiry and cancellation. Ten model turns and the explicit 10–300s
wall deadline bound assistance; no new runner/process or source modification occurs.
Control separately retrieves and validates the result, creates a proposal, and
requires trusted user Apply. The Runtime response alone is never approval.

### Retained app lifecycle after agent removal (FT0015)

- `GET /agents/{agent_id}/custom-page/removal-check` returns `{app: PageState|null}`
  after verified Personal owner/profile and storage validation. Only a genuinely
  empty/missing app directory is `null`; foreign/corrupt/unsupported reads are errors.
- `POST .../remove-agent` requires an already archived/quiescent app, the exact
  `expected_revision` and `confirmation="REMOVE AGENT {agent_id}"`. It removes the
  writer profile/conversations and assigned tasks, retaining the app database,
  attachments, revisions and backups. It never combines archive or data deletion.
  Response: `{deleted:true, app_retained:true, active_revision:N}`.
- `GET /custom-pages/retained?offset=0&limit=50` lists archived apps whose profiles
  are absent, filtered by verified subject/tenant. It returns bounded safe metadata
  in `{items:[{agent_id,title,active_revision,updated_at}],next_offset:null|N}`.
  Limit is 1–100, offset 0–1000; discovery scans at most 1000 directory entries with
  a 2s scan budget (individual SQLite waits remain separately bounded). Exceeding
  the bound is unavailable, never a falsely complete empty list. Deletion cleanup
  tombstones are excluded. No foreign page title/manifest is returned.
- Archived retained apps remain available through the existing page/revision/query/
  activity/backup/export/attachment-read and separately confirmed delete endpoints.
  Creating/writing/activating/running still requires the existing agent profile.
  `PageLifecycle.expected_revision` now accepts 0 so never-activated drafts can be
  explicitly archived/deleted/retained too.

Agent removal writes an app-local pending/completed journal. Completed replay cannot
remove a replacement profile (`409 custom_page_agent_replaced`). Interrupted removal
with a still-present profile is `409 custom_page_agent_removal_interrupted`; it needs
operator review, not automatic destructive replay. Missing-profile recovery completes
only the journal. The repository lock fences same-process draft creation/removal;
late prepare rechecks the profile under that lock. This is not a claim of complete
cross-process lifecycle/dispatch race verification.

Generic agent deletion remains available for agents without app data, but rejects
nonempty app storage (including damaged DB/backups) and observed live agent work.
The UI must use separate archive/retain/data-delete confirmations. `custom_page_*`
and `ui_composition_*` dispatch successes/handled failures are `private, no-store`.
No new service, browser identity authority, paid action or public sharing is added.

### Cross-process custom-page lifecycle gates

App storage now uses a stable kernel `flock` at `DATA_DIR/.custom-page-storage.lock`
in addition to the existing local RLock. It serializes app creation, SQLite
transactions, backup, directory rename/delete and the retained-removal journal across
Runtime processes. Waiting is bounded (2s local-lock and 2s file-lock acquisition;
SQLite/query budgets remain separate); contention returns the existing safe busy
error instead of accessing partly replaced files.

Per-agent execution lock files and per-conversation writer lock files also live
outside deletable profile/app directories, with hashed resource IDs, no content,
`O_NOFOLLOW`, close-on-exec and regular/single-link validation. Do not remove these
coordination files while Runtime processes are live. A shared execution lease is
acquired at final run admission after budget awaits; profile presence, maintenance,
dispatch guards and the conversation are rechecked before persistence. Archive,
destructive migration, app delete and agent removal require exclusive access.
Native schedule install/arm holds a shared lease until its dual-store reconciliation
returns. Kernel release on actual worker exit—not elapsed time—allows a later retry.

The original embedded adapter's `_profile_scope` enters a separate execution lease
inside its actual executor thread. Cancelling the awaiting coroutine cannot release
that lease while the thread is still executing. Late workers also check existing
profile and nonterminal/noncancelled app run state before entering. Registry app
calls hold the storage lock across validation and their data operation, preventing a
scheduled Stop from committing between authorization check and record write.

A second process does not mark a run interrupted merely because it is absent from
its in-memory registry: any live agent execution lease keeps the run nonterminal.
Cross-process cancellation reports an unavailable executor instead of claiming
cancellation. Actual process death permits existing stale-run repair without replay.
This is conservative per-agent coordination, not distributed run migration or an
approval to run multiple production Runtime processes against network-file SQLite.

Unverified boundaries remain: installed filesystem/mount replacement semantics,
malicious native code with direct filesystem access, broad update-drain participation,
upstream non-adapter execution paths and interrupted-removal operator recovery. Kernel
locks are cooperative lifecycle synchronization, not a sandbox against local shell.

### FT0009/FT0015 participation in Runtime update drain/checkpoint

A content-free workspace admission lock and activity lock now coordinate custom-page
SQLite/backup/lifecycle operations, staged layout-assistance records and admitted
conversation/native-adapter executor leases with the existing forward-only updater.
New mutations read the persisted maintenance fence under admission serialization;
updater instances no longer rely on their startup copy of `dispatch_paused`.
Already admitted activity remains visible until its actual transaction/thread exits.
New nested mutations during draining are denied; read-only access remains possible
outside the checkpoint/post-verification exclusive window.

`drain.active.workspace_activity` is an additive **0/1 busy indicator**, not an exact
worker count. Its value participates in `active.total`. Foreign processes or cancelled
coroutines with live executor threads therefore prevent a successful drain/checkpoint.
A bounded nonzero/unknown state fails closed; cancellation of local tasks cannot hide it.
Checkpoint/post-verify hold activity exclusively across SQLite flushing and manifests.
Storage work runs off the event loop; command/activity locks are retained until an
already submitted worker exits even if the observer cancels. A separate kernel update
command lock serializes different updater instances and processes.

SQLite `wal_checkpoint(TRUNCATE)` busy results now fail checkpoint rather than being
reported as flushed. Valid empty coordination files at exact root-level allowlisted
names are excluded from durable content digests; symbolic/hard links, special files
and nonempty reserved files are rejected, not hidden. App databases/backups/attachments/
revisions and UI-assistance records remain in the manifest. This prevents newly created
empty lock inodes after process replacement from falsely indicating data corruption.

Maintenance reads use no-follow/nonblocking opens, size bounds (16KiB for maintenance),
regular/single-link checks, duplicate-key rejection and typed fence-state checks.
Only absence means no maintenance; corrupt, unreadable, FIFO or symlinked journals do
not enable work. Reading an abandoned conversation during maintenance does not perform
a hidden stale-run repair/write; existing repair resumes only after verified resume.

These are cooperative source-runtime guarantees. They do not make arbitrary native
shell code obey locks or certify actual Incus/Docker volume/image replacement. Other
native execution paths retain their existing update drain checks and still require
separate end-to-end production acceptance.

### Installed custom-page capability negotiation

`GET /agents/{agent_id}/custom-page/capabilities` has a typed `PageCapabilities`
response and remains read-only (no app database creation). Existing schema/widget/
Personal/executable/query-cost fields are retained. Additive `operations` lists the
installed operation names, and `max_action_model_turns` reports the current 20-turn
named-action cap. `scheduled_updates` is true only when an existing writer profile
and the schedule service are available. Capability support is not authorization:
archive state, revision, ownership, maintenance and payer are rechecked on each call.

For an owner-authorized retained archived app without its writer, capabilities return
only read/preview/query/activity/backup/export/delete operations. Prepare/activate/
write/action/schedule are not advertised. The current UI negotiates before page reads;
a missing/incompatible endpoint shows explicit recovery rather than attempting every
operation. Legacy capability documents without the new operation list allow established
stored reads only, never inferred mutation support. Unknown future names do not install
code or grant new controls.

`xnobrain.tests.test_custom_page_journey` is the deterministic synthetic-news acceptance
fixture: actual engine registry + durable run service + signed HTTP + SQLite exercise
prepare/initial ingest/dedupe, explicit activate/replay, separate mock analysis action,
search/count/group/Markdown queries, provenance, attachment/backup/export and a fresh
spawned-process read. `XNOBRAIN_TEST_NEWS_FIXTURE_OUTPUT` optionally writes its synthetic
API results for source UI browser checks. It never calls a paid model or fetches news.

### Managed conversation creation replay (additive contract)

Control resolves `POST /sessions?agent=…` creation intents to a stable context and
server-generated conversation ID. The private signed context carries creation-only
`creation_intent` and `conversation_id`; the browser's intent must match the signed
value. Public stored ownership accepts optional positive `revocation_version` but
never exposes creation-only fields. Existing explicit signed ownership and legacy
Personal creation remain supported; old managed snapshots missing a bound intent
fail closed rather than silently creating a different session.

`DATA_DIR/conversation-creations/<agent-hash>/<session-hash>.json` retains one bounded
receipt (pending/complete/deleted plus exact-input fingerprint), separate from profile
exports/deletion. No plaintext token/title/identity is stored in the receipt. Fingerprints
include subject/tenant/context/title and profile device/inode; profile replacement or
volume migration requires explicit recovery rather than replay against a replacement.
One per-agent OS lock serializes creation and receipt quota checks (10,000 retained
receipts per agent, no expiry eviction/replay). Existing execution/update gates protect
creation against cooperative profile deletion and checkpoint. Receipt files participate
in update manifests; exact empty lock files are coordination only.

Matching explicit replay recovers the same native session after a lost response or
pending insert; changed input is 409. Completed/deleted or removed context is never
recreated (410). A pending unknown native failure keeps its binding, not a Personal
backfill. Reads or creation replay never start an inference run. New creation without
an intent remains non-idempotent and must not be blindly retried. This is cooperative
Runtime synchronization, not a filesystem sandbox against arbitrary same-user code.

### Default root-profile custom pages and tenant-bound admission

`XNOBrainApplication` explicitly passes the configured root profile to FileRepository.
`live_profile_path` resolves writer presence for the protected default agent there;
other agents remain under the validated profiles directory. This does not relocate
legacy conversation-run persistence. Root-agent custom pages use the same separate
app database, but capabilities do not advertise protected-agent removal and direct
removal is denied before journaling. Runtime assistance responses now include
`Cache-Control: private, no-store` just like custom-page reads.

Explicit signed ownership bindings now retain `actor_tenant_id`. Run admission,
layout assistance and custom-page actions deny a different tenant even for the same
subject. Historical bindings without that field are not silently assigned a tenant;
their legacy workspace-owned behavior is not a multi-tenant migration guarantee.

### Interrupted retained-page writer removal recovery

`GET /agents/{agent_id}/custom-page/removal-recovery` returns an authorized,
read-only `RemovalRecovery` with `state=none|recoverable|blocked|complete`, opaque
`operation_id`, `expected_revision`, `profile_state=original|missing|replaced|unverified`,
`next_action=none|remove_original|finalize`, and a plan `digest` only when recoverable.
No inode/device/private path is exposed. GET never resumes or deletes anything.

`POST .../remove-agent/recover` takes exactly operation ID, expected revision, digest,
and `confirmation="RECOVER REMOVAL <digest>"`. Recovery is separately approved, not
an automatic retry of initial removal. It rechecks app owner/archive/revision, jobs,
OS lifecycle gate and the original profile identity. An absent original profile
requires a new finalize-only plan; a changed/replaced profile or old journal without
identity stays blocked. Recoverable original-profile cleanup may repeat idempotent
agent task/registry cleanup but does not affect the retained app DB/attachments.
A completed exact recovery replay returns its receipt without another deletion.

The app SQLite `agent_removal` journal gains nullable operation/profile identity/
expected revision/recovery digest columns only during a new removal. Existing legacy
pending journals are readable but not guessed into recoverable operations. A pending
journal with a now-missing profile is completed only via explicit recovery, allowing
registry/cache cleanup after interruption. These gates cooperate with Runtime tools
and processes; they do not sandbox arbitrary same-user filesystem modification.

### Required persistent mount contract

`RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT`, when configured, is an absolute operator-owned
mount point that must contain `DATA_DIR`. Linux `/proc/self/mountinfo` must show it
as the actual covering, writable local mount; supported filesystem types are
ext2/ext3/ext4, XFS, Btrfs, ZFS and F2FS. Missing/relative/symlink/unrelated paths,
read-only, overlay/tmpfs/network/unknown filesystems and any nested mount beneath
`DATA_DIR` are rejected before repository directory creation. This is deliberately
not an arbitrary network-SQLite certification or automatic fallback.

The repository captures mount identity at initialization and rechecks it for app/
assistance storage, creation receipts, and update manifest/admission work. A changed
or removed mount requires restarting/recovering on the intended storage, not silently
continuing on a different backing filesystem. Configuration changes require service
restart. Unconfigured standalone/Incus root-filesystem installations keep existing
behavior; they do not gain a claim of verified dedicated-volume persistence.

Root Docker source Compose now sets this contract to `/opt/data` alongside its named
volume. Focused tests using temporary directories explicitly leave it unconfigured;
`tests/test_storage_mount.py` validates failures with controlled mount topology and
`xnobrain.tests.custom_page_volume` validates actual Docker mount state.

### UI composition named accents (FT0009)

Optional `accent` on a schema-v1 layout (and Control account preferences) is one
of `default|teal|blue|violet`. Omit it to inherit account/default behavior; explicit
layout `default` chooses the platform palette. No CSS/hex/URL/null is accepted.
Control's catalog advertises `accent_options`; current UI disables editing if absent.
Control revision/digest/undo includes the field; old absent-field digests are stable.
Older closed clients/runtime reject accented records as unsupported; coordinated
components are required, with no automatic field stripping or retry. Native layout
assistance catalog/schema advertises the same names and only stages a proposal.

### Declarative navigation and start-page fields

The schema-v1 layout optionally includes `navigation` with explicit `order` and
`hidden` arrays and a `default_page` string. Each array is bounded to 19 unique
built-in destination names. `home` and `settings` cannot be hidden. The approved
catalog exposes `navigation_options`/`default_page_options`; Runtime's narrow
layout catalog tool exposes the same sets. No actor/session/URL/permission value
is accepted. These fields participate in the existing proposal digest, revision
CAS and restore. Omission leaves legacy serialized layouts unchanged. UI applies
current context/role/feature availability after ordering, folds hidden entries
under More, and honors a start-page intent only at initial workspace entry or an
explicit context switch—not on Apply, direct feature links or Back/Forward.
Older closed clients/Runtime reject unsupported fields; no automatic stripping
or write retry is introduced. Coordinated source versions are required.

### Optional inspector placement (FT0009)

Schema-v1 account preferences and layout records optionally contain
`inspector_position: "right" | "left" | "bottom"` and integer
`inspector_height: 160..480`. Omission preserves prior serialization/digests and
platform defaults (Right, 280px); an omitted layout field inherits the account
setting. The catalog advertises `inspector_positions`. Unknown positions, null,
compound/string/boolean/fractional heights and out-of-range values are rejected.
No new API paths, SQL tables, raw CSS/URL permissions or authority fields are added.

The existing revision-bound proposal/Apply/history/restore and preference CAS flows
persist these fields in current JSON storage. Runtime's narrow catalog/propose tool
can stage placement but cannot Apply it. UI review shows exact before/after values;
old catalogs disable editing. Older closed clients/Runtime reject new fields instead
of silently stripping them; coordinated capability/version rollout is still required.
Width remains the existing `right_panel_width` field regardless of inspector side.
Device resize is a bounded unsaved override; mobile viewport caps are not persisted.

### Native schedule claim and finalization fencing — 2026-09-14

An approved app action remains bounded to 10–300 seconds; the native-to-async
bridge waits at most 330 seconds. App-owned native fire claims now have a 600-second
minimum TTL (an explicit larger caller value is preserved); ordinary cron claim
semantics are unchanged. The prior 300-second default was shorter than the bridge.
A longer TTL alone is insufficient: native output/history finalization can outlive
it, clocks can jump, and another worker must not clear the original claim or pause
a healthy schedule merely because its async executor already returned.

Runtime therefore takes a nonblocking, kernel-backed ScheduleDispatchLease before
claiming an app-owned job, keyed by canonical agent plus reserved schedule ID. The
same descriptor fences native claim → approved async run → output/history/mark →
reconciliation. A competing worker returns not-fired without changing native or
SQLite state or admitting a second model run. Different schedules/agents are not
serialized by this exclusive lock. The associated shared execution/update activity
also prevents profile removal/archive/checkpoint through native finalization, not
only while the model coroutine exists. Release is in finally; actual process death
releases the kernel lease. Unknown durable occurrences still require explicit Stop
and fresh approval; an expired timestamp is never evidence that work was safe to
replay. Lingering engine work retains its existing executor lease.

The zero-content root coordination name is
`.custom-page-schedule-<sha256(agent + NUL + schedule)>.lock`. Never unlink it while
processes may hold it. Update manifests recognize only the exact reserved pattern
and validate regular, nonlinked, empty files before excluding these ephemeral locks;
nonempty spoofed coordination files still fail verification. Existing databases,
public paths, approval digests and schedules do not need migration. A mixed old/new
Runtime process set is not protected by a new cooperative lock the old process does
not acquire; coordinated process replacement remains required. This is not a
sandbox against arbitrary same-user shell/file writes and not a network lease.

### Native schedule-to-page acceptance and terminal outcomes — 2026-09-14

The source integration fixture now registers the actual Runtime application lifespan,
including its profile cron discovery loop. Test-only private endpoints may set an
already approved fixture schedule's due timestamp and script model behavior; they
do not call the schedule executor/action directly or re-enable stopped bindings.
These endpoints exist only in `xnobrain/tests/ui_assistance_server.py`, never in
production route assembly. The fixture explicitly disables the regular configured
gRPC listener and organization connector and uses its own disposable listener/data.

The browser helper `verify-custom-page-schedules.cjs` goes through real preview,
Personal conversation selection, exact paid-schedule approval, schedule status,
stored queries and activity. Native cron claims and runs due records via the existing
runner, then finalizes output/history. Verified scenarios: successful one-occurrence
cap, failure after a committed generated record, user Stop of an in-flight run,
and deadline expiry. Page refresh/tab changes/reload merely read stored data.

A schedule waiting on a cancelled child task now distinguishes that from cancellation
of its own observer. User Stop records the parent's durable `cancelled` outcome;
observer cancellation propagates after cancelling its owned run safely. Stop after
completion or retry preserves the last result and cannot re-arm a stopped binding.
Repeated finalization of the same result does not add `schedule_finished` events,
so Last completed schedule does not advance merely because the user clicked Stop.

Named custom-page actions now have the approved 10–300s deadline enforced by the
durable parent (as layout assistance already did), not just by the engine adapter.
Expiry marks `timed_out`, while explicit Stop stays `cancelled`. The schedule
observer waits the approved deadline plus five seconds of cancellation cleanup grace;
this does not extend the parent's execution budget. The outer native bridge and
kernel/durable lifecycle fences remain. A late executor thread still holds its existing
lease, and page tools reject writes once the durable run is terminal/cancel-requested.
Timeout/idempotent retry never starts a replacement run implicitly.

### Strict customization conversation identity — 2026-09-14

Managed FT0009 assistance, FT0015 actions/schedules and signed ordinary parent-run
admission now require an explicit stored `actor_user_id` and `actor_tenant_id`
matching Control's verified principal, the requested agent/session, and a non-legacy
binding. Missing/null tenant is not a wildcard. An explicitly stored empty tenant
remains distinct and matches only a signed empty-tenant standalone identity; it never
matches a managed nonempty tenant. Layout assistance still requires a managed tenant.
No read or failed execution automatically fills/reassigns legacy ownership.

Admission rechecks the exact owner/payer/context snapshot after asynchronous budget
checks, before recording/dispatching a run. New signed runs persist the same principal
in private run metadata; idempotent replay cannot borrow an unbound/foreign receipt.
Custom-page tool binding and every inspect/query/prepare/write call re-read the durable
run and current conversation, including active state, exact context and Personal payer.
Layout tools perform the corresponding context/principal recheck before catalog or
proposal work. Terminal/cancelled runs and stale/replaced/foreign bindings are denied.
The new actor fields are removed from public run start/read/active/stop/child/todo and
custom-page action responses; they do not expand the browser contract or enter layout
JSON. Existing error codes remain, with safe actionable denial text.

Signed session facade operations now enforce the same stored principal on detail,
messages, usage, goals, mutation, run-read/control and event replay. The paginated
conversation list filters unbound/foreign entries before DTO/history materialization;
pagination remains the underlying native page (a filtered page may be empty while
`has_more` is true). Approval verifies the exact run exists under the URL's session,
not merely a globally supplied run ID. Signed stream admission checks before SSE
headers and rechecks before emitting persisted events. Read/Stop can retain matching
historical access even when stored context is inactive; new work and approvals cannot.

Compatibility/security decision: historical unbound sessions remain on disk and the
explicitly local, unsigned standalone compatibility path is unchanged. The signed
managed directory excludes unbound sessions, and direct access returns 403 rather
than silently claiming them. Use a newly verified conversation for customization.
Automatic migration needs independently verified legacy ownership evidence and is
not implemented; it cannot be inferred from the user currently opening a workspace.
Old in-flight runs without actor metadata cannot use new customization tools. A
coordinated Runtime process rollout is required; old code does not enforce these rules.

These checks do not authenticate the public network themselves: Control and the
private transport must still require service identity and resolve the workspace.
They do not sandbox broad same-user engine shell/file tools, revoke an org grant
without a fresh server observation, or prove every unrelated Runtime route/export
is tenant-filtered. Those remaining authority boundaries are tracked in the audit.

### Agent Community export and installed-page lifecycle — 2026-09-14

Community publication exports a definition, not a private app or full profile:
SOUL.md, workspace/AGENTS.md, enabled SKILL.md files and allowlisted skill references,
scripts/assets. The canonical `DATA_DIR/agent-apps/<agent>/app.sqlite3`, backup and
SQLite attachments/records/revisions/receipts are outside this boundary. Profile
conversation contexts/runs, cron configuration/history/output and workspace exports
are not Community files. Reserved runtime path components (agent-apps,
conversation-runs, ui-assistance, runtime-updates, transfers, cron) are now omitted
case-insensitively even within skill reference/asset trees; SKILL.md beneath these
reserved trees is not promoted into a public skill. Existing private/hidden path
exclusions remain. Old legitimate skills using these reserved directory names must
be renamed/reviewed before publication. Existing exclusion categories/DTO/digests
are unchanged; they do not expose counts or identifiers from external app storage.

Every selected public file is checked as a regular single-link inode before reading.
A hardlink to private app export text cannot masquerade as an innocuous public asset;
symlinked skill/profile paths continue to fail. This is structural exclusion, not
content classification: deliberately copying private prose to SOUL.md or an ordinary
public skill file cannot be detected from arbitrary text. Publisher review and
broad-tool access policy remain required; this is not a shell sandbox or a guarantee
against malicious relabelling of private content.

Full portable-profile bundles are a separate explicit private backup/export surface,
not the Community publishing input. They preserve existing profile-state semantics;
managed DATA_DIR remains excluded even when nested under the default root profile,
so canonical app data is still exported only through its owner-authorized page API.
Do not treat a full profile ZIP as safe for public publication. Whole-profile export
identity/audit and explicit copied-data policy remain separate audit work.

Community install/update/uninstall now participate in the same per-agent lifecycle,
workspace-update and app storage locks as page mutation/removal. Update waits for
quiescence and changes only publisher definition fields; the customer's private app
is not an update target. Fresh install never copies app data. Reinstall to an ID with
retained/damaged app storage is denied instead of attaching a replacement writer.
Symlinked profile aliases are rejected before mutation so a lock on one ID cannot
protect mutation of another ID. Actual process execution prevents update/uninstall;
maintenance prevents mutation and errors release all acquired gates.

Uninstall without an app still moves the profile to recoverable trash. If any app
state remains (active/draft/archived, backup-only, corrupt or non-directory), it
returns the existing `custom_page_retention_required` 409 without moving/deleting
profile or page bytes. Unsafe symlink/mount state fails closed. The user must resolve
page data with existing separately confirmed lifecycle controls; Community uninstall
is not implicit archive/delete permission. After explicitly archiving and deleting
the owned app, normal uninstall can proceed. A combined Community retain-page
uninstall/control-reconciliation UX is not implemented by this safety check; the
existing custom-page retain/remove workflow remains distinct. This limitation is
not a claim of full cross-product lifecycle acceptance.

No new public paths/schema fields, migrations, upstream code, executable extensions,
images or production deployment are introduced. Mixed older Runtime code can lack
these guards, so coordinated process replacement is still required.

## OpenAI-compatible agent invocation

`POST /xnobrain/api/control/v1/agents/{agent_id}/chat/completions` is the
managed public endpoint. Control authenticates the bearer token, resolves the
caller's workspace server-side, and proxies to the private Runtime endpoint
`POST /xnobrain/api/runtime/v1/agents/{agent_id}/chat/completions`. The request
uses the OpenAI chat-completions shape; `model` must equal `{agent_id}` and
`messages` must contain text. `stream: true` returns OpenAI-compatible SSE
`chat.completion.chunk` objects followed by `[DONE]`.

Each request creates a retained Personal API conversation and uses the normal
agent run lifecycle, model policy, tool approvals, and authoritative weekly
budget admission. The endpoint does not accept workspace, Router, provider
credential, payer, or tenant selectors. The access token's verified user owns
the request. Responses may include additive `xnobrain` conversation/run IDs;
OpenAI clients can ignore unknown fields.


## Managed session usage

In managed Router-accounting mode, session usage reads request, four-component
token, and cost totals from GoRouter v0.2.2 through Control's private
canonical-user-key facade. Runtime reuses its Control HTTP connection pool and
returns both conversation totals and `weekly_budget` in the existing session
usage response. These two read queries run concurrently; the browser makes one
usage call and no duplicate pre-send budget call. Control uses `GET /admin/usage/summary?breakdown=none` rather than activity/timeline.
Runtime performs the authoritative weekly admission check immediately before new
top-level work, uses `gorouter-user-usage-v1`, and fails closed when
unavailable. Stored-only reports do not prove durable acceptance or complete
in-flight coverage.


### Worker accounting and per-agent week limits

Kanban uses the native dispatcher `spawn_fn` extension. Claims, PID recovery,
review/goal flags and completion remain engine-owned. The Runtime spawn adapter
checks the assigned agent's configured weekly limit against Router spending before
launch. It refreshes the managed provider URL and supplies the canonical user key
only to the child process; `big-brother` uses the root profile, not a legacy named
profile. Review and scheduled occurrences pass through the same admission gate.

Teams check participants before acceptance. Coordinator, workers, dialogue and
synthesis use the private accounted CLI bootstrap. Every model call carries
agent, native session and execution IDs; `parent_run_id` identifies the team run.
Large composed prompts use stdin instead of OS argv. Provider failures, empty
worker results and failed prerequisites cannot be reported as completed. No
arbitrary inference or side-effecting task is automatically replayed.

`GET /analytics/agents/budgets?agents=a,b` returns an object keyed by local agent ID.
Omitted selection means all current profiles (maximum 100). Runtime owns stored
weekly limits and revisions; Router owns recorded spending and quota-week bounds.
The batch uses one private Control call and two constant-count Router reads
(calendar-week anchor + grouped report), never one query per selected agent.
Budget errors keep configured limits visible but return null spending and
`status=unavailable`; no error becomes zero usage. Existing `PUT
/analytics/agents/{agent_id}/budget` edits the limit with revision checking.
Historical requests without correlation remain unattributed. Reports are stored
facts, not atomic reservations; admitted work may finish above its soft limit.

Worker lifecycle isolation: the shared host reaps only Kanban-owned child PIDs,
not `waitpid(-1)` across unrelated Teams subprocesses. Teams use standard `Popen`
with an asynchronously awaited communication thread, avoiding the observed
uvloop child-launch SIGSEGV in the multithreaded gRPC host. Cancellation and
timeout terminate/reap the child before publishing a terminal state.
