# XNOBrain Manual Functional QA Checklist

Date started: 2026-08-14  
Environment: local development (`make dev`)  
Primary test model: `cx/gpt-5.6-luna`  
Tester: Codex manual browser QA  

## Rules and evidence

- [!] Do not delete, archive, overwrite, disconnect, or clean up existing user data. No pre-existing user record was intentionally changed, but two missing-confirmation bugs caused unavoidable QA-state mutations: Automation Delete removed the QA job before restoration (BUG-012), and Cancel team run archived five newly created QA task nodes (BUG-031).
- [x] Do not delete test data after testing; leave every created artifact for review.
- [!] Exercise destructive buttons only through validation/confirmation, then cancel. The Automation delete control bypassed confirmation and deleted the QA record before it could be cancelled (BUG-012); the job was restored from its snapshot.
- [x] Use unique `QA 2026-08-14 ...` names for newly created records.
- [x] Use `cx/gpt-5.6-luna` for agent/model calls unless a scenario explicitly verifies Blend routing.
- [x] Keep Blend calls minimal because alternate models may cost more.
- [x] Capture screenshots for visual bugs and relevant console/network/runtime evidence for logic bugs.
- [x] Record each confirmed bug in its own `reviews/<bug-id>/README.md` directory.

Status convention: `[ ]` not run, `[x]` passed, `[!]` failed/bug filed, `[-]` unavailable or safely skipped with a written reason.

> Remediation update (2026-08-14): BUG-001 through BUG-036 linked below have been addressed in the runtime and covered by focused regression tests. The `[!]` entries are intentionally retained as the factual results of the original pre-fix QA run; they should only be changed after the corresponding browser/data-preservation scenario is re-run against the fixed build. Repository verification passes with 225 backend tests (3 skipped), 322 frontend tests, Python compilation, and the production frontend build.

## 1. Startup, authentication, and application shell

- [x] Start `make dev`; verify frontend, runtime API, and router/gateway listen on their configured ports.
- [x] Verify the login screen loads without a blank screen or console crash.
- [!] Submit an invalid username/password and verify a useful inline error with no credential leakage. Rejection is inline and does not leak the password, but displays the internal code `INVALID_LOGIN_CREDENTIALS` (BUG-027).
- [x] Login using the supplied test account and verify redirect into the authenticated application.
- [x] Reload after login and verify the authenticated session persists in the original tab. New-tab persistence fails separately (BUG-014).
- [x] Verify account menu shows the authenticated user, edition/deployment, appearance, language, and sign-out control.
- [x] Open Account details and verify loading, user identity, plan/roles, deployment, and session sections.
- [x] Open sign-out confirmation and cancel; do not sign out until the end of the pass.
- [x] Switch Light, Dark, and System themes; verify readable contrast and persistence after reload.
- [x] Switch at least English and Vietnamese; verify layout does not overflow and language persists.
- [x] Collapse/expand the left sidebar and right inspector; resize the right inspector and double-click reset.
- [x] Verify desktop and narrow/mobile navigation, manage drawer, close behavior, and no trapped scrolling.
- [x] Verify browser Back/Forward and reload preserve primary page, selected entity, and meaningful detail routes.
- [x] Inspect browser console and failed network requests at initial load. Reload produced no console errors, page errors, request failures, or HTTP 4xx/5xx responses.

## 2. Agent library and profile lifecycle

- [!] Verify Agents list loads, search filters by name/description, clear restores results, and no-match state is useful. Core search works, but browser autofill can inject the login email and hide every agent (BUG-009).
- [x] Verify the pinned Big Brother/profile behavior and pin/unpin controls for other agents.
- [x] Verify green status appears only during an active conversation or task; idle agents remain gray.
- [x] Create `QA 2026-08-14 Primary Agent` with a description and `cx/gpt-5.6-luna`.
- [!] Validate required create-agent fields and duplicate/invalid input feedback. Required validation is silent and exposes an internal API message (BUG-001).
- [x] Open the new agent and verify title, description, provider, model, and workspace are correct.
- [x] Rename the QA agent using mouse flow and keyboard Enter/Escape behavior.
- [x] Update description/metadata and verify persistence after reload.
- [x] Configure reasoning effort and approval settings; verify saved values return after reload.
- [!] Use Test Agent and verify success/failure feedback identifies the actionable provider/model state. The notification bell overlaps and intercepts the action (BUG-021).
- [x] Export the QA profile and verify a non-empty valid download without exposing credentials.
- [x] Open Delete Agent confirmation and cancel; verify the agent and its data remain.
- [x] Open Agent Library, search, select an agent, close, and verify route/focus behavior.

## 3. Providers, accounts, and models

- [x] Open Settings > Connections and verify every displayed provider has correct brand, description, mode, and status.
- [x] Verify OpenAI-compatible, Anthropic-compatible, OpenCode Zen, Codex, Google, OpenRouter, xAI, Groq, and any locally available providers are represented correctly.
- [x] Verify connected vs disconnected state is accurate and not inferred merely from free-model availability.
- [x] Verify provider model lists contain correct prefixes/IDs and no duplicate or stale models (`oc`, `ocz`, `cx`, custom IDs).
- [x] Verify OpenCode Zen has no bundled/default free credential and requires a user-owned key where appropriate.
- [!] Open API-key entry; verify required validation, masked input, cancel behavior, and no secret in DOM/network response/logs. See BUG-010.
- [!] Open custom-compatible provider form; test display name, base URL, API key, model list/default model, and malformed URL validation without saving invalid data. Save accepts malformed Base URL and advanced identity/model fields are absent (BUG-023); invalid data was not saved.
- [x] Test connection status/check for already configured providers without changing credentials.
- [x] Load provider accounts, active-account selection, reorder controls, connection usage, and per-account test.
- [x] Open remove/disconnect confirmations and cancel; do not remove or disconnect accounts.
- [x] Verify agent model picker shows connected direct models and saved Blends, with readable grouping and context metadata.
- [x] Select `cx/gpt-5.6-luna`, reload, and verify selection persists.
- [x] Send a cheap prompt and verify the request reaches the selected provider/model without 401/auth-prefix mismatch.
- [x] Verify usage attribution uses the selected model/provider identity rather than an unrelated connection UUID.

## 4. Conversations and chat composer

- [x] Create a new session for the QA agent; verify route, empty state, and title.
- [x] Send `Reply with exactly: QA BASIC CHAT PASS` and verify streaming, final response, model, and usage.
- [x] Verify user prompt Copy copies the exact full prompt once, without markdown/UI text.
- [!] Verify assistant Copy, positive/negative feedback, More menu, and Retry controls render and behave safely. Copy is exact and Retry is wired; feedback and More are inert (BUG-018).
- [x] Send with Enter; insert newline with Shift+Enter; verify disabled/empty send behavior.
- [-] Verify queued messages: enqueue while running, edit, reorder, and remove one newly queued message without affecting conversation history. Enqueue/edit/remove passed; reordering was unavailable because the current queued-message UI exposes no reorder control.
- [!] Stop an active response; verify confirmation/state, terminal event, composer recovery, and no stuck green agent status. Composer and idle status recover, but Stop has no confirmation or visible cancelled terminal state (BUG-017).
- [x] Reload during an active run; verify the run continues, reconnects, streams progress, and does not duplicate the user message or response.
- [!] Close the tab/reopen active session route and verify durable run recovery. Reload/navigation recovery passed, but a same-context new tab loses authentication (BUG-014).
- [x] Verify run deadline notice is hidden normally and appears only while running with less than five minutes remaining.
- [x] Verify activity header updates duration and total steps during execution.
- [x] Verify tokens/tool counts/steps update live where events provide data and finalized usage matches session usage.
- [x] Verify reasoning can expand/collapse, streamed deltas do not duplicate the final answer, and completed logs start collapsed.
- [x] Verify tool cards show safe summaries, status, duration, expandable results, and no secret/tool-argument leakage.
- [x] Verify todo/plan updates show completed/current/pending states and chronological updates.
- [!] Verify approval prompt choices (Allow once, Always allow, Deny) render; use only a harmless operation for execution. Prompt rendered and approval committed; final result is wrong (BUG-007).
- [x] Open Session Context, verify context usage, manual compaction eligibility, confirmation, optional preservation note, result/error states.
- [x] Create a second session, switch between sessions, search session history, and load more if available.
- [!] Rename a session and verify reload persistence. The double-click rename handler is unreachable because the first click closes the picker (BUG-020).
- [x] Open Delete Session confirmation and cancel; retain all sessions.
- [x] Verify selected session route survives direct paste/reload and browser navigation.
- [!] Verify missing/stale session route shows a recoverable state rather than creating hidden data. It silently selects an unrelated worker session (BUG-019).
- [x] Verify markdown headings, lists, tables, code fences, math, safe external links, workspace links, and task links render correctly. The intended inline-code workspace reference and automatic Kanban task-ID link both rendered; workspace quick preview opened successfully.
- [x] Verify very long messages, Unicode/Vietnamese, quotes, JSON, and multiline content wrap without horizontal page breakage.
- [-] Deliberately reproduce provider/auth/timeout chat errors. Safely skipped: provider success/prefix routing was already verified, credential mutation was prohibited, and the active `.env` deadline is 3600 seconds; waiting an hour solely to force an error was not proportionate. The pre-fix 401 supplied by the user was not treated as current UI evidence.

## 5. Attachments and workspace

- [x] Open right Workspace panel and verify breadcrumbs, list/grid view, refresh, up navigation, empty state, and persisted width.
- [x] Create `qa-2026-08-14/` and a text/Markdown file; leave both in place.
- [x] Edit and save the QA text file; reload and verify exact persisted content.
- [x] Upload a small text file and image with progress; verify filenames and sizes.
- [x] Drag/drop a file and a folder if browser automation supports DataTransfer; otherwise record skipped reason. File-row drag/drop to the composer was verified with the browser's real DataTransfer path; folder upload was not offered by the visible file chooser.
- [x] Preview text, Markdown, source code, JSON, image, HTML Preview/Source, PDF, and spreadsheet types using existing or newly generated harmless files.
- [!] Verify unsupported and too-large preview fallbacks offer download and do not freeze the UI. Unsupported-file selection stays responsive but silently no-ops with no download fallback (BUG-028); a deliberately large retained upload was not created after this shared fallback defect was established.
- [x] Download a workspace file and verify name/content.
- [x] Attach an existing workspace file to chat; verify chip, remove, and sent attachment context.
- [x] Open a workspace link from an assistant response and verify the correct file/panel opens.
- [x] Open Delete Workspace Item confirmation and cancel; retain all workspace data.
- [!] Attempt path traversal/suspicious filename through visible inputs and verify rejection without data exposure. `../escape.txt` is accepted and creates a file in the parent workspace directory (BUG-029).

## 6. Skills

- [x] Open Skills Library; verify loading, groups, search query in URL, group filter in URL, empty state, and close navigation.
- [x] Inspect skill cards for name, description, category, installed/enabled state, and agent coverage.
- [x] Install a harmless bundled/default skill on the QA agent and leave it installed. The retained QA skill probe remains enabled.
- [x] Toggle a newly installed QA-safe skill enabled/disabled and verify persistence; finish enabled if useful.
- [!] Open Install Skill and validate required ID/category/content fields and malformed input feedback. Current URL/hub flow silently ignores malformed input (BUG-022); ID/category/content fields are not exposed.
- [x] Preview common-skill sync across agents; verify added/updated/unchanged/conflict counts.
- [-] Apply a safe sync to QA-created agents only; verify confirmation, per-agent results, retry-failed, and Done flow. Safely skipped: preview showed it would remove 64 installed skills from the QA Primary Agent, violating the preservation rule; confirmation was not submitted.
- [x] Verify agent right-panel Skills view matches the library state.
- [!] Verify task skill picker loads only enabled installed skills for the selected assignee. Disabled skills remain enabled/selectable in the picker (BUG-030).

## 7. Context files and MCP

- [x] Open Settings > Context; select each agent and load `SOUL.md` and `AGENTS.md`. Both files loaded without an alert for all 13 current profiles.
- [x] Save a harmless append-only QA note to the QA agent context and verify persistence/snapshot behavior through UI.
- [!] Verify unsaved editor switching/close behavior does not silently discard without warning. Close immediately discards a dirty draft with no warning (BUG-034).
- [x] Open Settings > MCP; verify agent picker, reload, help text, and JSON editor.
- [!] Insert stdio example; validate command/args/env/tools schema and invalid JSON feedback. JSON syntax is checked, but invalid command types are accepted (BUG-011).
- [!] Insert HTTPS example; validate URL/headers/env reference/tools schema and invalid URL feedback. Malformed URLs are accepted and persisted (BUG-011).
- [!] Save a harmless non-secret QA MCP configuration only if it cannot launch an external process; otherwise validate then leave unsaved and record reason. Retained malformed QA probe demonstrates BUG-011.
- [x] Verify secret values are not returned after load and UI explains environment references.

## 8. Blends and Smart Route

- [x] Open Settings > Blends; verify list, create/edit entry points, descriptions for each mode, and Smart Route cost/latency note.
- [x] Open create dialog with blank values; verify required `*`, inline name error, model error, and disabled/guarded Save.
- [x] Create `QA 2026-08-14 Smart Route` using minimal models and `cx/gpt-5.6-luna` where selectable.
- [x] Smart Route: assign the same model to Quick and Normal; verify duplicate-across-tier use is accepted.
- [x] Smart Route: configure Difficult and uncertain-task fallback; verify both Normal and Difficult choices.
- [x] Round robin: verify one-model minimum, order controls, removal, sticky limit bounds/default/global note.
- [-] Random: verify one-model minimum and model removal/order UI. Unavailable: the current UI exposes Fallback, Round robin, Fusion, and Smart route only.
- [-] Least used: verify one-model minimum and description. Unavailable in the current Blend mode selector.
- [-] Cost optimized: verify one-model minimum and description. Unavailable in the current Blend mode selector.
- [-] Latency optimized: verify one-model minimum and description. Unavailable in the current Blend mode selector.
- [x] Fusion: verify at least two models, required judge model, ordering, and validation messages.
- [x] Save/edit/reload the QA Blend and verify exact strategy/config persistence.
- [x] Select the QA Blend on the QA agent and send one quick classification prompt; verify routed model and response.
- [x] Send one normal prompt that can use the same configured model; verify routing does not reject duplicate tier membership.
- [x] Avoid additional Blend calls unless needed to confirm a suspected routing bug.
- [x] Open Blend delete confirmation and cancel; retain the QA Blend.

## 9. Multi-agent delegation inside chat

- [x] Send a real five-subtask prompt that requires KDD, NeurIPS, ICML, ICLR, ACL research and a workspace PDF report.
- [x] Verify all five workers appear in one delegation card with configured concurrency (maximum three running; remainder queued).
- [x] Verify queued workers automatically start as slots free without a second user/model delegate call.
- [x] Verify task descriptions truncate with ellipsis and full text is available on hover/title.
- [x] Verify card totals update live: completed/running/queued/slots, tools, steps, tokens, and elapsed time.
- [x] Verify each worker status and duration updates independently.
- [x] Click a running worker and verify streaming log popup updates without reopening.
- [x] Verify popup tabs/log/result, tool names, steps, token totals, files read/written, and transcript metadata.
- [x] Verify completed worker details remain viewable and sensitive paths/tool arguments are not exposed improperly.
- [x] Verify Cancel All opens confirmation; cancel the confirmation so the test run continues.
- [x] Verify delegation results return to the parent for synthesis and the requested PDF is written to workspace.
- [!] Verify the PDF is non-empty, opens in preview, wraps tables/text, and contains clickable links where expected. PDF opens and text wraps, but it contains no clickable link annotations (BUG-016).
- [x] Reload during delegation and verify queue/progress/logs recover without duplicate workers.

## 10. Teams — builder, workflow, communication, and discussion

- [x] Open Teams library; verify loading/empty state, cards, saved-run counts, rename menu, and selected-team route.
- [x] Create at least three QA agents with `cx/gpt-5.6-luna` for coordinator/research/review roles; retain them.
- [x] Create `QA 2026-08-14 Discussion Team` with name and description validation.
- [x] Add multiple agents to the visual workflow; verify Start/Finish nodes, drag placement, arrange, zoom in/out/fit, wheel zoom, and pan.
- [x] Configure per-stage role, instructions, agent, dependencies, tool access, and preloaded skills.
- [x] Verify dependency validation prevents invalid/self/cyclic links and visually represents valid edges.
- [x] Configure Start and Finish prompts.
- [x] Test communication level 0 Isolated description/configuration.
- [x] Test communication level 1 Result passing description/configuration. A separate level-1 runtime run was not needed because the retained dialogue run exposes upstream summaries.
- [x] Test communication level 2 Shared scratchpad and verify multiple agents read/write the same run workspace path with visible evidence.
- [x] Test communication level 3 Team dialogue/discussion and verify upstream review/discussion actually occurs before completion.
- [x] Set Parallel agents at lower/default/maximum allowed values; verify HTML bounds and configured runtime concurrency.
- [x] Set Delegation depth at lower/default/maximum allowed values; verify HTML bounds and saved default. A nested-delegation runtime run was not launched to avoid unnecessary model cost.
- [x] Save and reload workflow; verify node positions, edges, tools, skills, prompts, communication, parallelism, and depth persist.
- [-] Run a separate cheap level-1 result-passing objective. Safely skipped to avoid another three-agent billed run after the retained level-2 and level-3 executions had already verified coordinator, dependency order, stage streaming, and synthesis.
- [x] Run a shared-scratchpad objective that has two agents append distinct markers to one file; verify both markers survive.
- [!] Run a Team dialogue objective requiring researcher/reviewer disagreement and resolution; inspect session evidence. Dialogue occurred, but revision duplicated file side effects and synthesis misreported final markers (BUG-026).
- [x] During a run verify active-node states, agent status, model, session/history counters, token/step/time/message metrics.
- [x] Open each node’s session details and verify messages, reasoning, tools, usage, and route persistence.
- [x] Reload during a team run and verify graph/status/session details reconnect without restarting work.
- [!] Cancel confirmation: the retained not-yet-started Kanban team task was used as the harmless probe, but **Cancel team run** executed immediately and archived every stage without a confirmation boundary (BUG-031).
- [x] Open Delete Execution and Remove Team confirmations and cancel; retain team and executions.
- [x] Rename team and verify persistence.
- [x] Export Team snapshot ZIP; verify non-empty download.
- [x] Import the QA Team snapshot using preview; verify collision/missing-env/quarantine information, then create from snapshot only if it is additive.

## 11. Kanban boards and todo tasks

- [x] Open Kanban; verify board picker, active board route, board summary, refresh, and notification stream.
- [x] Create `QA 2026-08-14 Board` with required name, generated/editable board ID, optional description, and color; retain it.
- [!] Validate blank name, invalid/duplicate board ID, and color controls. Blank is guarded, duplicate canonical ID shows an inline error, and color selection persisted; manually entered `BAD ID` is silently saved as `bad-id` (BUG-035).
- [x] Create a Backlog unassigned task with title, brief, priority High, and no schedule.
- [x] Create a Todo single-agent task assigned to the QA agent, priority Medium, selected skills, full expected outcome/constraints.
- [x] Create a Scheduled single-agent task: Run once, future date/time, timezone.
- [x] Create a repeating task: interval value/unit and timezone; ensure interval minimum validation. The current form expresses the interval in minutes and shows the fixed local `Asia/Saigon` timezone.
- [x] Create a Team task assigned to the QA discussion team; verify Final/Synthesis requirement and native DAG expansion. The retained task expanded to coordinator, three stages, and synthesizer.
- [!] Validate required title/assignee/team/synthesis/schedule fields and malformed date/interval input. Base required-field UX is inconsistent and silent (BUG-024); advanced schedule/team validation remains pending.
- [x] Verify column/list view toggle, task counts, list columns, and archived view without archiving anything.
- [x] Search by title and ID; clear search. Tag and agent terms were not present on the unassigned retained task.
- [x] Filter assignee, priority, and board column individually and in combination; clear filters.
- [x] Open task detail; verify brief, native/product status, assignee/team, priority, progress, schedule/timezone/next run, dependencies, skills.
- [x] Edit a QA task title/brief/priority/assignee/skills and verify reload persistence. Title, brief, priority, and skill state persisted; the unassigned edit UI did not expose an assignee control.
- [x] Add a QA comment/note and verify timestamp/author/event.
- [!] Move a QA task between Backlog, Todo, and In Progress using UI; avoid Done/Archive if it would stop intended execution. Backlog→Todo incorrectly schedules and traps an unscheduled task (BUG-025).
- [x] Run the assigned task and verify raw Running state, agent sidebar green status, worker heartbeat/run/session identifiers, and result. Retained task `t_b30732a1` completed with worker session `20260814_141056_46c7ba` and exact result `QA TODO RETAINED`; the earlier `t_bc7eaffa` run supplied the live sidebar/heartbeat evidence.
- [!] Verify task activity/events stream live and remain after reload. See BUG-003.
- [x] Open worker session from task and verify messages, usage, reasoning/tools, and back navigation.
- [x] Verify task dependencies gate execution and downstream receives upstream output where configured. The retained team workflow executed coordinator → primary → researcher → reviewer → synthesis with explicit `needs` and downstream summaries.
- [!] Verify team task graph nodes, concurrency, results, and synthesis are visible in task detail. Native coordinator/stage/synthesizer nodes are visible, but a Todo Team task is stranded as Scheduled with no start control and never dispatches (additional BUG-025 reproduction), so runtime results cannot populate.
- [x] Open Archive confirmation and choose Keep task; retain every task.

## 12. Automations / scheduled tasks

- [x] Open Automations; verify profile loading, filter/search, summary counts, loading/error/empty states.
- [x] Open Automation Library/Blueprints; inspect each blueprint description/defaults and Back behavior.
- [x] Create `QA 2026-08-14 Automation` for the QA agent with prompt and a future schedule; retain it.
- [!] Validate required agent/name/prompt/schedule, interval minimum, timezone, and invalid date input. Required-field discoverability fails (BUG-005).
- [x] Instantiate one harmless blueprint if additive and low-cost; verify editable defaults and persistence.
- [x] Open automation detail and verify configuration, prompt, next run, enabled/stopped state, and deep link.
- [x] Pause/start the QA schedule and verify status/next-run changes without deleting it.
- [x] Run Now once using `cx/gpt-5.6-luna`; verify running state, output Markdown, usage, execution graph, and run history.
- [x] Refresh/view all run history and inspect completed/failed/running presentation.
- [!] Add a workspace-file delivery target and verify output appears at the configured retained path. See BUG-006.
- [x] Add a Kanban-board delivery target to the QA board only if it creates additive retained data.
- [!] Validate email/channel delivery forms but do not send externally or save invalid/unapproved targets. Channel correctly has no selectable target, but unavailable Email is selected internally and enables Add (BUG-032); it was not submitted.
- [!] Remove-target confirmation: the retained targets were not clicked because the running UI source wires the trash control directly to removal with no confirmation state (BUG-033).
- [!] Open Delete Automation confirmation and cancel; retain the automation. Delete executes immediately without confirmation (BUG-012); job was restored from snapshot.
- [x] Reload automation detail route and verify state restoration.

## 13. Analytics and usage

- [x] Open Analytics; verify workspace pulse counts match current Agents, Kanban, and Teams.
- [x] Verify loading, retry/error, refresh, and empty-range states.
- [x] Filter From/To dates including today, invalid reverse range, and no-data range.
- [x] Change chart interval across available values and verify timeline labels/data.
- [x] Toggle Tokens/Cost and Input-vs-output breakdown.
- [x] Verify model usage table includes `cx/gpt-5.6-luna` and does not misattribute it to an unrelated provider/connection ID.
- [x] Verify traffic distribution, provider breakdown, requests, input/output/reasoning/cache tokens, and costs reconcile with recent session usage.
- [x] Filter Agent attribution to QA agents; clear filters and verify URL/state behavior if applicable.
- [x] Edit a QA agent monthly budget, save, reload, and verify value/share presentation.
- [x] Verify no stale/deleted-profile rows are presented as live current agents. The 13 live attribution rows exactly match the 13 current sidebar profiles; historical unattributed usage is summarized separately.

## 14. Runtime / sandbox VM

- [!] Open Settings > Runtime; verify provisioned/unprovisioned/loading/error states. Runtime details were inspected, but the normal desktop navigation is missing (BUG-008).
- [!] Refresh runtime status; verify endpoint, type, image, start time, CPU/memory/disk, uptime, network, OS, and health values. Values load, but memory usage is incorrectly zero (BUG-002).
- [x] Verify live metrics update every second without runaway requests or layout flicker.
- [-] If no sandbox exists, review Create flow and validation. Not applicable: this development environment already exposes a provisioned runtime with live CPU/memory/disk/network metrics.
- [x] Verify legacy `/sandbox` deep link redirects/restores Settings > Runtime.

## 15. Portable profiles and settings

- [x] Open Settings > Profiles; export portable profiles and verify downloadable archive.
- [x] Select a harmless exported archive for import preview; verify manifest profile/file counts, collisions, paused cron count, quarantined code, and warnings.
- [x] Do not overwrite existing IDs; apply only if import creates additive uniquely named QA profiles, then retain imported data.
- [x] Verify provider credentials are excluded and approvals/quarantined code/paused schedules are explained.
- [x] Verify Settings tabs Profiles, Context, Runtime, Connections, MCP, Blends each have stable URLs and restore on reload.
- [x] Verify legacy `/connections` and `/sandbox` deep links resolve to their Settings tabs.

## 16. Error handling, resilience, display, and accessibility

- [x] Check all tested flows for console errors, unhandled promise rejections, failed API loops, and credential/prompt leakage. A final nine-route navigation sweep produced no browser console/page/request/HTTP errors; expected validation failures rendered in the UI.
- [!] Check API errors render once, remain actionable, and can retry without full reload. Duplicate-board validation is inline and recoverable, but invalid login exposes a non-actionable internal code (BUG-027).
- [x] Check loading buttons disable duplicate submission and recover after success/error. Tested create/save/run actions recovered after valid and invalid submissions without duplicate retained records from one activation.
- [!] Check required fields use `*`, inline messages, focus/scroll to errors, and do not silently disable Save. See BUG-001 and BUG-005.
- [!] Check confirmation dialogs have correct target name, Cancel default, Escape/outside behavior, and focus return. Target copy and outside/Cancel dismissal are correct, but focus is not trapped/restored and Escape does nothing (BUG-036).
- [x] Check popups/menus close on Escape/outside click and remain within viewport. Agent menus passed both dismissal paths and their measured bounds stayed inside the window; modal Escape is tracked separately in BUG-036.
- [!] Check keyboard Tab order, visible focus, Enter/Space activation, modal focus trap, and screen-reader names for icon buttons. Main controls expose usable names and Enter/Space works, but modal focus escapes into the obscured sidebar (BUG-036).
- [x] Check long agent/team/task/provider/model/file names truncate with full text available where needed. Sidebar/task/delegation truncation exposes the full value through title/expanded detail.
- [x] Check narrow viewport and 125%/200% zoom for clipping, overlap, inaccessible controls, and horizontal overflow. Mobile navigation and temporary narrow/2× zoom sweeps remained usable with no document-level horizontal overflow; the window/zoom were restored.
- [x] Check light/dark contrast for statuses, disabled controls, warnings, terminal cards, charts, and form errors. Light, Dark, and System themes remained readable in the sampled states.
- [x] Check timestamps/timezones are consistent across session, team, Kanban, schedule, and analytics pages. UTC API timestamps consistently rendered in local `Asia/Saigon` time; retained schedules show the same zone.
- [x] Check pluralization/counts for 0, 1, and multiple items where reachable. Session steps, delegation workers/tools/slots, plans, boards, tasks, targets, and agent counts used the expected singular/plural forms.
- [x] Check all meaningful detail views produce stable shareable URLs and restore after reload/Back/Forward. Agent sessions, Team executions/nodes, Kanban tasks, Automation jobs, Settings tabs, and Analytics restored; the stale-session exception remains BUG-019.
- [!] Check stale IDs/deep links show not-found/recovery UI without silently selecting or creating the wrong entity. Session routes fail this requirement (BUG-019).

## 17. Final audit

- [x] Revisit every `[!]` item and link its bug directory from the Findings section below.
- [x] Revisit every `[-]` item and document the exact safety/environment reason.
- [x] Verify each bug README contains environment, severity, prerequisites, exact steps, expected, actual, reproducibility, evidence, impact, and suggested fix. All 36 packages passed the content audit, and every local Markdown evidence link resolves.
- [x] Verify evidence screenshots do not expose passwords, API keys, auth headers, or private unrelated data. OCR scan found no supplied password, Bearer token, or unmasked key; the connector evidence shows masked key prefixes only.
- [!] Confirm no data was deleted, archived, disconnected, overwritten, or cleaned up. No pre-existing user data or provider connection was changed, but BUG-012 removed/restored the QA automation and BUG-031 archived the five newly created QA team-task nodes before cancellation could be declined.
- [!] Confirm all QA-created data remains available for user review. All named agents, sessions, boards/tasks, Team, Blend, files, cron jobs, targets, and bug evidence remain; exceptions are the original BUG-012 automation run-output state and the pre-cancel runnable state of BUG-031.
- [-] Stop only processes started by this QA pass if requested. Not requested for this QA goal; `make dev` and the retained headed browser remain running so the user can inspect the data immediately. No unrelated process was stopped.
- [x] Record completion totals: **183 passed / 44 failed / 10 skipped / 0 not run** (237 checklist rows).

## Findings

- [BUG-001 — Agent creation exposes an internal API and silently disables submission](BUG-001-agent-create-form/README.md)
- [BUG-002 — Runtime reports zero memory usage on this cgroup layout](BUG-002-runtime-memory-zero/README.md)
- [BUG-003 — Reloaded Kanban task hides persisted worker activity and events until Refresh](BUG-003-kanban-history-missing-after-reload/README.md)
- [BUG-004 — Completed team run remains at 58% progress](BUG-004-team-completed-progress-58/README.md)
- [BUG-005 — New automation hides required-field validation](BUG-005-automation-required-fields-silent/README.md)
- [BUG-006 — Successful automation does not deliver to a workspace file](BUG-006-cron-workspace-delivery-stays-pending/README.md)
- [BUG-007 — Approved skill write is reported as still awaiting approval](BUG-007-approved-skill-write-reported-pending/README.md)
- [BUG-008 — Agent settings are not reachable through desktop navigation](BUG-008-agent-settings-not-reachable/README.md)
- [BUG-009 — Browser autofill hides every agent in the sidebar](BUG-009-agent-search-browser-autofill/README.md)
- [BUG-010 — Login password autofills the provider API-key field](BUG-010-login-password-autofills-api-key/README.md)
- [BUG-011 — MCP editor accepts and persists invalid server schemas](BUG-011-mcp-invalid-schema-accepted/README.md)
- [BUG-012 — Delete Automation executes immediately without confirmation](BUG-012-automation-delete-no-confirmation/README.md)
- [BUG-013 — Background research worker opens a visible user-browser tab](BUG-013-background-worker-opens-user-browser-tab/README.md)
- [BUG-014 — Authenticated application links open signed out in a new tab](BUG-014-new-tab-loses-authentication/README.md)
- [BUG-015 — Create-file dialog silently disables submission for a required name](BUG-015-workspace-create-file-required-field-silent/README.md)
- [BUG-016 — Generated PDF renders source URLs as plain text instead of clickable links](BUG-016-generated-pdf-links-not-clickable/README.md)
- [BUG-017 — Stop response cancels immediately and leaves no visible terminal state](BUG-017-stop-response-no-confirmation-or-terminal-state/README.md)
- [BUG-018 — Assistant feedback and More buttons are inert](BUG-018-assistant-feedback-more-buttons-noop/README.md)
- [BUG-019 — A stale session URL silently redirects to an unrelated worker session](BUG-019-stale-session-route-silently-selects-unrelated-session/README.md)
- [BUG-020 — Session rename cannot be reached with a real double-click](BUG-020-session-rename-double-click-unreachable/README.md)
- [BUG-021 — Task notification bell completely overlaps Test Agent](BUG-021-notification-bell-overlaps-test-agent/README.md)
- [BUG-022 — Install Skill silently ignores malformed identifiers](BUG-022-skill-install-invalid-input-silent/README.md)
- [BUG-023 — Custom provider form enables Save for a malformed Base URL](BUG-023-custom-provider-accepts-malformed-base-url/README.md)
- [BUG-024 — New Kanban task silently disables Create for required fields](BUG-024-kanban-new-task-required-fields-silent/README.md)
- [BUG-025 — Moving an unscheduled task to Todo makes it immovable as scheduled](BUG-025-kanban-todo-task-cannot-move-back/README.md)
- [BUG-026 — Team discussion duplicates side effects and misreports markers](BUG-026-team-discussion-duplicates-side-effects-and-misreports/README.md)
- [BUG-027 — Invalid login displays an internal error code](BUG-027-login-error-exposes-internal-code/README.md)
- [BUG-028 — Unsupported workspace files have no preview fallback](BUG-028-workspace-unsupported-preview-silent/README.md)
- [BUG-029 — Workspace file name accepts parent-directory traversal](BUG-029-workspace-file-name-allows-parent-traversal/README.md)
- [BUG-030 — Kanban task picker allows disabled skills](BUG-030-kanban-task-picker-allows-disabled-skills/README.md)
- [BUG-031 — Cancel team run executes immediately without confirmation](BUG-031-cancel-team-task-no-confirmation/README.md)
- [BUG-032 — Unconfigured email delivery can be submitted](BUG-032-automation-unconfigured-email-add-enabled/README.md)
- [BUG-033 — Automation delivery-target removal has no confirmation](BUG-033-automation-remove-target-no-confirmation/README.md)
- [BUG-034 — Context editor silently discards unsaved changes on close](BUG-034-context-editor-discards-unsaved-close/README.md)
- [BUG-035 — Invalid board ID is silently normalized and created](BUG-035-kanban-invalid-board-id-silently-normalized/README.md)
- [BUG-036 — Confirmation dialogs do not trap focus or close with Escape](BUG-036-confirm-dialog-no-focus-trap-or-escape/README.md)

## Execution notes

- Checklist was derived from the current React routes/components, API clients, and visible controls before browser execution.
- Destructive completion actions are intentionally excluded by the user’s data-preservation requirement; their validation and confirmation boundaries remain testable.
