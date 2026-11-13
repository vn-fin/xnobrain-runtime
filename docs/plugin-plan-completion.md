# Plugins, hooks, and toolsets implementation audit

Audit date: 2026-11-03. This audit treats the acceptance requirements in
`plans/007_plugins_and_hooks/README.md` and `validation.md` as requirements;
the historical-plan notice does not count as evidence that a requirement is
complete. Current implementation uses the profile-scoped Agent Extensions
contract in [agent-extensions.md](agent-extensions.md).

## Requirement status

| Requirement | Status | Evidence / remaining work |
| --- | --- | --- |
| Top-level Plugins destination with catalog, installed items, hooks, URL install and plugin lifecycle controls | Partial / UI verification pending | Runtime contract exposes catalog, inventory, async install/scan/update/remove operations, approval, enable/disable, and hidden preference: `xnobrain/routes/extensions.py`; contract in `agent-extensions.md`. URL source install is a plugin operation, while catalog integrations are configured separately. UI implementation and route/UI tests are owned by the UI workstream. |
| Scan, explicit approval, enable/disable, update, remove | Implemented in Runtime; lifecycle API E2E evidence pending | `ExtensionsService` has digest/revision-bound approval and enable (`xnobrain/services/extensions.py`); `ExtensionOperationWorker` handles install/scan/update/remove. Runtime engine validates current bytes at execution (`xnobrain/integrations/extension_engine.py`). The parent is adding the native HTTP lifecycle and dangerous-scan tests in `test_extensions_api.py`; until that lands and runs, do not mark full HTTP lifecycle acceptance verified. |
| Hide plugin | Implemented in Runtime; UI evidence pending | `PATCH /agents-plugins/{agent_id}/{plugin_id}/visibility`; service revision check and persistence; unit test `test_visibility_uses_revision_and_persists` in `xnobrain/tests/test_extensions_service.py`. |
| Per-agent toolset controls | Implemented in Runtime; full HTTP/panel evidence pending | `GET/PATCH /agents-tools/{agent_id}[/{toolset}]`; persistent toolset adapter and engine round-trip test `test_toolset_toggle_updates_native_and_cli_positive_selection` in `xnobrain/tests/test_extension_engine.py`. |
| Hook visibility, create with approval, delete | Implemented in Runtime; full HTTP evidence pending | `GET/POST/DELETE /agents-hooks/{agent_id}...`; shell hooks are inert until digest-bound approval then enable; plugin/gateway metadata is read-only. Engine coverage includes native consent round-trip and auto-accept bypass rejection. API E2E coverage pending parent test. |
| Catalog covers 31 Openbot reference entries | Implemented as a coverage count | `xnobrain/assets/plugin_catalog.json`; `test_checked_in_catalog_has_full_coverage_and_safe_installable_subset` asserts 31 entries and fail-closed supported subset. Provenance states checklist-only use in `docs/plugin-catalog.md`. |
| UI languages | Implemented for en, vi, zh | The product decision (2026-11-03) keeps the app at English, Vietnamese and Chinese. `pluginsView`, `agentTools` and `hooks` keys exist in all three and `src/i18n.test.ts` checks parity. Catalog entries also carry es/fr/de/ja summaries from the earlier plan; the UI ignores them and falls back to English for unsupported languages. |
| MCP catalog installation | Implemented in Runtime; live provider check pending | Catalog servers use the profile-scoped adapter in `xnobrain/integrations/managed_mcp.py` instead of native `mcp_servers` discovery, which stays process-global. `test_managed_mcp.py` proves scoped registration, dispatch with per-profile credentials, refresh, unload and agent tool injection. Available in native conversation runs only; see `agent-extensions.md`. |

| Phase 5 Runtime OAuth and one-shot plugin MCP safety | Implemented; offline focused verification pending | Nineteen OAuth catalog recipes use profile-scoped Hermes OAuth tokens and the shared guarded transport. OAuth flows bind to verified identity. Portable plugin MCP declarations are shadowed from Hermes one-shot discovery. See `agent-extensions.md`. |


| Agent Plugins v1 and Claude plugin formats | Implemented; browser verified 2026-11-04 | `plugin_formats.py` detects native/portable/Claude packages. Portable packages may not carry code outside `skills/` or stdio MCP; Claude packages keep skills and HTTPS MCP, and dropped parts (hooks, agents, commands, secret-header servers) are listed. Plugin MCP hosts must resolve to public addresses at scan time. In chat, plugin MCP runs through the per-profile adapter with no secret interpolation and a 20 s connect cap; verified in Chrome by a chat turn calling DeepWiki through an uploaded plugin. |
| Git subdirectory and commit SHA pin | Implemented; browser verified 2026-11-04 | `clone_plugin(path=..., ref=<sha>)` fetches one commit and keeps one directory. Update keeps a pinned SHA (checked in Chrome via a marketplace install of `frontend-design`). |
| ZIP upload install | Implemented; browser verified 2026-11-04 | `POST /agents-plugins/{agent_id}/upload` (3 MiB archive, 32 MiB extracted, 2000 entries, no symlinks or traversal, zip-bomb ratio check). The archive goes through the same scan and approval. In Chrome, a valid zip installed and a `../` zip was rejected with no file written. |
| Personal workspace Git plugin marketplaces | Implemented in Runtime, Control policy and UI; browser verified 2026-11-04 | Runtime clones and parses Claude/Codex marketplace manifests into bounded atomic storage; relative entries are pinned to the fetched commit and UI installs use the existing scan/approval operation. Control rejects the marketplace route family in organization workspaces. Marketplace clones skip per-plugin artifact limits (only the manifest is read; repos above 32 MiB still fail). Verified in Chrome with `anthropics/claude-plugins-official`. See `agent-extensions.md`. |

## Verification evidence and limits

The checked-in implementation note in `docs/agent-extensions.md` records 55
focused Runtime tests, 111 focused UI tests plus `tsc --noEmit`, and 45 root
wiring/release/development tests. It also records reproduced pre-existing
failures in broader Runtime and Control suites. Those historical command
results are not a substitute for running the new native HTTP lifecycle test
after it is added. No build, deployment, or browser smoke is claimed by this
audit.

Runtime tests inspected for this audit include `test_extension_engine.py`,
`test_extensions_service.py`, and `test_plugin_catalog.py`. The independent
HTTP API suite currently has identity, validation, redaction, asynchronous
operation, and feature-flag tests; the full lifecycle HTTP sequence remains
the evidence gap being addressed by the parent workstream.

## Actionable remaining items

1. Apply plugin MCP safety (public host + DNS pinning) to one-shot `agent -p` runs (team, delegated, Kanban); today only native chat turns re-check on connect.
2. Browser smoke test of Plugins, Tools and Hooks in en, vi and zh.
3. Connect one real catalog MCP provider (for example Context7) from the UI and
   confirm its tools appear only in that agent's conversation runs.
