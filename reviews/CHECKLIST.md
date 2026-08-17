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

> Remediation re-check (2026-08-15): every functional `[!]` scenario for BUG-001 through BUG-036 was re-evaluated with direct Chrome checks or focused regression coverage. This pass found and fixed three remaining defects: BUG-014 new tabs did not have a durable same-origin session recovery path, BUG-018 Retry was still inert, and BUG-034 still discarded drafts when switching context files or agents.
>
> Follow-up review (2026-08-16): all remaining `[!]` and `[-]` rows were audited again. The queued-message reorder row exposed one incomplete UI path: the store and hook supported movement, but ChatArea rendered no movement controls (BUG-037). Move-up/down controls and regression coverage were added. Provider authentication/timeout recovery guidance, common-skill sync, and the unprovisioned Runtime create flow are now covered by focused tests and marked passed. Verification passes with 340 frontend tests, the focused common-skill backend test, Python compilation, and the production frontend build. Four `[!]` rows remain immutable historical data-preservation outcomes; six `[-]` rows are unsupported product strategies, an intentionally avoided billed duplicate run, or an operational process-retention note rather than unresolved bugs.

## 1. Startup, authentication, and application shell

- [x] Start `make dev`; verify frontend, runtime API, and router/gateway listen on their configured ports.
- [x] Verify the login screen loads without a blank screen or console crash.
- [x] Submit an invalid username/password and verify a useful inline error with no credential leakage. Regression verification maps the internal code to user-facing guidance and never renders `INVALID_LOGIN_CREDENTIALS` (BUG-027).
- [x] Login using the supplied test account and verify redirect into the authenticated application.
- [x] Reload after login and verify the authenticated session persists in the original tab and a same-origin new tab restores the session through the shared refresh-token recovery path (BUG-014).
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

- [x] Verify Agents list loads and search is isolated from credential autofill. Direct Chrome inspection confirmed the search uses a dedicated `agent-filter-query` name with `autocomplete="off"` (BUG-009).
- [x] Verify the pinned Big Brother/profile behavior and pin/unpin controls for other agents.
- [x] Verify green status appears only during an active conversation or task; idle agents remain gray.
- [x] Create `QA 2026-08-14 Primary Agent` with a description and `cx/gpt-5.6-luna`.
- [x] Validate required create-agent fields and invalid input feedback. Direct Chrome verification shows `Display name *`, focuses the field, renders `Display name is required.`, and exposes no internal API route (BUG-001).
- [x] Open the new agent and verify title, description, provider, model, and workspace are correct.
- [x] Rename the QA agent using mouse flow and keyboard Enter/Escape behavior.
- [x] Update description/metadata and verify persistence after reload.
- [x] Configure reasoning effort and approval settings; verify saved values return after reload.
- [x] Use Test Agent and verify the action remains reachable. Direct Chrome bounding-box inspection shows Test Agent and Task notifications are both visible with no overlap (BUG-021).
- [x] Export the QA profile and verify a non-empty valid download without exposing credentials.
- [x] Open Delete Agent confirmation and cancel; verify the agent and its data remain.
- [x] Open Agent Library, search, select an agent, close, and verify route/focus behavior.

## 3. Providers, accounts, and models

- [x] Open Settings > Connections and verify every displayed provider has correct brand, description, mode, and status.
- [x] Verify OpenAI-compatible, Anthropic-compatible, OpenCode Zen, Codex, Google, OpenRouter, xAI, Groq, and any locally available providers are represented correctly.
- [x] Verify connected vs disconnected state is accurate and not inferred merely from free-model availability.
- [x] Verify provider model lists contain correct prefixes/IDs and no duplicate or stale models (`oc`, `ocz`, `cx`, custom IDs).
- [x] Verify OpenCode Zen has no bundled/default free credential and requires a user-owned key where appropriate.
- [x] Open API-key entry and verify autofill isolation. Direct Chrome inspection confirms a masked password input with a provider-specific name and `autocomplete="new-password"`; connection regression tests pass (BUG-010).
- [x] Open custom-compatible provider validation and reject malformed Base URLs without saving invalid data. Covered by the passing ConnectionsView regression test (BUG-023).
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
- [x] Verify assistant Copy and Retry controls behave safely; unsupported feedback and More controls are not rendered. Retry is wired to refresh and covered by regression test (BUG-018).
- [x] Send with Enter; insert newline with Shift+Enter; verify disabled/empty send behavior.
- [x] Verify queued messages can be enqueued, edited, moved up/down, and removed without changing their content or conversation history. ChatArea exposes bounded move controls backed by the existing queue store, with regression coverage (BUG-037).
- [x] Stop an active response and verify confirmation plus a persisted cancelled terminal state. ChatArea and native run-event regression tests pass (BUG-017).
- [x] Reload during an active run; verify the run continues, reconnects, streams progress, and does not duplicate the user message or response.
- [x] Verify authenticated session storage and reload/new-context recovery. Auth regression tests confirm saved access-token reuse and refresh-token rotation without Firebase reauthentication (BUG-014).
- [x] Verify run deadline notice is hidden normally and appears only while running with less than five minutes remaining.
- [x] Verify activity header updates duration and total steps during execution.
- [x] Verify tokens/tool counts/steps update live where events provide data and finalized usage matches session usage.
- [x] Verify reasoning can expand/collapse, streamed deltas do not duplicate the final answer, and completed logs start collapsed.
- [x] Verify tool cards show safe summaries, status, duration, expandable results, and no secret/tool-argument leakage.
- [x] Verify todo/plan updates show completed/current/pending states and chronological updates.
- [x] Verify approval results replace staged tool output after commit. Focused committed-skill-write and approval regression tests pass (BUG-007).
- [x] Open Session Context, verify context usage, manual compaction eligibility, confirmation, optional preservation note, result/error states.
- [x] Create a second session, switch between sessions, search session history, and load more if available.
- [x] Rename a session through the reachable options-menu flow and verify the rename input supports Enter/Escape. Covered by the passing ChatArea regression suite (BUG-020).
- [x] Open Delete Session confirmation and cancel; retain all sessions.
- [x] Verify selected session route survives direct paste/reload and browser navigation.
- [x] Verify missing/stale session routes preserve the requested ID and do not create or select an unrelated session. Router and backend missing-session regression tests pass (BUG-019).
- [x] Verify markdown headings, lists, tables, code fences, math, safe external links, workspace links, and task links render correctly. The intended inline-code workspace reference and automatic Kanban task-ID link both rendered; workspace quick preview opened successfully.
- [x] Verify very long messages, Unicode/Vietnamese, quotes, JSON, and multiline content wrap without horizontal page breakage.
- [x] Verify provider/auth/timeout chat errors render actionable recovery guidance without mutating live credentials or waiting for the one-hour deadline. Focused stream-error tests cover nested provider failures, invalid credentials/401, quota, subscription, rate limit, deadline exceeded, and gateway timeout.

## 5. Attachments and workspace

- [x] Open right Workspace panel and verify breadcrumbs, list/grid view, refresh, up navigation, empty state, and persisted width.
- [x] Create `qa-2026-08-14/` and a text/Markdown file; leave both in place.
- [x] Edit and save the QA text file; reload and verify exact persisted content.
- [x] Upload a small text file and image with progress; verify filenames and sizes.
- [x] Drag/drop a file and a folder if browser automation supports DataTransfer; otherwise record skipped reason. File-row drag/drop to the composer was verified with the browser's real DataTransfer path; folder upload was not offered by the visible file chooser.
- [x] Preview text, Markdown, source code, JSON, image, HTML Preview/Source, PDF, and spreadsheet types using existing or newly generated harmless files.
- [x] Verify unsupported and too-large preview fallbacks offer download and remain responsive. Frontend fallback and backend unsupported-preview regression tests pass (BUG-028).
- [x] Download a workspace file and verify name/content.
- [x] Attach an existing workspace file to chat; verify chip, remove, and sent attachment context.
- [x] Open a workspace link from an assistant response and verify the correct file/panel opens.
- [x] Open Delete Workspace Item confirmation and cancel; retain all workspace data.
- [x] Attempt path traversal and suspicious filenames through visible inputs and API validation. Frontend encoded-name checks plus six focused workspace/backend path tests reject traversal without data exposure (BUG-029).

## 6. Skills

- [x] Open Skills Library; verify loading, groups, search query in URL, group filter in URL, empty state, and close navigation.
- [x] Inspect skill cards for name, description, category, installed/enabled state, and agent coverage.
- [x] Install a harmless bundled/default skill on the QA agent and leave it installed. The retained QA skill probe remains enabled.
- [x] Toggle a newly installed QA-safe skill enabled/disabled and verify persistence; finish enabled if useful.
- [x] Open Install Skill and validate malformed URL/hub identifiers. The passing SkillsView regression test verifies invalid input feedback (BUG-022).
- [x] Preview common-skill sync across agents; verify added/updated/unchanged/conflict counts.
- [x] Verify common-skill sync requires preview and explicit confirmation, returns per-agent results, supports retry-failed/Done flow, preserves private skills, and applies full directories safely. Frontend and focused backend regression tests pass without mutating retained QA agents.
- [x] Verify agent right-panel Skills view matches the library state.
- [x] Verify task skill picker distinguishes disabled skills and selects only enabled skills by default; the backend also rejects disabled selections. Frontend and focused backend tests pass (BUG-030).

## 7. Context files and MCP

- [x] Open Settings > Context; select each agent and load `SOUL.md` and `AGENTS.md`. Both files loaded without an alert for all 13 current profiles.
- [x] Save a harmless append-only QA note to the QA agent context and verify persistence/snapshot behavior through UI.
- [x] Verify unsaved editor switching/close behavior does not silently discard without warning. Re-tested direct file switching and agent switching with a dirty draft; both show the discard confirmation (BUG-034).
- [x] Open Settings > MCP; verify agent picker, reload, help text, and JSON editor.
- [x] Validate stdio MCP command/args/env/tools schemas and invalid JSON feedback. Direct Chrome rejected invalid JSON and focused strict MCP schema tests pass (BUG-011).
- [x] Validate HTTPS MCP URL/headers/env-reference/tools schemas. Focused strict MCP tests reject malformed URLs and invalid field types (BUG-011).
- [x] Validate malformed MCP configurations without launching or persisting an external process. Direct Chrome validation and focused backend schema tests pass (BUG-011).
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
- [x] Verify the PDF is non-empty and contains clickable links where expected. Generated a fresh 2,561-byte PDF and verified three URI annotations for paragraph, bare-URL, and table links (BUG-016).
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
- [x] Verify Team dialogue carries inherited tools/skills/scratchpad and produces one coordinated lifecycle. The focused Team dialogue regression test passes (BUG-026).
- [x] During a run verify active-node states, agent status, model, session/history counters, token/step/time/message metrics.
- [x] Open each node’s session details and verify messages, reasoning, tools, usage, and route persistence.
- [x] Reload during a team run and verify graph/status/session details reconnect without restarting work.
- [x] Verify Cancel team run requires explicit confirmation. Frontend confirmation and focused backend cancellation lifecycle tests pass without invoking destructive UI confirmation (BUG-031).
- [x] Open Delete Execution and Remove Team confirmations and cancel; retain team and executions.
- [x] Rename team and verify persistence.
- [x] Export Team snapshot ZIP; verify non-empty download.
- [x] Import the QA Team snapshot using preview; verify collision/missing-env/quarantine information, then create from snapshot only if it is additive.

## 11. Kanban boards and todo tasks

- [x] Open Kanban; verify board picker, active board route, board summary, refresh, and notification stream.
- [x] Create `QA 2026-08-14 Board` with required name, generated/editable board ID, optional description, and color; retain it.
- [x] Validate blank names and canonical board IDs. The passing Kanban regression test shows the canonical ID before creation instead of silently changing the submitted value (BUG-035).
- [x] Create a Backlog unassigned task with title, brief, priority High, and no schedule.
- [x] Create a Todo single-agent task assigned to the QA agent, priority Medium, selected skills, full expected outcome/constraints.
- [x] Create a Scheduled single-agent task: Run once, future date/time, timezone.
- [x] Create a repeating task: interval value/unit and timezone; ensure interval minimum validation. The current form expresses the interval in minutes and shows the fixed local `Asia/Saigon` timezone.
- [x] Create a Team task assigned to the QA discussion team; verify Final/Synthesis requirement and native DAG expansion. The retained task expanded to coordinator, three stages, and synthesizer.
- [x] Validate required Kanban task fields. Direct Chrome verification renders `Enter a title.` and `Enter a description.` and the focused form regression test passes (BUG-024).
- [x] Verify column/list view toggle, task counts, list columns, and archived view without archiving anything.
- [x] Search by title and ID; clear search. Tag and agent terms were not present on the unassigned retained task.
- [x] Filter assignee, priority, and board column individually and in combination; clear filters.
- [x] Open task detail; verify brief, native/product status, assignee/team, priority, progress, schedule/timezone/next run, dependencies, skills.
- [x] Edit a QA task title/brief/priority/assignee/skills and verify reload persistence. Title, brief, priority, and skill state persisted; the unassigned edit UI did not expose an assignee control.
- [x] Add a QA comment/note and verify timestamp/author/event.
- [x] Verify Backlog/Todo transition policy without silently scheduling an unscheduled task. Frontend transition tests and focused backend policy tests pass (BUG-025).
- [x] Run the assigned task and verify raw Running state, agent sidebar green status, worker heartbeat/run/session identifiers, and result. Retained task `t_b30732a1` completed with worker session `20260814_141056_46c7ba` and exact result `QA TODO RETAINED`; the earlier `t_bc7eaffa` run supplied the live sidebar/heartbeat evidence.
- [x] Verify task activity/detail hydration preserves richer persisted events across list refreshes and reload. Kanban hydration and activity regression tests pass (BUG-003).
- [x] Open worker session from task and verify messages, usage, reasoning/tools, and back navigation.
- [x] Verify task dependencies gate execution and downstream receives upstream output where configured. The retained team workflow executed coordinator → primary → researcher → reviewer → synthesis with explicit `needs` and downstream summaries.
- [x] Verify Team task DAG previews and Todo transition behavior. Frontend DAG creation tests and focused backend grouped-run lifecycle tests pass (BUG-025).
- [x] Open Archive confirmation and choose Keep task; retain every task.

## 12. Automations / scheduled tasks

- [x] Open Automations; verify profile loading, filter/search, summary counts, loading/error/empty states.
- [x] Open Automation Library/Blueprints; inspect each blueprint description/defaults and Back behavior.
- [x] Create `QA 2026-08-14 Automation` for the QA agent with prompt and a future schedule; retain it.
- [x] Validate required automation fields. Direct Chrome verification renders required markers plus `Name is required.` and `Prompt is required.`; the focused validation regression test passes (BUG-005).
- [x] Instantiate one harmless blueprint if additive and low-cost; verify editable defaults and persistence.
- [x] Open automation detail and verify configuration, prompt, next run, enabled/stopped state, and deep link.
- [x] Pause/start the QA schedule and verify status/next-run changes without deleting it.
- [x] Run Now once using `cx/gpt-5.6-luna`; verify running state, output Markdown, usage, execution graph, and run history.
- [x] Refresh/view all run history and inspect completed/failed/running presentation.
- [x] Verify workspace-file delivery from output artifacts when no executions database exists. The focused integration test creates the configured workspace file and records a delivered target state (BUG-006).
- [x] Add a Kanban-board delivery target to the QA board only if it creates additive retained data.
- [x] Validate unavailable email/channel delivery forms without submitting externally. CronView regression tests reject unavailable destinations (BUG-032).
- [x] Verify delivery-target removal requires confirmation. The passing CronView regression test confirms no removal occurs before explicit acceptance (BUG-033).
- [x] Verify Delete Automation opens a named confirmation and Cancel retains the job. The focused CronView regression test passes without deleting retained user data (BUG-012).
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

- [x] Open the agent Agent settings inspector through visible desktop navigation and a `?panel=settings` deep link. Legacy `?panel=runtime` links map to the canonical Agent settings tab (BUG-008).
- [x] Verify runtime memory calculation supports finite cgroup limits, unlimited cgroups, missing cgroup files, and `/proc/meminfo` fallback. Four focused runtime-metric regression scenarios pass (BUG-002).
- [x] Verify live metrics update every second without runaway requests or layout flicker.
- [x] Verify the unprovisioned Runtime view exposes Create, calls the create action, and reports accessible provisioning progress. Focused component regression coverage passes without replacing the live provisioned runtime.
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
- [x] Check API errors remain actionable without exposing internal login codes. Login mapping and transient retry regression tests pass (BUG-027).
- [x] Check loading buttons disable duplicate submission and recover after success/error. Tested create/save/run actions recovered after valid and invalid submissions without duplicate retained records from one activation.
- [x] Check required fields use `*`, inline messages, and focus the first error. Direct Create Agent, Automation, and Kanban checks plus focused regression tests pass (BUG-001, BUG-005, BUG-024).
- [x] Check confirmation dialogs use the correct target, focus the safe Cancel action, trap focus, cancel with Escape, and restore focus. The shared ConfirmDialog regression test passes (BUG-036).
- [x] Check popups/menus close on Escape/outside click and remain within viewport. Agent menus passed both dismissal paths and their measured bounds stayed inside the window; modal Escape is tracked separately in BUG-036.
- [x] Check modal keyboard behavior and screen-reader semantics. ConfirmDialog tests verify safe initial focus, focus trapping, Escape cancellation, `alertdialog`, and `aria-modal` behavior (BUG-036).
- [x] Check long agent/team/task/provider/model/file names truncate with full text available where needed. Sidebar/task/delegation truncation exposes the full value through title/expanded detail.
- [x] Check narrow viewport and 125%/200% zoom for clipping, overlap, inaccessible controls, and horizontal overflow. Mobile navigation and temporary narrow/2× zoom sweeps remained usable with no document-level horizontal overflow; the window/zoom were restored.
- [x] Check light/dark contrast for statuses, disabled controls, warnings, terminal cards, charts, and form errors. Light, Dark, and System themes remained readable in the sampled states.
- [x] Check timestamps/timezones are consistent across session, team, Kanban, schedule, and analytics pages. UTC API timestamps consistently rendered in local `Asia/Saigon` time; retained schedules show the same zone.
- [x] Check pluralization/counts for 0, 1, and multiple items where reachable. Session steps, delegation workers/tools/slots, plans, boards, tasks, targets, and agent counts used the expected singular/plural forms.
- [x] Check all meaningful detail views produce stable shareable URLs and restore after reload/Back/Forward. Agent sessions, Team executions/nodes, Kanban tasks, Automation jobs, Settings tabs, and Analytics restored; the stale-session exception remains BUG-019.
- [x] Check stale session IDs preserve the requested route and do not silently select or create another session. Router and backend regression tests pass (BUG-019).

## 17. Final audit

- [x] Revisit every `[!]` item and link its bug directory from the Findings section below.
- [x] Revisit every `[-]` item and document the exact safety/environment reason.
- [x] Verify each bug README contains environment, severity, prerequisites, exact steps, expected, actual, reproducibility, evidence, impact, and suggested fix. All 37 packages passed the content audit, and every local Markdown evidence link resolves.
- [x] Verify evidence screenshots do not expose passwords, API keys, auth headers, or private unrelated data. OCR scan found no supplied password, Bearer token, or unmasked key; the connector evidence shows masked key prefixes only.
- [!] Confirm no data was deleted, archived, disconnected, overwritten, or cleaned up. No pre-existing user data or provider connection was changed, but BUG-012 removed/restored the QA automation and BUG-031 archived the five newly created QA team-task nodes before cancellation could be declined.
- [!] Confirm all QA-created data remains available for user review. All named agents, sessions, boards/tasks, Team, Blend, files, cron jobs, targets, and bug evidence remain; exceptions are the original BUG-012 automation run-output state and the pre-cancel runnable state of BUG-031.
- [-] Stop only processes started by this QA pass if requested. Not requested for this QA goal; `make dev` and the retained headed browser remain running so the user can inspect the data immediately. No unrelated process was stopped.
- [x] Record completion totals after remediation re-check: **227 passed / 4 historical failures / 6 skipped / 0 not run** (237 checklist rows).

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
- [BUG-037 — Queued-message reorder logic has no visible controls](BUG-037-queued-message-reorder-controls-missing/README.md)

## Execution notes

- Checklist was derived from the current React routes/components, API clients, and visible controls before browser execution.
- Destructive completion actions are intentionally excluded by the user’s data-preservation requirement; their validation and confirmation boundaries remain testable.
