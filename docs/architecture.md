# Architecture

XNOBrain is a Python modular monolith layered onto the original Hermes CLI
FastAPI application. The sibling `xnobrain-ui` image serves the React UI, and
the browser reaches the UI and API through Traefik.
The managed runtime container starts exactly one application process: FastAPI
and Hermes on port 8642. router is not installed in the workspace.

```text
Traefik -> xnobrain-ui (React)
        -> FastAPI (Hermes native routes + XNOBrain routes)
             -> services -> repositories -> profile/config files
             -> integrations -> Hermes CLI/core
             -> integrations -> centralized LLM router
```

XNOBrain route assembly is centralized in `xnobrain/routes/setup.py`.
Handlers translate HTTP and SSE, services coordinate business rules,
repositories persist atomic files, and integrations isolate upstream APIs.
Pydantic models are bound to routes and generate
`/xnobrain/api/runtime/swagger_docs` and `/xnobrain/api/runtime/openapi.json`.

Hermes remains authoritative for its native sessions, cron, MCP, config,
skills, tools, provider, webhook, and gateway APIs. XNOBrain adds stable UI
compatibility APIs for agent lifecycle, per-profile files, snapshots, teams,
portable bundles, and streaming runs.

## Profiles and persistence

The default profile is `HERMES_ROOT_PROFILE`. Named profiles live at
`DATA_DIR/profiles/<agent-id>` and are discovered through the Hermes CLI
profile inventory. Hermes uses its own profile-local `state.db` for native session
history; that remains an upstream profile file, not an XNOBrain schema. FT0015 adds
one separate, Runtime-mediated SQLite app store at
`DATA_DIR/agent-apps/<agent-id>/app.sqlite3` on verified persistent workspace
storage. The human tenant/subject owns it; the agent is a scoped writer. It is not
Control's database, is not stored in the portable profile directory, and persists
when the user explicitly retains a page after removing its writer. It contains
validated app revisions, typed records, provenance, action/schedule receipts and
activity. Attachments/backups live in that app directory. No SQL or filesystem
path is selected by the browser.

Every named profile owns config, prompts, skills, memory, workspace, session,
cron, log, MCP, and snapshot data. Portable bundles include every regular file
in the profile directory while redacting secret values. Imported credential
files are discarded, approvals reset to manual, and cron jobs are paused.

New named profiles are seeded from the installer-managed
`HERMES_ROOT_PROFILE/profile-template`, whose default model is `auto`. They do
not copy the mutable default profile's persona, memory, plugins, or workspace.
Enabled global skills are inherited separately. Provider credentials are never
copied into a profile or workspace.

## Local runtime boundary

Chat selects a profile and invokes the original Hermes CLI/core from the one
FastAPI process. Streaming emits structured `run.started`, `message.delta`,
terminal run events, and supports process interruption. Hermes' approval core
remains the resolver for pending approvals.

Managed LLM inference calls the centralized router directly with a Control-issued
API key. Runtime does not host the router, retain provider credentials, or
read router usage storage. Profile files, tools, memory, and other local agent behavior
remain local. The optional
OpenTelemetry export is disabled by default and can target the configured collector. When
`OTEL_ENABLED=true`, the runtime exports metadata-only spans only to the
Compose collector or a loopback endpoint.

### Router protocol and failed inference

The Runtime uses GoRouter's `/v1/chat/completions` contract for prefixed models.
In GoRouter v0.1.0, `cx/` is translated to the Codex Responses backend and
`cc/` to Anthropic Messages. The public `/v1/responses` handler also delegates
to the same chat routing pipeline; switching the public endpoint does not
bypass credential health, quota, or provider request validation.

A structured engine result with `failed: true` terminates the Runtime stream
with `run.failed`, even when `final_response` contains diagnostic text or
partial deltas have already been delivered. Diagnostic final output is not
emitted as a synthetic assistant delta. Cancellation retains precedence.
A router 503 reporting no healthy credentials requires checking the scoped
principal's eligible provider connections; a generic upstream 400 alone does
not identify the rejected field. Do not infer either cause from a successful
request using a different provider or principal.

### GoRouter v0.1.0 streaming and telemetry

GoRouter v0.1.0 must run with `OTEL_ENABLED=false` when it serves streaming
inference. Its Fiber handler registers a lazy response stream after starting
the provider request with the handler context. With OTEL middleware enabled,
that context ends when the handler returns, which can terminate the upstream
response after its first buffered fragment. The client then sees HTTP 200 and
`[DONE]` without a finish reason; Router records 502, and repeated failures can
produce `503 no healthy credentials available`. Non-streaming calls can appear
healthy because they finish before handler return.

Runtime does not disable token streaming or infer transport behavior from model
or provider prefixes. All centralized models use the router's configured
OpenAI-compatible streaming contract. Re-enable router traces only after the
router owns a stream-lifetime context that survives handler return, and verify
text, tool calls, terminal finish reason, usage, cancellation, and health state.

### Router tool-name wire codec

The centralized router boundary aliases Runtime tool names to stable neutral
wire identifiers and restores the original names before Hermes validates,
persists, displays, or executes calls. Historical assistant/tool messages and
explicit tool choices use the same aliases, and delegated child agents install
the same codec. The original tool identity is included in each description so
models retain semantic selection context.

This is provider-independent: every model sent through the centralized router
uses the same codec. It avoids upstream coding-client reserved-name
interactions without dropping tools, reducing schemas, switching API mode, or
checking public model prefixes. The alias is derived only from the public tool
name and never contains credentials or tool arguments.

### Custom-page native scheduling coordination

Native cron owns recurrence, fire claims, execution history and delivery. Approved
app-owned `xcp_` jobs dispatch into the existing durable conversation runner, not
an unrestricted cron prompt or separate timer. Personal conversation/payer, exact
page/action digests, revision, occurrence count and budget are rechecked. The
330-second native-to-async bridge exceeds the maximum 300-second approved action;
app-native claims therefore use a minimum 600-second TTL. TTL is not a process lock.

Runtime holds a per-agent/schedule kernel lock through claim, async execution,
native history/output finalization and reconciliation. A second live worker returns
not-fired without changing the schedule or admitting work. Shared lifecycle/update
activity prevents removal/checkpoint before native finalization is done. The lock
has no clock expiry and releases on close/process death; uncertain durable receipts
remain blocked until explicit recovery. Zero-content hashed coordination inodes live
outside deletable app/profile trees and are validated/excluded by update manifests.
Old Runtime processes do not acquire the new fence, so mixed-process operation is
not a supported isolation claim. The supported local persistent filesystem and
cooperating Runtime processes are assumptions; this is not a same-user shell sandbox.

### Community publication versus private app portability

Community definitions use an allowlist, not the full-profile portability exporter.
Private app SQLite, attachments, backups and runtime history are not publication
artifacts. Public text must be a regular, single-link inode; reserved runtime paths
are excluded even within skill assets. This is not a classifier for intentionally
copied private prose. Full profile backups remain private artifacts and retain their
existing contents policy; nested managed DATA_DIR is excluded from them.

Community profile install/update/uninstall takes the existing per-agent lifecycle
and app storage/update gates. An update cannot mutate a live executor's definition,
and uninstall cannot silently orphan an unresolved private app. Reinstall cannot
reattach a replacement writer to retained data. No second lifecycle database or
scheduler is introduced; combined Community retain/uninstall reconciliation remains
an explicit future integration rather than automatic consent.

### Workspace working overlay and Python environments

The installer seeds `workspace/AGENTS.md` (durable product instructions,
unchanged marketplace/UI/blueprint contract). Profile working rules live in
the packaged `HERMES.md` template and the profile-root copy beside
`config.yaml`, not in the user workspace. Big Brother's profile-root copy
refreshes with snapshots on installation; named-profile copies are backfilled
only when missing. Leftover `workspace/HERMES.md` files are removed on apply.
The independent `profile-template` never inherits Big Brother edits.

Upstream context discovery selects HERMES before AGENTS, rather than merging.
The Runtime conversation prompt adapter injects workspace `AGENTS.md` plus the
internal HERMES overlay for new/cached and resumed prompts, including Big
Brother, without changing the upstream loader. Working rules are authored only
in HERMES. Big Brother remains free of the named-agent CWD lock.

Profile Python environments use `/opt/data/python/.<profile-id>-venv`, including
`.big-brother-venv`. The container entrypoint provisions the shared root as 0700
for the runtime identity on the existing persistent data mount. It shares that
mount's quota (not a new unlimited disk); persistence across replacement depends
on retaining the data volume. Environments are not portable profile artifacts.
Use the explicit `uv --no-cache venv` and `uv --no-cache pip --python` command
shapes in HERMES, remaining in workspace CWD. No workspace `.venv`, separate uv
cache, runtime-interpreter relocation, or office-tools changes are needed.
These are model instructions, not command enforcement or an OS sandbox. The
named-agent exception permits only its own environment, never arbitrary `/opt`
writes or external deliverables. Full-disk errors must be reported, not bypassed.
