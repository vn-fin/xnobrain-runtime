# 007 — Implementation

Ordered, file-by-file steps for [README.md](README.md). Read
[architecture.md](architecture.md) and [findings.md](findings.md) first;
verify [approaches.md](approaches.md) A1/B1/C1 is still the decision.
All paths absolute-from-repo-root. Do the smallest coherent change per step and
run focused tests before moving on.

## Phase 0 — Pin + compatibility test {#phase-0}

1. **Pin Hermes.** Replace the moving `HERMES_BRANCH=main` in
   `Dockerfile.backend:17` with the immutable tag/commit already chosen for the
   Kanban program (keep one pin for the whole repo). Record the pin and the
   plugin/hook/tool symbols it must provide in `docs/development.md` and
   `docs/architecture.md`.
2. **Compatibility test** — new `xnobrain/tests/test_plugins_compat.py`
   (mirror `xnobrain/tests/test_kanban.py`'s guarded-import + temp-`HERMES_HOME`
   style). Guard with `try: import hermes_cli.plugins_cmd … except Exception:
   HERMES_AVAILABLE=False`. Assert the symbols in
   [findings.md §5](findings.md#compatibility-apis-to-pin-and-test) are importable
   and callable:
   - `plugins_cmd.dashboard_install_plugin`, `dashboard_set_agent_plugin_enabled`,
     `dashboard_update_user_plugin`, `dashboard_remove_user_plugin`,
     `_get_enabled_set`, `_get_disabled_set`, `_discover_all_plugins`.
   - `hermes_cli.plugins.VALID_HOOKS` is a non-empty set containing
     `pre_llm_call`, `pre_tool_call`, `transform_llm_output`.
   - `agent.shell_hooks`: `iter_configured_hooks`, `_record_approval`, `revoke`,
     `allowlist_entry_for`, `script_is_executable`, `load_allowlist`,
     `ShellHookSpec`.
   - `import toolsets`: `TOOLSETS` (dict), `resolve_toolset`, `_HERMES_CORE_TOOLS`.
   - `from tools.registry import registry`: `get_definitions`,
     `get_registered_toolset_names`, `check_toolset_requirements`.
   - `hermes_cli.tools_config`: `_get_platform_tools`, `_save_platform_tools`,
     `_get_effective_configurable_toolsets`.
   - `tools.skills_guard`: `scan_skill`, `scan_skill_cached`,
     `should_allow_install`, `format_scan_report`, `ScanResult`.
   - `tools.osv_check.check_package_for_malware`.
   - **Lifecycle in a temp `HERMES_HOME`** using a real local plugin directory
     committed under `xnobrain/tests/fixtures/plugins/sample_plugin/` (a minimal
     manifest + a harmless tool; NO network): install-from-local → scan (verdict
     `safe`) → assert enable-without-approval is blocked by the service → approve
     → enable → assert in `_get_enabled_set()` → disable → remove. Also a second
     fixture `dangerous_plugin/` whose file trips a `skills_guard` THREAT_PATTERN
     to prove a `dangerous` verdict blocks enable.
   - A toolset round-trip: write `agent.disabled_toolsets` for a temp profile via
     the integration, re-read effective toolsets, assert the change is visible.
   - A shell-hook round-trip: create with `approve=True`, assert
     `allowlist_entry_for` returns an entry, list, delete, assert revoked.
3. **Readiness.** Add a startup readiness probe (beside the Kanban diagnostics)
   that imports the plugin symbols and, on `ImportError`/signature mismatch,
   surfaces one concise remediation message and marks the plugins subsystem
   degraded — without blocking unrelated local features. Expose it in the
   existing health/diagnostics model.

Gate: this test and `make check` pass before Phase 1.

## Phase 1 — Models + integration adapter {#phase-1}

4. **Models** — `xnobrain/models/api.py`: add `PluginInstall`,
   `PluginApproval`, `PluginVisibility`, `HookCreate`, `HookDelete` exactly as in
   [architecture.md](architecture.md#new-xnobrain-api-contract). Export them in
   `xnobrain/models/__init__.py` and import them in `xnobrain/routes/setup.py`'s
   models import block. Reuse `EnabledPatch` for tool toggles.
5. **Integration adapter** — new `xnobrain/integrations/plugins.py`:
   - `class PluginAPIError(ValueError)` with `__init__(self, message, *,
     code="plugin_error", status=400)` (mirror `AgentAPIError`).
   - `class PluginManager` with the surface in
     [architecture.md](architecture.md#integration-adapter-xnobrainintegrationspluginspy).
     Construct from env (`HERMES_ROOT_PROFILE`, `HERMES_PROFILES_ROOT`) like
     `AgentManager`. Hold no policy.
   - Import Hermes lazily inside methods (as `integrations/kanban.py` does):
     `from hermes_cli import plugins_cmd`, `from tools import skills_guard,
     osv_check`, `from agent import shell_hooks`, `import toolsets`,
     `from tools.registry import registry`, `from hermes_cli import tools_config`,
     `from hermes_cli.plugins import VALID_HOOKS`.
   - `plugin_dir(name)` sanitizes `name` (reuse the `_validate_plugin_name`
     rules: reject empty/`..`/`\`/separators) and resolves strictly under
     `~/.hermes/plugins/`.
   - `content_hash(name)` = sha256 over the plugin dir's scannable files
     (reuse `skills_guard.full_content_hash` if available, else a stable local
     walk) — the enable-gate anchor.
   - `install(identifier, *, force)` → `plugins_cmd.dashboard_install_plugin(
     identifier, force=force, enable=False)`; translate a non-`ok` result to
     `PluginAPIError`. **Never pass `enable=True` from anywhere.**
   - `scan(name)` → `skills_guard.scan_skill_cached(self.plugin_dir(name),
     source="community")`, then `should_allow_install(result)`; parse declared
     npm/PyPI deps from the manifest and run `osv_check.check_package_for_malware`
     per dep; fold into a verdict `safe|caution|dangerous` and a **sanitized**
     findings summary (category + severity + file + line; never the matched
     source snippet or absolute paths). Return `{verdict, content_sha256,
     findings: [...], report_text}` (report_text via `format_scan_report`,
     path-stripped).
   - `enable/disable(name)` → `plugins_cmd.dashboard_set_agent_plugin_enabled(
     name, enabled=...)`. `update(name)` → `dashboard_update_user_plugin`.
     `remove(name)` → `dashboard_remove_user_plugin`.
   - `list_plugins()`/`browse_hub()` from `_discover_all_plugins` +
     `_get_enabled_set`/`_get_disabled_set`; strip `_`-prefixed and path fields.
   - `list_hooks()` merges `shell_hooks.iter_configured_hooks(cfg)` (with
     `allowlist_entry_for`/`script_is_executable`), a plugin-hook view from
     `VALID_HOOKS` cross-referenced with enabled plugins' declared hooks, and the
     gateway registry's `loaded_hooks`. **Command strings become a label**
     (basename + short hash).
   - `create_hook(...)` validates `event in VALID_HOOKS`, appends to config
     `hooks[event]`, writes config, and calls `shell_hooks._record_approval(
     event, command)` when `approve`. `delete_hook(...)` removes the entry and
     calls `shell_hooks.revoke(command)`.
   - `list_agent_toolsets(agent_id)` composes `toolsets.TOOLSETS` +
     `registry.get_registered_toolset_names()` + the profile's effective config
     (`tools_config._get_platform_tools`) + readiness
     (`registry.check_toolset_requirements`). `set_agent_toolset(agent_id,
     toolset, enabled)` loads the profile `config.yaml`, edits
     `agent.disabled_toolsets` (denylist, mirroring skills' `_write_disabled_skills`),
     runs `normalize_nine_router_config`, and writes atomically. Reject any
     toolset not in `SAFE_TOOLSETS` ∪ enabled-plugin toolsets.
6. **Wire the adapter** into `xnobrain/app.py`: construct `PluginManager` and
   pass it into the service (see Phase 2). Instantiate in `xnobrain/server.py`
   next to `AgentManager()`/`GlobalConfigManager()`/`NineRouterManager()`.

## Phase 2 — Service with the scan+approval rule {#phase-2}

7. **Snapshot kind** — `xnobrain/repositories/files.py`: extend the `snapshot()`
   `kind` whitelist (currently `{memory, skills, config}` at ~line 75) to include
   `"plugins"`. Approval records live at
   `DATA_DIR/plugins/approvals/<name>.json`; snapshots at
   `DATA_DIR/plugins/snapshots/`. (If `DATA_DIR` layout differs, follow the
   repository's existing root helpers — do not hardcode.)
8. **Service** — new `xnobrain/services/plugins.py`, `class PluginService`
   constructed `(repository, plugins: PluginManager, agents: AgentManager)`.
   Implement the methods and invariant from
   [architecture.md](architecture.md#service-xnobrainservicespluginspy):
   - `_record_path(name)`, `_load_record(name)`, `_write_record(name, record)`
     (snapshot-before-write via `repository.snapshot(agent_id="_global",
     "plugins", name, payload)` then `repository.atomic_json`).
   - `_scan_and_record(name)`: run `self.plugins.scan(name)`, build the record
     `{name, content_sha256, verdict, findings_summary, scanned_at,
     approved:False, approved_at:None, approval_choice:None}`, persist, return the
     sanitized scan.
   - `install(body)`: `self.plugins.install(...)` then `_scan_and_record`.
   - `scan(name)`: `_scan_and_record` (re-scan; clears prior approval).
   - `approve(name, body)`: load record; **raise `ServiceError("scan required",
     status=404, code="plugin_scan_required")` if absent**; raise
     `plugin_unsafe`(409) if `verdict=="dangerous"`; raise `plugin_scan_stale`(409)
     if `body["content_sha256"] != record["content_sha256"]`; else set
     `approved = choice=="approve"`, persist.
   - `enable(name)` — **the mandatory gate, explicit in code:**
     ```python
     def enable(self, name: str) -> dict:
         record = self._load_record(name)
         if record is None:
             raise ServiceError("plugin must be scanned first",
                                status=409, code="plugin_scan_required")
         if record["verdict"] == "dangerous":
             raise ServiceError("plugin failed the malware scan",
                                status=409, code="plugin_unsafe")
         if record["content_sha256"] != self.plugins.content_hash(name):
             raise ServiceError("plugin changed since scan; rescan required",
                                status=409, code="plugin_scan_stale")
         if not record.get("approved"):
             raise ServiceError("plugin enable requires explicit approval",
                                status=409, code="plugin_approval_required")
         return self.plugins.enable(name)   # only reachable after all checks
     ```
   - `disable(name)`/`remove(name)`: unconditional passthrough; `remove` also
     deletes the approval record.
   - `update(name)`: `self.plugins.update(name)` then **delete the approval
     record** (content changed) so a re-scan+approval is forced before re-enable.
   - `list()/hub()/rescan()/set_visibility()`, `list_hooks()/create_hook()/
     delete_hook()`, `list_agent_toolsets()/set_agent_toolset()` delegate to the
     adapter with input validation.
   - Add `PluginAPIError` to `EXPECTED_ERRORS` in `xnobrain/services/__init__.py`
     (or `platform.py` where it is defined) so handlers map it.
   - Register `PluginService` on `XNOBrainApplication` (compose in
     `xnobrain/app.py`); native-tool methods may live on `PlatformService`
     instead, beside skills/MCP — pick one and keep the handler wiring
     consistent.

## Phase 3 — Handlers + routes {#phase-3}

9. **Handlers** — `xnobrain/handlers/api.py`, add to the `operations` dict in
   `_operation` (async methods are already awaited by `dispatch`):
   ```python
   "plugins_list":     (s.plugins.list, "plugins retrieved successfully", 200),
   "plugins_hub":      (s.plugins.hub, "plugin hub retrieved successfully", 200),
   "plugins_rescan":   (s.plugins.rescan, "plugins rescanned successfully", 200),
   "plugins_install":  (lambda: s.plugins.install(body), "plugin installed (disabled) successfully", 201),
   "plugin_scan":      (lambda: s.plugins.scan(p["name"]), "plugin scanned successfully", 200),
   "plugin_approve":   (lambda: s.plugins.approve(p["name"], body), "plugin approval recorded", 200),
   "plugin_enable":    (lambda: s.plugins.enable(p["name"]), "plugin enabled successfully", 200),
   "plugin_disable":   (lambda: s.plugins.disable(p["name"]), "plugin disabled successfully", 200),
   "plugin_update":    (lambda: s.plugins.update(p["name"]), "plugin updated successfully", 200),
   "plugin_remove":    (lambda: s.plugins.remove(p["name"]), "plugin removed successfully", 200),
   "plugin_visibility":(lambda: s.plugins.set_visibility(p["name"], body), "plugin visibility updated", 200),
   "tools_list":       (lambda: s.list_agent_toolsets(p["agent_id"]), "tools retrieved successfully", 200),
   "tools_patch":      (lambda: s.set_agent_toolset(p["agent_id"], p["toolset"], body), "tool updated successfully", 200),
   "hooks_list":       (s.plugins.list_hooks, "hooks retrieved successfully", 200),
   "hook_create":      (lambda: s.plugins.create_hook(body), "hook created successfully", 201),
   "hook_delete":      (lambda: s.plugins.delete_hook(body), "hook deleted successfully", 200),
   ```
   (Use whichever service object holds each method; `s` is `self.service`.)
10. **Routes** — `xnobrain/routes/setup.py`, add to `ROUTES` (one tag group
    `("Plugins",)`; hooks/tools may share it or use `("Hooks",)`/`("Tools",)`):
    ```python
    Route("GET",    "/api/brain/v1/plugins",                 "plugins_list", tags=("Plugins",)),
    Route("GET",    "/api/brain/v1/plugins/hub",             "plugins_hub", tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/rescan",          "plugins_rescan", tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/install",         "plugins_install", PluginInstall, tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/{name}/scan",     "plugin_scan", tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/{name}/approve",  "plugin_approve", PluginApproval, tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/{name}/enable",   "plugin_enable", tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/{name}/disable",  "plugin_disable", tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/{name}/update",   "plugin_update", tags=("Plugins",)),
    Route("DELETE", "/api/brain/v1/plugins/{name}",          "plugin_remove", tags=("Plugins",)),
    Route("POST",   "/api/brain/v1/plugins/{name}/visibility","plugin_visibility", PluginVisibility, tags=("Plugins",)),
    Route("GET",    "/api/brain/v1/agents-tools/{agent_id}",  "tools_list", tags=("Tools",)),
    Route("PATCH",  "/api/brain/v1/agents-tools/{agent_id}/{toolset}", "tools_patch", EnabledPatch, tags=("Tools",)),
    Route("GET",    "/api/brain/v1/hooks",                    "hooks_list", tags=("Hooks",)),
    Route("POST",   "/api/brain/v1/hooks",                    "hook_create", HookCreate, tags=("Hooks",)),
    Route("DELETE", "/api/brain/v1/hooks",                    "hook_delete", HookDelete, tags=("Hooks",)),
    ```
    Keep these ahead of the SPA catch-all (the existing reorder at the bottom of
    `setup_routes` already handles that).

## Phase 4 — Native tool toggles {#phase-4}

11. Implement `list_agent_toolsets`/`set_agent_toolset` per steps 5 and 8. Add
    `PlatformService` (or `PluginService`) wrappers that validate `agent_id` via
    `repository.profile_path`, snapshot the profile `config.yaml`
    (`repository.snapshot(agent_id, "config", "config", yaml_bytes)`) before the
    toolset write, and reject unknown/privileged toolsets. Confirm a toggle is
    visible to a subsequent Hermes chat run (`--toolsets` resolution reads the
    same config).

## Phase 5 — Frontend {#phase-5}

12. **API clients** — new `src/api/plugins.ts` (clone `src/api/skills.ts`;
    `ROOT='/api/brain/v1/plugins'`; methods `list, hub, rescan, install,
    scan, approve, enable, disable, update, remove, setVisibility`) and new
    `src/api/tools.ts` (`ROOT='/api/brain/v1/agents-tools'`; `list`,
    `setEnabled`), plus `src/api/hooks.ts`. Add DTOs to
    `src/api/contracts/agentGateway.ts`.
13. **Hooks/state** — new `src/hooks/usePlugins.ts` and
    `src/hooks/useAgentTools.ts`, cloning the optimistic-then-reconcile
    pattern from `useAssistants.ts` (`setSkillEnabled`).
14. **Plugins page** — new `src/components/PluginsView.tsx` (clone
    `SkillsView.tsx`): catalog grid + install panel + scan-report/approval
    dialog + per-card lifecycle actions. Add `'plugins'` to `CenterView`
    (`src/types.ts`); branches in `src/hooks/useRouter.ts`
    `parseRoute`/`computeUrl`; a nav row in `src/components/Sidebar.tsx`
    `navItems`; a branch in `src/App.tsx`'s center-view ternary.
15. **Per-agent Tools panel** — add a `'tools'` tab to
    `src/components/RightPanel.tsx` (clone the `rightView === 'skills'`
    block); add `'tools'` to `RightView` and the `panel` whitelist in
    `useRouter.ts`. Render toolset toggles with `enabled/available/ready` badges
    and a reason when not ready.
16. **i18n** — add `pluginsView`, `agentTools`, and `hooks` namespaces to all 7
    locale files under `src/locales/` (`en, vi, es, fr, de, ja, zh`).

## Phase 6 — Tests + docs {#phase-6}

17. Tests per [validation.md](validation.md): compatibility, service-gate unit
    tests, handler tests, integration lifecycle, frontend component/hook tests.
18. Docs: update `docs/architecture.md`, `docs/api.md`, `docs/development.md`
    with the plugin/hook/tool surfaces, the scan+approval gate, the pinned
    Hermes version, and troubleshooting. Do not add a static `docs/openapi.yaml`.

## Editing checklist per endpoint (repeat)

`models/api.py` (+`models/__init__.py`, +route import) → `integrations/plugins.py`
method → `services/plugins.py` (or `platform.py`) method (with the gate for
enable) → `handlers/api.py` `operations` entry → `routes/setup.py` `Route(...)`
→ frontend client/hook/component → i18n keys → tests.
