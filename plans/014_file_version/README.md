# 014 — Opt-in Workspace File Versions and Rollback

Priority: **P1**. This is a local runtime feature. It is disabled by default
and enabled independently for each agent profile.

Read this document completely before implementation. Also read
[`AGENTS.md`](../../AGENTS.md), every rule in [`../../.agents/rules/`](../../.agents/rules/),
and the upstream Hermes sources linked under [References](#references). The
Hermes source installed for development under [`.tools/hermes-agent/`](../../.tools/hermes-agent/)
is the implementation source of truth; the public guide is product-level
documentation only.

## Goal

Give users a safe, terminal-styled way to inspect and recover files changed in
an agent workspace:

1. A user explicitly enables restore points for one agent profile.
2. Before that agent changes workspace files, the runtime records a restore
   point without touching the workspace's real `.git` repository.
3. XNOBrain workspace mutations made through the UI are protected by the same
   setting.
4. Each workspace file row uses an overflow (`...`) menu instead of a directly
   exposed delete button.
5. The user can open, download, rename, copy the path of, inspect version
   history for, or delete a file.
6. The user can preview a diff and restore one file or the whole workspace.
7. A restore first records the current state so the restore itself is
   recoverable.

The feature is not a replacement for Git, branches, commits, cloud backup, or
conversation history. It is a local filesystem safety net for agent work.

## Product terminology and semantics

Use **Restore points** for workspace-wide history and **Version history** when
the view is filtered to one file. Do not describe every checkpoint as a new
version produced by one write.

Hermes records the workspace state immediately **before** a mutating operation.
It deduplicates checkpoints to at most one working-directory checkpoint per
agent turn. A single turn may therefore modify several files while producing
one restore point. The UI must not promise one snapshot per tool call or one
snapshot after every edit.

User-facing description:

> Restore points preserve this agent's workspace before files change. Preview
> or restore the entire workspace, or recover one file.

The initial implementation restores filesystem state only. It does not rewind
or delete XNOBrain conversation messages. Every restore confirmation must say
that the agent may still remember work performed after the selected restore
point.

## Scope

### Included

- Per-agent-profile `checkpoints.enabled` setting, default `false`.
- Automatic checkpoints already produced by Hermes before `write_file`,
  `patch`, and destructive terminal commands.
- Restore points before XNOBrain workspace create, write, rename, delete, and
  completed upload publication.
- Workspace-wide restore-point list and diff preview.
- File-specific version history derived from retained workspace checkpoints.
- Single-file restore and whole-workspace restore.
- Pre-restore safety snapshot.
- File-row overflow menu replacing the current trash icon.
- Empty, disabled, unavailable, busy, loading, error, and success states.
- Stable URL state for selected restore-point and file-version views.
- Retention through the existing Hermes limits and pruning behavior.
- Tests for profile isolation, path confinement, concurrency, API contracts,
  routing, accessibility, localization, and rollback.

### Not included in the first release

- Editing or rewriting the upstream Hermes checkpoint engine.
- Changing a workspace's real `.git` index, branches, or commits.
- Branching, checkpoint labels, merging, or comparing two arbitrary restore
  points.
- Rewinding conversation history, token accounting, or model context.
- Synchronizing restore points to cloud storage.
- Exporting checkpoint object data in agent portability bundles.
- Restoring a deleted file from a file-row menu after its row no longer exists;
  use the workspace Restore Points view for that case.
- User-configurable global storage and garbage-collection controls in the first
  UI. Use the runtime's bounded defaults.
- A recycle bin. Delete remains a confirmed filesystem mutation protected by a
  restore point when the feature is enabled.

## Verified existing behavior

The repository currently installs Hermes from its `main` branch in
[`Dockerfile.backend`](../../Dockerfile.backend). The vendored development
checkout already contains the v2 `CheckpointManager`.

Hermes currently:

- keeps one shadow Git object store and separate per-workspace refs/indexes;
- does not touch the working directory's real `.git` metadata;
- checkpoints `write_file` and `patch` before execution;
- checkpoints terminal commands classified as destructive;
- resolves file-tool paths through the task's effective working directory;
- deduplicates to one checkpoint per directory per conversation turn;
- excludes dependency/build/cache/VCS patterns, `.env*`, logs, archives, and
  common large media;
- skips a directory with more than 50,000 files;
- skips individual files above `max_file_size_mb`;
- takes a pre-rollback snapshot before restore;
- supports full and single-file restore, list, diff, pruning, and size caps;
- treats checkpoint failures as non-fatal so an agent tool still runs.

The API-server agent construction path already translates the active profile's
raw `checkpoints` configuration into `AIAgent` constructor arguments. XNOBrain's
`_profile_runtime_scope` makes named profile configuration available during a
run. Enabling one profile therefore must not require a global runtime switch.

Important gaps XNOBrain must close:

1. There is no XNOBrain HTTP contract for listing, diffing, or restoring Hermes
   checkpoints.
2. The public agent DTO does not expose checkpoint enablement or availability.
3. XNOBrain workspace create/write/upload/delete calls do not execute through
   Hermes tools and therefore are not automatically checkpointed.
4. The existing UI exposes delete directly and has no row action menu.
5. Hermes lists aggregate checkpoint metadata but does not provide the
   file-oriented DTO required by the proposed UI.
6. Hermes CLI rollback may undo a CLI chat turn, but the web/API restore path
   must not claim that behavior.
7. Restore and mutation operations need an XNOBrain-owned per-agent lock so a
   restore cannot race an active run or another workspace mutation.

## Configuration and persistence

Persist the opt-in switch in each profile's existing `config.yaml`:

```yaml
checkpoints:
  enabled: false
```

Rules:

- Missing `checkpoints.enabled` means `false`.
- New profiles remain disabled unless the user explicitly enables them.
- Enabling Agent A does not modify Agent B, Big Brother, or the root defaults
  used for future profiles.
- Big Brother is still a profile and may be enabled independently through its
  own settings.
- Disabling stops new automatic restore points. It does not immediately erase
  retained history.
- Do not copy an enabled value from the root profile into newly created named
  profiles. This feature always requires an explicit per-profile choice.
- Use Hermes defaults for the first release: 20 restore points per workspace,
  500 MB shared store cap, 10 MB maximum individual file size, automatic
  pruning enabled, and seven-day retention.
- Do not expose conflicting per-profile controls for the shared total-store
  size. The shared cap is a runtime policy, even though Hermes accepts it in a
  profile config object.

The current container resolves the shadow store below the root Hermes data
directory. All profile workspaces share its content-addressed objects while
their histories remain separated by the hash of the canonical workspace path.
Do not create a second store below each workspace.

Checkpoint data can retain source that has since been deleted. It must remain
local, must not be included in normal workspace downloads or profile exports,
and must never be returned across agent boundaries.

## Information architecture

### Agent settings

Add a **File restore points** card to the selected agent's Runtime settings and
to `AgentSettingsModal`. It is an individual profile setting, not a global
default toggle.

Disabled:

```text
+-- RUNTIME / SAFETY ----------------------------------+
|                                                     |
|  FILE RESTORE POINTS                         [ OFF ] |
|                                                     |
|  Preserve this agent's workspace before it changes |
|  files or runs destructive commands.               |
|                                                     |
|  Scope      This agent profile only                 |
|  Storage    No new restore points while disabled    |
|                                                     |
+-----------------------------------------------------+
```

Enabled:

```text
+-- RUNTIME / SAFETY ----------------------------------+
|                                                     |
|  FILE RESTORE POINTS                          [ ON ] |
|  * ACTIVE                                           |
|                                                     |
|  12 restore points                 38.4 MB retained |
|  Keeps up to 20 points              Files <= 10 MB |
|                                                     |
|  [ View restore points ]                            |
|                                                     |
+-----------------------------------------------------+
```

If Git is unavailable or the engine cannot initialize, distinguish
`enabled` from `available`:

```text
FILE RESTORE POINTS                             [ ON ]
! UNAVAILABLE — Git is not installed in this runtime
```

Do not silently render the switch as off when the saved setting is on but the
runtime capability is unavailable.

### Workspace file-row overflow menu

Replace the direct recycle-bin/trash button on each row with an accessible
ellipsis button. Clicking it must not activate or open the row.

File menu:

```text
AGENTS.md          Aug 11, 2026       694 B       ...
                                                +---------------------+
                                                | Open / Preview      |
                                                | Download            |
                                                | Version history   3 |
                                                +---------------------+
                                                | Rename              |
                                                | Copy path           |
                                                +---------------------+
                                                | Delete...           |
                                                +---------------------+
```

Required behavior:

- **Open / Preview** invokes the same behavior as opening the row.
- **Download** downloads the original file, not a preview conversion.
- **Version history** opens history scoped to that relative file path.
- **Rename** uses a validated server-side move within the same workspace.
- **Copy path** copies the workspace-relative path and shows a short live-region
  confirmation.
- **Delete...** is last, separated, destructive-colored, and always confirmed.
- The menu opens through mouse, touch, `Enter`, or `Space`; arrow keys move
  through items; `Escape` closes and returns focus to the ellipsis button.
- Close on outside click, agent/workspace navigation, row removal, or action
  completion.
- Prevent viewport clipping by flipping above/left near panel edges.
- Only one row menu may be open at a time.

When restore points are disabled, replace **Version history** with:

```text
Enable version history...
```

This navigates to the selected agent's Runtime settings. It must not enable the
feature automatically. When enabled but no retained version exists, keep
**Version history** available and show its empty state rather than presenting a
misleading count.

Directory menu:

```text
+---------------------+
| Open                |
| Upload here         |
| New file            |
| New folder          |
+---------------------+
| Rename              |
| Copy path           |
+---------------------+
| Delete folder...    |
+---------------------+
```

Do not place **Restore latest** directly in either overflow menu. Restoration
must go through a history/diff view so the target and impact are visible.

### Workspace Restore Points subview

Inside the Workspace panel, add a small segmented view:

```text
+-- WORKSPACE -----------------------------------------+
| [ FILES ]  [ RESTORE POINTS 12 ]              refresh|
+------------------------------------------------------+
| RESTORE POINTS                                       |
| Workspace state saved before agent changes           |
|                                                      |
| * #01  a38f21c                            2 min ago   |
|   before patch                                       |
|   Select to preview changes since this point         |
|                                                      |
| o #02  d091b72                           18 min ago   |
|   before terminal: sed -i ...                        |
|                                                      |
| o #03  8bc31e4                            yesterday  |
|   before write_file                                  |
|                                                      |
| Showing 3 of 12                             [ More ]  |
+------------------------------------------------------+
```

Do not expose an absolute workspace path, shadow-store path, index path, or raw
ref name. Display only short hashes; APIs use and validate the full hash.

Selecting a restore point shows:

```text
+-- RESTORE POINT a38f21c -----------------------------+
| Aug 14, 12:42:18                                     |
| Saved before: patch                                  |
|                                                      |
| CHANGES SINCE THIS POINT                             |
| M  src/components/KanbanView.tsx              +18 -4 |
| M  src/components/modals.tsx                    +6 -1 |
| A  src/components/HistoryPanel.tsx             +94 -0 |
|                                                      |
| [ Preview diff ]                                     |
|                                                      |
| [ Restore selected file ]                            |
| [ Restore entire workspace ]                         |
+------------------------------------------------------+
```

The narrow right panel is suitable for the timeline and file summary, not a
full unified diff. Open the full diff in a larger route-backed drawer over the
center area.

### File-specific version history

Opening **Version history** from a row filters history to the selected file:

```text
+-- AGENTS.md / VERSION HISTORY -----------------------+
| <- Workspace                                         |
|                                                      |
| * Current                                   694 B     |
| |                                                    |
| o a38f21c / Aug 14, 12:42                            |
| | before patch                              +12 -3   |
| | [Preview changes]  [Restore this file]             |
| |                                                    |
| o d091b72 / Aug 13, 09:18                            |
|   before write_file                           +4 -1   |
+------------------------------------------------------+
```

A file version is a unique retained blob state, not every checkpoint in which
the file happened to exist. Walk retained checkpoints newest to oldest and
collapse consecutive entries with the same file blob hash. Include a synthetic
**Current** entry. Represent absence explicitly so a version can restore a file
that was later deleted when reached from the workspace-wide history view.

Renames are not automatically inferred as file history in the first release.
History follows the exact workspace-relative path. The rename operation's
workspace checkpoint still permits a whole-workspace rollback.

### Diff drawer

Use a terminal-like unified diff with a file navigator and summary:

```diff
RESTORE POINT a38f21c
Changes since Aug 14, 12:42

---------------- src/components/modals.tsx ----------------
@@ -318,6 +318,11 @@
 const [approvalMode, setApprovalMode] = useState(...);
+const [checkpointsEnabled, setCheckpointsEnabled] =
+  useState(agent.checkpointsEnabled);

-----------------------------------------------------------
3 files changed, +118, -5

[ Restore this file ]                 [ Restore workspace ]
```

Requirements:

- Additions use the existing success color and deletions the existing danger
  color; meaning must also be conveyed through `+`, `-`, labels, and text.
- Long diffs are server-bounded and visibly truncated. Never load an unbounded
  patch into the browser.
- Binary and excluded files show metadata only. Do not attempt a text diff.
- The file navigator permits selecting one changed file without changing the
  restore-point route.
- Copying diff text is allowed, but no prompt, credentials, tool arguments, or
  tool output may be included by the checkpoint API.

### Restore confirmation

Whole workspace:

```text
+-- RESTORE WORKSPACE? --------------------------------+
|                                                      |
| Target       a38f21c / Aug 14, 12:42                 |
| Impact       3 files will change                     |
|                                                      |
| A safety restore point will be created first.        |
|                                                      |
| Chat history will not be changed. The agent may      |
| still remember work performed afterward.             |
|                                                      |
| Type RESTORE to continue                              |
| > _                                                  |
|                                                      |
| [ Cancel ]                      [ Restore workspace ] |
+------------------------------------------------------+
```

Single-file restore uses the same pattern without typed confirmation, but it
must name the relative file path, show the selected restore point, and state
that a safety restore point will be created first.

After success:

- close the confirmation;
- retain the history/diff location;
- show a success notice naming the restored scope and short hash;
- refresh the workspace directory, open-file content, restore-point list, and
  version count;
- if the open file was removed by the restore, close its preview and explain
  that it no longer exists;
- announce the result through a polite live region.

## Stable URLs

The repository requires meaningful selected subviews to survive reload and
Back/Forward navigation. Extend the existing chat/workspace route serializer
with query state equivalent to:

```text
?panel=workspace&workspaceView=restore-points
?panel=workspace&workspaceView=restore-points&checkpoint=<full-hash>
?panel=workspace&workspaceView=versions&path=src%2Fapp.ts
?panel=workspace&workspaceView=versions&path=src%2Fapp.ts&checkpoint=<full-hash>
```

The exact parameter names may be adjusted to match current router conventions,
but these states are required:

- Files versus Restore Points subview.
- Selected full checkpoint hash.
- Selected workspace-relative file path for version history.
- Selected checkpoint within that file's history.

The row overflow menu, transient confirmation, focus, and unsaved rename input
must not be stored in the URL.

## Public API contract

Add a dedicated `checkpoints` backend service group. Use that name consistently
for routes, models, handler operations, services, and the Hermes integration
adapter. The checkpoint engine owns its own shadow-store persistence, so no
second XNOBrain checkpoint repository is required.

All routes live below `/xnobrain/api/runtime/v1` through the existing route
assembly:

```text
GET  /agents/{agent_id}/checkpoints/status
GET  /agents/{agent_id}/checkpoints
GET  /agents/{agent_id}/checkpoints/{checkpoint_id}/diff
GET  /agents/{agent_id}/checkpoints/file-versions?path=<relative-path>
POST /agents/{agent_id}/checkpoints/{checkpoint_id}/restore
```

Keep rename with the workspace service group:

```text
POST /agents-workspaces/{agent_id}/rename
```

Do not accept a working-directory path from the caller. Resolve the exact
profile and workspace from `agent_id` on the server.

### Configuration patch

Extend the existing agent config patch:

```http
PATCH /xnobrain/api/runtime/v1/agents-configs/{agent_id}
Content-Type: application/json

{
  "checkpoints_enabled": true
}
```

The response agent DTO includes:

```json
{
  "config": {
    "checkpoints_enabled": true
  }
}
```

Do not add this switch to `agents-configs/global` as a default for new profiles.
If the shared Pydantic model makes the field syntactically accepted on the
global endpoint, the global service must reject it with a clear validation
error rather than silently creating a default-on policy.

### Status response

```json
{
  "agent_id": "a1b2c3",
  "enabled": true,
  "available": true,
  "unavailable_reason": null,
  "checkpoint_count": 12,
  "retained_bytes": 40265318,
  "max_snapshots": 20,
  "max_file_size_bytes": 10485760
}
```

`retained_bytes` must be scoped to this workspace/project where possible. Do
not return the shared store's other projects, absolute workdirs, or project
metadata. If the upstream store cannot calculate an accurate per-project byte
count without walking shared/deduplicated objects, return `null`; never present
the entire shared store size as if this agent alone consumed it.

### List response

```json
{
  "items": [
    {
      "id": "a38f21c7c8f6d4e2b3...",
      "short_id": "a38f21c",
      "created_at": "2026-08-14T05:42:18Z",
      "reason": "before patch",
      "trigger": "patch",
      "files_changed": 3,
      "insertions": 118,
      "deletions": 5
    }
  ],
  "next_cursor": null
}
```

Never use the list position (`1`, `2`, and so on) as an API identifier because
positions change after pruning and new checkpoints. The UI may display a
temporary ordinal, but restore and diff requests use the validated full hash.

Hermes' current aggregate stats compare adjacent checkpoint commits. They do
not necessarily describe all changes from the selected checkpoint to the
current workspace. Label list stats conservatively, and use the selected diff
endpoint for the authoritative **Changes since this point** summary.

### Diff response

```json
{
  "checkpoint_id": "a38f21c7c8f6d4e2b3...",
  "short_id": "a38f21c",
  "files": [
    {
      "path": "src/components/modals.tsx",
      "status": "modified",
      "insertions": 6,
      "deletions": 1,
      "binary": false
    }
  ],
  "patch": "diff --git ...",
  "truncated": false,
  "total_patch_bytes": 4218
}
```

Use response limits for maximum files, maximum lines, and maximum bytes. A
truncated response remains successful and tells the UI exactly that it was
truncated. Do not place the diff body in logs or telemetry.

### File-version response

```json
{
  "path": "AGENTS.md",
  "current": {
    "exists": true,
    "size": 694,
    "modified_at": "2026-08-14T05:46:00Z"
  },
  "items": [
    {
      "checkpoint_id": "a38f21c7c8f6d4e2b3...",
      "short_id": "a38f21c",
      "created_at": "2026-08-14T05:42:18Z",
      "reason": "before patch",
      "exists": true,
      "blob_id": "server-internal-or-opaque",
      "size": 681
    }
  ]
}
```

The server may use blob IDs to collapse duplicates, but the browser does not
need raw Git object operations. Do not expose a route that reads arbitrary
objects by hash.

### Restore request and response

Whole workspace:

```json
{}
```

Single file:

```json
{
  "path": "src/components/modals.tsx"
}
```

Response:

```json
{
  "restored": true,
  "scope": "file",
  "path": "src/components/modals.tsx",
  "checkpoint_id": "a38f21c7c8f6d4e2b3...",
  "short_id": "a38f21c",
  "safety_checkpoint_created": true,
  "conversation_changed": false
}
```

If the pre-restore safety checkpoint cannot be created, fail the restore. The
upstream automatic tool behavior is fail-open, but an explicit user-requested
rollback must be fail-closed because the confirmation promises recoverability.
If necessary, add a narrow XNOBrain integration wrapper that verifies `_take`
succeeded before invoking restore; do not change silent fail-open behavior for
normal agent tools.

### Rename request

```json
{
  "path": "reports/old-name.md",
  "new_name": "new-name.md"
}
```

Rename stays in the same parent directory for the first release. Validate
`new_name` with the same Unicode normalization, encoded separator, traversal,
NUL, and length rules used for workspace creation. Reject overwrite with
`409 destination_exists`. Use an atomic filesystem rename after creating a
restore point when enabled.

## Backend architecture

### Files to add

```text
xnobrain/routes/checkpoints.py
xnobrain/models/checkpoints.py
xnobrain/handlers/operations/checkpoints.py
xnobrain/services/checkpoints.py
xnobrain/integrations/checkpoints.py
xnobrain/tests/test_checkpoints.py
```

### Files to update

```text
xnobrain/routes/setup.py
xnobrain/models/__init__.py
xnobrain/handlers/operations/__init__.py
xnobrain/services/platform.py
xnobrain/routes/agents.py
xnobrain/models/agents.py
xnobrain/services/agents.py
xnobrain/integrations/agents.py
xnobrain/routes/workspaces.py
xnobrain/models/workspaces.py
xnobrain/handlers/operations/workspaces.py
xnobrain/services/workspaces.py
xnobrain/integrations/agent_profiles.py
```

Keep composition files limited to composition. Do not add checkpoint workflows
to `services/platform.py`, `routes/setup.py`, or the generic repository facade.

### Integration adapter responsibilities

The XNOBrain checkpoint integration adapts `tools.checkpoint_manager` and owns:

- construction with the selected profile's effective config;
- capability probing without taking a snapshot;
- exact workspace-root resolution;
- normalized DTOs that omit absolute paths and debug stderr;
- full-hash validation before calling Hermes;
- diff size/line/file limits;
- file-version traversal and duplicate-blob collapse;
- explicit safety-checkpoint verification for restore;
- translation of upstream non-fatal failures into typed service errors for
  explicit user operations;
- per-project status filtering.

Do not copy `CheckpointManager`, shell out to the user's real repository, or
reach through local HTTP. Import and adapt the installed Hermes implementation.
If a missing public method requires upstream-private helpers, isolate that use
inside this one adapter and cover it with a compatibility test against the
installed Hermes checkout.

### Service responsibilities

The checkpoint service owns:

- agent/profile existence validation;
- profile enablement policy;
- active-run and lock checks;
- workspace path confinement and symlink escape rejection;
- mapping disabled/unavailable/busy/not-found/conflict states to safe errors;
- list/diff/version/restore orchestration;
- workspace-cache invalidation after restore;
- structured metadata-only audit/telemetry events.

The workspace service calls a small checkpoint-service method before each
mutation. Avoid a circular dependency by injecting a narrow collaborator such
as `workspace_checkpoint(agent_id, reason)` during service composition.

### Workspace mutation coverage

When enabled, checkpoint immediately before:

- text/binary workspace write;
- file or directory create;
- file or directory delete;
- rename;
- publication of a completed upload.

Do not checkpoint every upload chunk. Chunks live in temporary upload storage;
create one restore point immediately before atomically publishing the completed
file to its destination. If publication fails, retain normal upload cleanup
behavior and do not report the restore point as proof that the upload succeeded.

Use sanitized, bounded reasons such as:

```text
before workspace write: reports/summary.md
before workspace delete: reports/old.md
before workspace rename: reports/old.md
before workspace upload: data/input.csv
```

Do not put file contents, prompts, commands, request bodies, or credentials in
checkpoint reasons or logs.

### Concurrency

Create one process-wide lock per canonical agent workspace. The lock covers
explicit diff staging, file-version inspection where it shares an index,
checkpoint creation, workspace mutation publication, pruning for that project,
and restore.

Before restore:

1. Resolve and validate the agent workspace.
2. Check the conversation runner and team-run service for active execution by
   that profile.
3. Return `409 agent_busy` if any foreground/background/team execution can
   write the workspace.
4. Acquire the per-workspace operation lock.
5. Recheck active execution after acquiring the lock.
6. Verify the target checkpoint belongs to the exact workspace ref.
7. Create and verify the pre-restore safety checkpoint.
8. Restore the requested relative path or workspace.
9. Release the lock and invalidate workspace/history caches.

Do not offer a `force` restore in the first release. A user must stop the run
first. This avoids a restored filesystem being immediately overwritten by a
still-running tool.

The shared bare store and per-project Git index must not be manipulated by two
XNOBrain requests concurrently. The lock is required even when two routes are
read-only at the HTTP level because Hermes diff stages the current tree into a
per-project index before resetting it.

### Path and data security

- Derive workspace root from the validated profile, never from a request path.
- Accept only workspace-relative file paths for version and restore operations.
- Reject absolute paths, traversal, percent-encoded separators, Unicode slash
  lookalikes, NUL/newlines, and symlink escapes.
- Verify a selected checkpoint exists on the workspace's own ref. A valid Git
  object from another profile is not a valid target.
- Never return upstream `debug`, Git stderr, environment data, or absolute
  directories in API errors.
- Preserve Hermes secret exclusions. Add tests proving `.env`, `.env.local`,
  logs, and oversized/binary exclusions do not leak through diff responses.
- Never log or trace patch bodies, file contents, prompts, tool arguments, or
  tool results. Telemetry may include agent ID, operation, success/failure code,
  duration, file count, and byte counts only.
- Bound Git subprocess time and API response size.

## Frontend architecture

### Suggested files to add

```text
src/api/checkpoints.ts
src/api/mappers/checkpoints.ts
src/hooks/useCheckpoints.ts
src/components/WorkspaceRowMenu.tsx
src/components/WorkspaceRestorePoints.tsx
src/components/FileVersionHistory.tsx
src/components/CheckpointDiffDrawer.tsx
```

Add colocated focused tests for each behavior. Exact component splitting may
follow existing conventions, but do not grow all menu, history, diff, and
restore logic inside `WorkspacePanel.tsx`.

### Files to update

```text
src/types.ts
src/api/contracts/agentGateway.ts
src/api/agents.ts
src/api/workspace.ts
src/api/mappers/agents.ts
src/hooks/useAssistants.ts
src/hooks/useWorkspace.ts
src/hooks/useRouter.ts
src/components/WorkspacePanel.tsx
src/components/RightPanel.tsx
src/components/modals.tsx
src/App.tsx
src/styles.css
src/locales/en.json
src/locales/de.json
src/locales/es.json
src/locales/fr.json
src/locales/ja.json
src/locales/vi.json
src/locales/zh.json
```

All strings, relative dates, counts, file statuses, confirmations, errors, and
live announcements go through i18n. Do not update only English and Vietnamese.

### State ownership

- `useWorkspace` continues to own files, selection, preview, upload, and
  workspace mutations.
- `useCheckpoints` owns status, pagination, selected checkpoint, diff,
  file-version history, restore mutation, and cache invalidation.
- Router owns only durable view selection described in Stable URLs.
- `WorkspaceRowMenu` owns ephemeral open/focus state.
- Confirmation components own unsaved typed confirmation input.
- Successful restore invalidates both hooks rather than manually patching a
  partial local representation.

Do not fetch a version count for every file during a directory listing. Fetch
file history lazily when its menu opens or the user selects Version history.
The count may then be cached by agent/path and invalidated after workspace
mutation, checkpoint creation observed after a run, prune, or restore.

### Interaction details

- A normal row click continues to open/select the entry.
- The ellipsis has `aria-haspopup="menu"`, an expanded state, and a file-specific
  accessible label such as `Actions for AGENTS.md`.
- Menu actions use real buttons, not clickable `div` elements.
- Rename begins with the basename selected without the extension where
  practical, validates locally, and preserves input after a recoverable API
  error.
- Delete confirmation keeps the existing behavior but is launched from the
  menu. Focus returns to a sensible neighboring row if deletion succeeds.
- Disabled checkpoint state contains a direct navigation action to Runtime
  settings, not a dead control.
- Loading must not erase the existing file list or cause panel-width jumps.
- Refresh applies to the currently selected Files/Restore Points view.
- Dark/light themes and 200% browser zoom must remain usable.

## Error and empty states

Design and test the following explicitly:

| State | Required UI |
| --- | --- |
| Disabled | Explanation plus **Enable in Runtime settings** |
| Enabled, no history | `No restore points yet. One will be saved before the next file change.` |
| Git unavailable | Saved switch remains on; show capability error and runtime recovery guidance |
| Checkpoint skipped: file count | Explain the 50,000-file protection without claiming the edit failed |
| Oversized file excluded | Show that the file was not protected and the configured limit |
| Active run | Disable restore and provide **Stop run first** guidance |
| Pruned target | Refresh list and report that the restore point expired |
| Empty diff | `No changes since this restore point.` |
| Truncated diff | Show summary plus `Diff limited to ...` |
| Binary diff | Metadata/status only; no broken text renderer |
| Restore failure | Keep confirmation context, show safe retryable error, do not claim success |
| Safety snapshot failure | Abort restore and explain that current state could not be protected |
| File removed after menu opened | Close menu, refresh, announce the stale item |
| Network loss | Keep selected checkpoint/path and offer retry |

## Implementation sequence

1. Add focused compatibility tests around the installed Hermes
   `CheckpointManager`: profile config, project-ref isolation, list, diff,
   single-file restore, full restore, pre-restore snapshot, exclusions, and
   concurrent index access.
2. Add `checkpoints_enabled` to the named-agent config integration, service,
   DTO, TypeScript mapper, and settings UI. Prove the root/global endpoint
   cannot make new profiles default-on.
3. Create the backend `checkpoints` service group and read-only status/list/diff
   endpoints. Normalize all responses and remove absolute/debug data.
4. Add file-version traversal and duplicate-blob collapse for an exact relative
   path.
5. Implement active-run checks, per-workspace locks, verified safety snapshot,
   single-file restore, and whole-workspace restore.
6. Integrate checkpoints into every XNOBrain workspace mutation, including one
   snapshot at completed-upload publication.
7. Add confined, atomic workspace rename and its tests.
8. Implement typed frontend clients and `useCheckpoints`.
9. Replace direct trash buttons with accessible row overflow menus while
   preserving current open, download, and delete behavior.
10. Add the Workspace Files/Restore Points subview, file-specific history, diff
    drawer, restore confirmations, refresh/invalidation, and notices.
11. Add durable route parsing/serialization and Back/Forward tests.
12. Add every locale, responsive styling, keyboard behavior, focus lifecycle,
    live-region announcements, and reduced-motion support.
13. Run focused backend/frontend tests, compile the backend, run the frontend
    build, and then run `make check` when practical.

Do not begin with the UI against invented responses. Land and test the public
contract first, then build the UI against those exact DTOs.

## Backend test matrix

At minimum, cover:

- missing setting defaults to disabled;
- one named profile enabled while another remains disabled;
- creating a new profile never inherits an enabled checkpoint switch;
- Big Brother and a named profile remain independently configurable;
- effective config reaches API-server-created `AIAgent` instances;
- disabled mutation creates no checkpoint;
- enabled agent `write_file`, `patch`, and destructive terminal paths create a
  checkpoint through Hermes;
- enabled workspace create/write/delete/rename/upload publication creates a
  checkpoint;
- upload chunks do not create repeated checkpoints;
- list returns only the selected workspace's history and no absolute paths;
- a hash from another workspace is rejected;
- invalid/short/malformed/injected hashes are rejected;
- file paths reject traversal, absolute paths, encoded separators, Unicode
  lookalikes, NUL/newline, and symlink escape;
- `.env*`, logs, VCS data, excluded binaries, and oversized content are not
  returned;
- diff limits report truncation accurately;
- consecutive identical blobs collapse in file history;
- single-file restore changes only the selected file;
- whole restore handles modifications, deletions, and newly created files;
- a verified pre-restore checkpoint makes rollback-of-rollback possible;
- safety-checkpoint failure aborts explicit restore;
- active conversation/team run returns `409 agent_busy`;
- mutation and restore cannot interleave under the per-workspace lock;
- a pruned/unknown checkpoint returns a safe `404` or `409` contract;
- restore never changes conversation records;
- config mutation creates the repository-required immutable config snapshot and
  uses atomic write semantics;
- no response or captured log includes raw Git stderr, absolute storage paths,
  prompts, tool arguments, tool output, or credentials.

## Frontend test matrix

At minimum, cover:

- file rows render ellipsis instead of direct trash;
- clicking ellipsis does not open the file;
- row click still opens the file;
- mouse and keyboard menu open/navigation/close/focus return;
- menu flips or remains reachable near viewport edges;
- Open, Download, Copy path, Rename, and Delete call the correct existing/new
  controller operations;
- Delete remains confirmed and destructive-styled;
- disabled history action navigates to this agent's Runtime settings;
- enabled history opens the correct route with an encoded relative path;
- no per-row version-history N+1 fetch occurs on directory load;
- restore-point and file-version empty/loading/error states;
- selecting a restore point updates the URL and Back/Forward restores it;
- diff file navigation and truncated/binary rendering;
- single-file and whole-workspace confirmation differences;
- active-run restore disabled state;
- successful restore refreshes tree, selected preview, checkpoint list, and
  history count;
- restored deletion closes a now-missing preview with an explanation;
- switch saves only the selected profile and preserves unavailable/on state;
- API errors preserve rename input and selected history context;
- focus and live-region announcements after copy, rename, delete, and restore;
- all seven locale files contain the required keys;
- light/dark theme, narrow panel, 200% zoom, and reduced-motion checks;
- `npm test` and `npm run build` pass.

## Acceptance criteria

The feature is complete when all of the following are true:

1. Restore points are off by default and can be enabled for exactly one agent
   without changing any other profile or future-profile default.
2. Both Hermes agent edits and XNOBrain workspace mutations are protected when
   enabled and unchanged when disabled.
3. The workspace row shows a usable ellipsis menu with Open/Preview, Download,
   Version history, Rename, Copy path, and confirmed Delete.
4. A user can browse restore points, select a file version, inspect a bounded
   diff, and understand what will change before restoring.
5. Single-file restore does not alter unrelated files; whole-workspace restore
   correctly handles created, modified, and deleted paths.
6. Every explicit restore first verifies a safety checkpoint and never races an
   active agent run or another workspace mutation.
7. Restore-point selection and file-version history survive reload and browser
   Back/Forward navigation.
8. No API or log leaks another profile's history, absolute paths, secret files,
   file contents outside the bounded diff response, prompts, tool arguments, or
   tool output.
9. Disabling the feature stops new snapshots without pretending existing data
   was erased.
10. Focus, keyboard operation, localization, responsive layouts, and all test
    gates pass.

## References

Upstream public documentation:

- [Hermes Agent — Checkpoints and `/rollback`](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/checkpoints-and-rollback.md)
- [Hermes Agent — Git worktrees](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/git-worktrees.md)

Upstream implementation source:

- [Checkpoint manager](https://github.com/NousResearch/hermes-agent/blob/main/tools/checkpoint_manager.py)
- [Agent tool executor checkpoint hooks](https://github.com/NousResearch/hermes-agent/blob/main/agent/tool_executor.py)
- [Agent checkpoint construction](https://github.com/NousResearch/hermes-agent/blob/main/agent/agent_init.py)
- [Gateway profile checkpoint configuration](https://github.com/NousResearch/hermes-agent/blob/main/gateway/run.py)
- [CLI rollback behavior](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/cli_commands_mixin.py)
- [Gateway rollback behavior](https://github.com/NousResearch/hermes-agent/blob/main/gateway/slash_commands.py)
- [Checkpoint manager tests](https://github.com/NousResearch/hermes-agent/blob/main/tests/tools/test_checkpoint_manager.py)
- [Tool executor checkpoint path tests](https://github.com/NousResearch/hermes-agent/blob/main/tests/agent/test_tool_executor_checkpoint_paths.py)

Relevant XNOBrain implementation surfaces:

- [`src/components/WorkspacePanel.tsx`](../../src/components/WorkspacePanel.tsx)
- [`src/hooks/useWorkspace.ts`](../../src/hooks/useWorkspace.ts)
- [`src/api/workspace.ts`](../../src/api/workspace.ts)
- [`src/components/RightPanel.tsx`](../../src/components/RightPanel.tsx)
- [`src/components/modals.tsx`](../../src/components/modals.tsx)
- [`src/hooks/useRouter.ts`](../../src/hooks/useRouter.ts)
- [`xnobrain/routes/workspaces.py`](../../xnobrain/routes/workspaces.py)
- [`xnobrain/services/workspaces.py`](../../xnobrain/services/workspaces.py)
- [`xnobrain/integrations/agents.py`](../../xnobrain/integrations/agents.py)
- [`xnobrain/integrations/conversation_runner.py`](../../xnobrain/integrations/conversation_runner.py)

## Implementation note for the next agent

Treat the installed Hermes implementation as an external runtime boundary even
though its development checkout is locally visible. Extend XNOBrain around it;
do not fork or copy it. Verify signatures against the actual installed source
before coding because the Docker build tracks Hermes `main`.

Preserve unrelated user changes already present in the worktree. In particular,
do not hand-edit generated `dist/` assets. Implement source changes under
`src/` and `xnobrain/`, add focused tests, and regenerate distributable output
only if a later request explicitly asks for it.
