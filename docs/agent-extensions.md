# Agent extensions

This is the current contract replacing the historical plan 007 implementation
sketches. Runtime owns profile-local executable extensions and persistence;
`xnobrain-ui` owns the browser interface; authenticated Control selects the
personal workspace. XNORouter remains a separate service.

## Exposure and authority

`FT_ENABLE_AGENT_EXTENSIONS` defaults to true; malformed values disable the new
routes. The UI consumes `VITE_FT_ENABLE_AGENT_EXTENSIONS`. Control and Runtime
reject extension management in organization contexts. A feature switch grants
no ownership. All profile and operation calls require a Control-verified subject
and tenant; an imported profile's durable owner marker must match.

The Plugins destination is `/plugins?agent=<id>&tab=catalog|installed|hooks`.
`panel=tools` opens per-agent capabilities on the existing agent route. Source
and UI are in separate repositories; no React source belongs in Runtime.

## Catalog compatibility

The independent catalog covers all 31 referenced Openbot entries. Openbot is a
coverage reference only, not a runtime dependency or a source of copied code,
text, icons, or manifests; see [catalog provenance](plugin-catalog.md).

A recipe's `installability` describes its transport/auth requirements. Runtime
additionally returns `engine_capabilities` and per-entry `capability`. Clients
must honor both. Native `mcp_servers` discovery in the pinned engine still
registers process-global tools (the A/B test in `test_extension_engine.py`
records this), so catalog servers never go through it. They are stored under
the separate `xnobrain_managed_mcp_servers` config key and run through
`integrations/managed_mcp.py`:

- `ManagedMCPServerTask` subclasses the native `MCPServerTask` and registers
  every tool with the profile's registry scope, so another profile cannot see
  or dispatch it. Refresh and disconnect restore only that profile's entries.
- Server IDs are an HMAC of profile path, profile generation and logical name,
  so same-named servers in two profiles get separate sessions and a recreated
  profile cannot reuse an old session.
- Catalog API keys and OAuth tokens live in Control. Runtime stores only a Control connection ID
  and connects through Control's private MCP proxy with an in-memory grant bound
  to the active conversation run on each request; management calls use a
  10-minute fallback grant. The key never enters the profile. Header placeholders
  for other managed servers resolve only from the profile's own secret scope.
  Direct public HTTPS transport uses the pinned Hermes SSRF-safe client.
- Before each native conversation run, `refresh_profile_mcp` reconciles the
  profile's sessions, then `managed_mcp.install_tools` adds the connected
  tools to that agent. Native runs never call Hermes `discover_mcp_tools()`.

The profile's own `mcp_servers` use the same adapter in native runs, whether
they were written by the MCP settings page, `agent -p PROFILE mcp add`, or an
import. Each entry runs under the logical name `custom:<name>`, so a custom
server and a catalog server with the same name both stay connected. Entries
recorded as legacy catalog servers in the profile state are skipped. A
profile with custom servers and no generation gets one on first use.

Custom HTTP servers follow the catalog transport rules: public HTTPS only, no
query string, no OAuth, and placeholders resolved only from the profile's
secrets. A custom server with a `command` runs as a local stdio process
instead, with the same `${VAR}` resolution. Stdio servers run as the Runtime
user with no sandbox and no command allowlist, because each workspace is the
user's own server. Their keys are literal values in the server's `env`, which
the API masks as `***`. An entry that has both `command` and `url`, or
`command` and a Control connection, is rejected, as is OAuth.

A stdio server stops after 600 seconds without a tool call unless its entry
sets `idle_timeout_seconds` (or `lifecycle.idle_timeout_seconds`); the next
tool call starts it again, and it stays registered while stopped. Remote HTTP
and catalog servers have no idle stop. Each stdio server writes its stderr to
its own profile's `logs/mcp-stderr.log` (Hermes opens one process-wide log, so
the Runtime routes it per profile while a task runs, without editing Hermes).
Deleting an agent stops its MCP sessions, including stdio processes, before
the profile directory is removed; a failure there is logged without detail and
does not block the deletion.

Custom servers fail open per server, unlike catalog servers:

- An invalid entry is skipped as `rejected`.
- An unreachable entry is skipped as `unavailable`.
- Entries with `enabled: false` are ignored.
- An unavailable server is retried after 30 seconds. The wait doubles after
  each failure, up to 10 minutes, and resets when the server config changes.
  One dead server therefore never blocks or slows every turn.

`GET` and `PUT /agents-mcp/{agent_id}` return `chat_status`, which maps each
profile-defined server to one of these states:

- `connected`, `unavailable` or `rejected`: reported by the last native turn
  for the same saved config.
- `pending`: no native turn has loaded this config yet.
- `disabled`: the entry has `enabled: false`.

Mixed `command`+`url`, `http://`, query-string and OAuth entries report `rejected` right away,
without waiting for a turn. Private addresses need DNS, so they report
`rejected` only after a native turn has loaded them. Statuses live in memory,
so a Runtime restart resets them to `pending`. The MCP settings page shows the
state next to each saved server. Logs name
only the server, never its URL, headers or secrets. The model sees the tools
as `mcp__xnb_<id>__<tool>` with the server's descriptions. OAuth and
private-network servers still work only in the one-shot CLI path described below.

`mcp_profile_isolation_supported()` returns true when the engine exposes the
scoped registry API and task hooks this adapter needs, and fails closed
otherwise. `test_managed_mcp.py` is the conformance suite: concurrent A/B
profiles with distinct credentials, dispatch, refresh, unload of A while B
keeps working, initial registration through the real native `start()` path,
and agent tool injection.

Limits: catalog MCP tools are available only in native conversation runs.
The one-shot `chat` endpoint (team runs and delegated executions) and Kanban
workers run the Hermes CLI as a separate per-profile subprocess. That
subprocess runs native discovery over `mcp_servers` only, including stdio and OAuth entries.
Catalog tools are absent there, not leaked. Catalog API keys are
stored by Control and never copied to Runtime profile files; shareable exports
and imports therefore contain no catalog key.

Nineteen OAuth recipes are installable alongside seven non-OAuth remote recipes.
Four desktop integrations are unsupported, and the unpinned Brave Search stdio
recipe remains `needs_review`.

Catalog OAuth credentials live in Control. The UI creates a pending Control
connection, installs its proxy URL in Runtime with an empty `fields` object,
then calls Control's `POST /xnobrain/api/control/v1/mcp/connections/{id}/oauth/start`.
The browser posts `{state, code}` (or `{state, error}` when authorization is
denied) to Control's `/xnobrain/api/control/v1/mcp/oauth/callback`, then calls
Runtime `POST /agents-plugins/{agent_id}/mcp/reconnect` so servers parked on
`needs_sign_in` reconnect with the fresh grant Control attaches. Reconnect
clears the retry backoff for those servers and reruns the profile MCP sync
immediately, without waiting for the next chat turn. Runtime has
no OAuth start or callback routes and stores no catalog OAuth tokens or DCR
client data. Runtime startup removes legacy `xnobrain-*` token files and catalog
MCP entries that contain plaintext headers or `auth: oauth`; users reconnect
those integrations through the UI. Control connection status is authoritative
for sign-in state, while Runtime reports whether the proxy session is available.

When a portable plugin with MCP is enabled, Runtime writes disabled
`xnobrain_plugin_shadow` entries in profile `config.yaml`; Hermes skips these
entries in one-shot `agent -p` discovery. Runtime removes them when the plugin
is disabled, updated, or removed. Profile-owned MCP servers continue to use
Hermes' native one-shot path.

## Executable plugin lifecycle

Public HTTPS Git installation is staged outside Hermes discovery, records the
resolved commit, scans every supported text file without honoring a supplied
`.skillignore`, and publishes the plugin disabled. Optional `ref` selects a branch
or tag. Unsupported binary/bytecode files, incomplete scans and dangerous
findings block activation. The scanner is a review aid, not a Python sandbox.

Before the digest and scan, the clone drops top-level content the plugin loader
never reads: `.github/`, `.gitlab/`, `.circleci/`, `tests/`, `test/`, `docs/`,
`bench/`, `benchmarks/`, `benchmarking/`, README/CHANGELOG/CONTRIBUTING/
SECURITY/CODE_OF_CONDUCT/ROADMAP/AGENTS/CLAUDE markdown, `conftest.py`,
`pytest.ini`, `tox.ini`, `.pre-commit-config.yaml`, `.editorconfig`, and
`.gitattributes`. Documentation examples and CI scripts therefore no longer
block a plugin, and those files are absent from the installed artifact. A
symlink with one of these names is kept so the digest still rejects it.

Approval binds the exact artifact digest and record revision. Enable and native
or CLI execution check that approval and current bytes still match. Mutations
use the same lifecycle inode as agent execution. Native plugin managers reload
changed generations before a subsequent turn, not halfway through an active
turn. Updates revoke approval and leave the new artifact disabled. Required
XNOBrain plugins are protected and trusted only when their bytes match the
packaged reference, never merely because their name matches.

### Native plugin kinds and error codes

Native `kind: standalone` plugins are the only kind this API installs. Their
tools, hooks, commands and skills use the reviewed scan, approval and enable
lifecycle above. Portable and Claude-format packages continue through their own
format checks.

The manifest loader also recognizes `backend`, `exclusive`, `model-provider` and `platform`, but
these are not installable through this API. They require code and wiring that
ships with the Runtime image:

- `platform`: gateway messaging adapter; requires packaged channel support.
- `model-provider`: model provider; requires managed provider support.
- `backend`: pluggable backend for a core tool; requires packaged engine support.
- `exclusive`: single-provider category such as memory; requires packaged engine
  support.

An install or scan operation that encounters one reports the stable
`plugin_kind_unsupported` code, a fixed explanation, and ends `BLOCKED`. A
manifest the pinned loader cannot parse, or an unrecognized kind, remains
`plugin_manifest_invalid`. These checks classify manifests; they do not sandbox
plugin Python, so the scanner remains a review aid.

Shell hooks are created inert. Approval and enable are separate actions. The
engine's valid shell events and maximum timeout (300 seconds) are authoritative.
Lists expose a command label/digest, not full shell commands. Native consent
records and Runtime approval must agree; auto-accept settings cannot bypass the
preflight check. Plugin/gateway hook metadata is read-only.

Hooks written directly into `config.yaml` before managed hooks existed are
adopted into `.xnobrain/extensions/state.json` on the next load or validation.
The adopted record is disabled and unapproved, keeps its event, command,
matcher and timeout, and the entry is removed from `config.yaml`, so it never
runs until the user approves and enables it in the Hooks screen. Migration runs
under the same profile lifecycle lock as other extension state writes.

Hooks that share one command are independent. Disabling, revoking or deleting
one hook removes only its own event/matcher/command config entry; native consent
for the `(event, command)` pair is revoked only when no other enabled and
approved hook still uses it.

Tools report enabled, available, ready and missing requirements separately.
Persistent disabled toolsets remain an upper bound on per-run selections.

## Storage and operations

Profile state is below `.xnobrain/extensions/state.json`; actual Hermes plugin
artifacts remain under `plugins/<id>`. Mutable records are atomic and private.
Import removes executable approval state and native shell-hook consent; imported
plugin files remain inert until the receiving Runtime reviews them.

The durable queue is `DATA_DIR/extension-operations/<agent-hash>.json`, with one
pending/running operation per agent. Operations return 202 and expose sanitized
status through owner-checked list/detail/cancel endpoints. Lifecycle workers do
blocking work off the event loop; Git staging and scan precede the short locked
publication step. Terminal history expires after seven days. No credentials or
raw subprocess output are included in operation records or responses.

Each accepted operation captures a random profile generation. The worker checks
it again under the profile lifecycle lock before mutation, so deleting and
recreating a profile under the same agent ID cannot receive stale queued work.
Publication writes a private, atomic `publication.json` intent before moving
plugin trees. Startup recovery validates the phase and artifact digests, restores
or retains only a matching tree, disables it, and revokes scan and approval
state; invalid or ambiguous intents block extension use for operator recovery.

States are `PENDING`, `PROCESSING`, `COMPLETED`, `FAILED`, `CANCELLED`, `BLOCKED`.
Restart reconciles interrupted work rather than replaying an unknown mutation.
UI polling survives navigation, recovers through the list API after reload, and
never automatically retries a mutation.

## HTTP surface

All paths below are relative to `/xnobrain/api/runtime/v1`; the existing JSON
envelope and stable error mapping apply. FastAPI generates OpenAPI at runtime.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/plugins/catalog` | Static recipe catalog plus engine capabilities |
| GET | `/agents-plugins/{agent_id}` | Plugin and managed MCP inventory |
| POST | `/agents-plugins/{agent_id}/install-catalog` | Recipe configuration; compatibility-gated |
| POST | `/agents-plugins/{agent_id}/{plugin_id}/approval` | Digest/revision-bound decision |
| POST | `/agents-plugins/{agent_id}/{plugin_id}/enable` | Enable reviewed artifact |
| POST | `/agents-plugins/{agent_id}/{plugin_id}/disable` | Disable on subsequent turns |
| PATCH | `/agents-plugins/{agent_id}/{plugin_id}/visibility` | Hidden preference |
| POST | `/agents-plugins/{agent_id}/{plugin_id}/probe` | Start read-only MCP probe |
| POST | `/agents-plugins/{agent_id}/{plugin_id}/disconnect` | Remove owned MCP configuration |
| POST | `/agents-extension-operations/{agent_id}` | Start install/scan/update/remove |
| GET | `/agents-extension-operations/{agent_id}` | Recover operation history |
| GET | `/agents-extension-operations/{agent_id}/{operation_id}` | Observe operation |
| POST | `/agents-extension-operations/{agent_id}/{operation_id}/cancel` | Request cancellation |
| GET | `/agents-tools/{agent_id}` | Effective toolsets and readiness |
| PATCH | `/agents-tools/{agent_id}/{toolset}` | Set persistent enabled state |
| GET | `/agents-hooks/{agent_id}` | Shell and read-only plugin/gateway metadata |
| POST | `/agents-hooks/{agent_id}` | Create inert shell hook |
| POST | `/agents-hooks/{agent_id}/{hook_id}/approval` | Approve exact hook digest |
| POST | `/agents-hooks/{agent_id}/{hook_id}/enable` | Enable approved hook |
| POST | `/agents-hooks/{agent_id}/{hook_id}/disable` | Disable hook |
| DELETE | `/agents-hooks/{agent_id}/{hook_id}` | Remove hook and revoke approval |

Operation creation accepts `kind`, `plugin_id`, optional public `source` and
`ref`, and a client-generated `idempotency_key`. Reusing a key with a different
payload is a conflict. Approval accepts `artifact_digest`, `approved`, and
`expected_revision`; hook approval uses `command_digest`. A toolset patch accepts
`enabled` and `expected_revision`. Secret recipe fields are write-only and never
used as background-operation inputs.

Operation responses include `plugin_id`. A failed or blocked operation keeps the
fixed public reason of an expected error in `error.message` (for example
`Plugin exceeds scan limits`); unexpected errors stay `Operation failed`.
`progress` is cleared when an operation fails. A `remove` operation for a
catalog integration (an id in the profile's catalog records with no plugin
artifact) stops its managed MCP connection and deletes the record; `remove` for
an id with neither an artifact nor a record fails with `plugin_not_found`
instead of reporting success.

## Source verification

Run focused source tests in the existing Docker development images, including
`test_extension_engine`, `test_extensions_service`, operation-store tests,
`test_extension_imports`, `test_plugin_catalog`, and profile-template tests.
Engine tests must use the exact pinned artifact; skipped engine tests are not
proof of compatibility. UI requires route, feature flag, catalog, Tools and
operation-tracker tests plus no-emit TypeScript checking. Control requires
feature-gate, personal-context, environment propagation and navigation tests.
No production bundle/image or deployment is needed for source verification.

### Implementation evidence (2026-11-03)

Source work is on local branch `codex/plugins-hooks-catalog` in the workspace,
Runtime, UI and Control repositories. The final focused Runtime command ran
55 tests successfully across engine, API, service, operation recovery, import,
catalog, feature-flag and profile-template modules, against the existing
`xnobrain-runtime:dev` image. Ruff check and format-check passed for all 17 new
extension Python files. The root wiring, release-script and development-stack
suites passed 45 tests. These are source checks, not deployment evidence.
UI verification passed 111 tests across seven focused files and
`tsc --noEmit`, including dangerous-scan activation controls and sanitized
scan details in the explicit approval dialog.
Control source checks passed for `internal/config`,
`internal/domain/uicomposition`, `internal/remote`,
`internal/services/workspace`, `internal/v1/handlers/gateway`,
`internal/v1/routes` and `internal/nodegateway` in its existing development image.

Broader Runtime verification also exposed four existing skill-catalog/bootstrap
failures. Running those same four cases against an archive of the unchanged
Runtime HEAD with the same image reproduced them; they are outside this change.
The broader Control handlers package has six failing groups involving auth,
route order and workspace proxy responses. The exact six groups also fail on
an unchanged Control HEAD archive in the same development image.
No application-stack browser smoke test or image build is claimed here.

### Follow-up verification (2026-11-03)

Catalog MCP activation was completed after the summary above. Commands run in
the existing `xnobrain-runtime-1` dev container (with
`RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT` unset so temporary test profiles are
allowed):

- 63 Runtime tests across managed MCP, isolation, engine, imports, operations,
  API, service, catalog, feature-flag and profile-template modules: all pass.
- Ruff check and format-check pass on every changed Runtime Python file.
- UI: 108 tests in seven focused files and `tsc --noEmit` pass, after
  restricting the app to en, vi and zh (es/fr/de/ja locale files removed).
- Control: `go test` passes for the changed config, domain, nodegateway,
  remote, services, gateway and routes packages.
- Root: 45 wiring/release/dev-stack tests pass.

Also fixed: worker validation errors (for example `plugin_manifest_invalid`)
were reported as a generic 503 because the worker exit code hid the code. The
worker now reports a code, and the parent maps only allowlisted codes.

Not done: no live MCP provider was contacted, no browser smoke test was run,
and no image was built or deployed.
# Installing plugins from Git and ZIP uploads

Git installs accept an optional `path` subdirectory (up to 512 characters) to
select a plugin folder inside a repository. A 40-character commit SHA in `ref`
pins the fetched revision; installed inventory reports `commit`, `pinned`, and
the selected `path`.

`POST /agents-plugins/{agent_id}/upload` accepts multipart fields `plugin_id`,
`file`, and optional `idempotency_key`. The filename must end in `.zip` or
`.plugin`; archives are limited to 3 MiB compressed. ZIP extraction allows at
most 2,000 entries and 32 MiB uncompressed, rejects symlinks, traversal,
absolute/drive paths, duplicate names, and compression ratios above 100 for
files over 1 MiB, and ignores `__MACOSX/` and `.DS_Store`. A common top-level
folder is stripped. Uploaded plugins follow the same scan, approval, and enable
flow as Git installs. Their reported commit is `upload:` plus the extracted
tree digest, and `pinned` is false. Updating an uploaded plugin requires a new
upload.

## Personal workspace plugin marketplaces

Personal workspace owners can register up to 20 public Git marketplaces. Runtime
clones each repository synchronously, reads a bounded Claude or Codex marketplace
manifest, and stores its parsed entries below `DATA_DIR/extensions/marketplaces.json`.
Each marketplace is capped at 200 manifest entries. Unsupported package managers,
invalid names, and invalid Git sources appear in `skipped` with a reason code.
Relative entries are installed from the marketplace repository at the exact commit
that was read, with their validated subdirectory passed as `path`.

- `GET /plugins/marketplaces` returns `{ "marketplaces": [...] }`.
- `POST /plugins/marketplaces` accepts `{ "source": "owner/repo[#ref]" }` and returns
  a marketplace with HTTP 201.
- `POST /plugins/marketplaces/{marketplace_id}/refresh` reclones and reparses it.
- `DELETE /plugins/marketplaces/{marketplace_id}` removes only the marketplace record.

Marketplace discovery does not install code. The UI submits a selected entry to the
existing extension install operation with its source, path, and pinned ref. It therefore
uses the same scan, approval, and enable lifecycle as any other Git install.

## MCP credentials for plugins and custom servers

Portable plugin MCP servers that declare OAuth or header placeholders are
never connected directly with credentials. Inventory rows include the declared
HTTPS URL, auth kind, the declared key header (`auth_header`, the first header
whose value is empty or a `${...}` placeholder) and installed plugin `commit`
revision (uploaded archives use `upload:<digest>`). The UI obtains host
approval, creates a Control connection, and stores its ID through `PUT
/agents-plugins/{agent_id}/plugins/{plugin_id}/mcp/{server_name}/connection`.
`connection_id` must be a UUID and is stored in canonical lowercase form.
`DELETE` on that path clears the mapping. Runtime uses a mapping only while its
revision matches the installed commit; otherwise the server remains
`needs_sign_in`. Plugin updates change the revision and removal clears mappings.

Custom HTTP MCP entries may store `xnobrain_connection_id`. The settings UI
moves literal header values to Control before saving that ID. Runtime keeps the
displayed upstream URL and ID, rebuilds the proxy URL for chat, and drops
headers during sync. Stdio entries remain unchanged.

## Connect card from Big Brother chat

Big Brother signed runs expose the read-only `xnobrain_integration_connect` tool
(toolset `xnobrain_integration_connect`). It accepts `kind`
(`catalog_mcp`, `plugin_mcp`, or `custom_mcp`), an optional target `agent_id`
(default `big-brother`), the matching `catalog_id` or `plugin_id` +
`server_name`, an optional public HTTPS `url` for `custom_mcp`, and an optional
`reason`. It has no field for headers, keys, tokens, environment, or other
secrets, and it never installs or changes settings. It validates the target
agent, the catalog entry or installed plugin MCP server, and the custom URL,
then returns the connect request plus an instruction to finish credentials in
the card.

The tool result becomes a live event after a successful `tool.completed`:

```json
{
  "event": "integration.connect_requested",
  "run_id": "run_...",
  "timestamp": 1793950000.0,
  "tool_call_id": "call_...",
  "request": {
    "id": "6f1c...",
    "kind": "catalog_mcp",
    "agent_id": "big-brother",
    "display_name": "Notion",
    "auth_kind": "oauth",
    "catalog_id": "notion",
    "recipe_revision": 2,
    "reason": "You asked to connect Notion."
  }
}
```

The same JSON is in the stored tool message so history replay rebuilds the
card without new persistence. Malformed or failed results emit nothing.

After a completed Big Brother reply, Runtime makes one small side model call
(bounded to 3 seconds) and may emit up to three suggested next user messages
just before `run.completed`:

```json
{
  "event": "run.suggestions",
  "run_id": "run_...",
  "timestamp": 1793950001.0,
  "suggestions": ["Connect Google Drive too", "Show my installed plugins"]
}
```

Suggestions are one line, at most 80 characters, deduplicated, and not
persisted. Timeout, error, or empty output emits nothing. The event is sent
just before `run.completed` because the durable-run relay stops appending
events after a terminal status; that ordering is what lets suggestions reach
the UI.
