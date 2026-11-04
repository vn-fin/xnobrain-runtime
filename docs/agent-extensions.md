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
- Header placeholders resolve only from the profile's own secret scope.
  Transport is DNS-pinned public HTTPS; OAuth and stdio are rejected.
- Before each native conversation run, `refresh_profile_mcp` reconciles the
  profile's sessions, then `managed_mcp.install_tools` adds the connected
  tools to that agent (Hermes only auto-selects toolsets from `mcp_servers`).

`mcp_profile_isolation_supported()` returns true when the engine exposes the
scoped registry API and task hooks this adapter needs, and fails closed
otherwise. `test_managed_mcp.py` is the conformance suite: concurrent A/B
profiles with distinct credentials, dispatch, refresh, unload of A while B
keeps working, initial registration through the real native `start()` path,
and agent tool injection.

Limits: catalog MCP tools are available only in native conversation runs.
The one-shot `chat` endpoint and Kanban workers run the Hermes CLI as a
subprocess, which reads only `mcp_servers`; there the tools are absent, not
leaked. API keys are stored in the profile `config.yaml` header, the same
storage model as native `mcp_servers`; shareable exports drop them and imports
remove the managed key.

Six remote recipes are installable; 17 OAuth recipes and four desktop
integrations are unsupported, and four unreviewed/unpinned stdio recipes remain
`needs_review`. No OAuth broker, desktop bridge, package auto-install, or remote
catalog synchronization is introduced.

## Executable plugin lifecycle

Public HTTPS Git installation is staged outside Hermes discovery, records the
resolved commit, scans every supported text file without honoring a supplied
`.skillignore`, and publishes the plugin disabled. Optional `ref` selects a branch
or tag. Unsupported binary/bytecode files, incomplete scans and dangerous
findings block activation. The scanner is a review aid, not a Python sandbox.

Approval binds the exact artifact digest and record revision. Enable and native
or CLI execution check that approval and current bytes still match. Mutations
use the same lifecycle inode as agent execution. Native plugin managers reload
changed generations before a subsequent turn, not halfway through an active
turn. Updates revoke approval and leave the new artifact disabled. Required
XNOBrain plugins are protected and trusted only when their bytes match the
packaged reference, never merely because their name matches.

Shell hooks are created inert. Approval and enable are separate actions. The
engine's valid shell events and maximum timeout (300 seconds) are authoritative.
Lists expose a command label/digest, not full shell commands. Native consent
records and Runtime approval must agree; auto-accept settings cannot bypass the
preflight check. Plugin/gateway hook metadata is read-only.

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
