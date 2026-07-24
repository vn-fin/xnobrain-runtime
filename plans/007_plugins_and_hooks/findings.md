# 007 — Findings

Evidence for [README.md](README.md). Recheck every path/line against the pinned
Hermes revision before implementation; line numbers drift.

Hermes source root (read-only, EXTEND — never fork):
`/home/kim/Documents/xno/brain4all-dev/brain4all/.tools/hermes-agent/`

Import roots (top-level packages inside that tree): `hermes_cli`, `tools`,
`toolsets` (single module `toolsets.py`), `gateway`, `agent`, `hermes_state`.
Precedent: `brain4all/integrations/kanban.py` already does
`from hermes_cli import kanban_db`.

## 1. What Hermes provides

### 1.1 Native plugin HTTP endpoints (`hermes_cli/web_server.py`)

Short names in the brief map to `/api/dashboard/...`. Verified handlers:

| Method + path | Handler (line) | Body | Response | Backing call |
| --- | --- | --- | --- | --- |
| `GET /api/dashboard/plugins` | `get_dashboard_plugins` (19055) | none | array of plugin dicts (`_`-keys stripped) | `_get_dashboard_plugins()` + `_get_enabled_set`/`_get_disabled_set` |
| `GET /api/dashboard/plugins/rescan` | `rescan_dashboard_plugins` (19095) | none | `{ok, count}` | `_get_dashboard_plugins(force_rescan=True)` |
| `GET /api/dashboard/plugins/hub` | `get_plugins_hub` (19227) | none (token-gated) | `{plugins[], orphan_dashboard_plugins[], providers{}}` | `_merged_plugins_hub()` |
| `POST /api/dashboard/agent-plugins/install` | `post_agent_plugin_install` (19238) | `{identifier:str, force:bool=False, enable:bool=True}` | `{ok, plugin_name, warnings[], missing_env[], enabled}` | `plugins_cmd.dashboard_install_plugin` |
| `POST /api/dashboard/agent-plugins/{name}/enable` | (19267) | none | `{ok, name, unchanged}` | `plugins_cmd.dashboard_set_agent_plugin_enabled(name, enabled=True)` |
| `POST /api/dashboard/agent-plugins/{name}/disable` | (19279) | none | `{ok, name, unchanged}` | `dashboard_set_agent_plugin_enabled(name, enabled=False)` |
| `POST /api/dashboard/agent-plugins/{name}/update` | (19291) | none | `{ok, name, output, unchanged}` | `plugins_cmd.dashboard_update_user_plugin` |
| `DELETE /api/dashboard/agent-plugins/{name}` | (19304) | none | `{ok, name}` | `plugins_cmd.dashboard_remove_user_plugin` |
| `PUT /api/dashboard/plugin-providers` | `put_plugin_providers` (19322) | `{memory_provider?, context_engine?}` | `{ok}` | `plugins_cmd._save_memory_provider`/`_save_context_engine` |
| `POST /api/dashboard/plugins/{name}/visibility` | `post_plugin_visibility` (19344) | `{hidden:bool}` | `{ok, name, hidden}` | mutates `dashboard.hidden_plugins` + `save_config` |

Path params use `{name:path}`, sanitized by `_validate_plugin_name` (19259,
rejects empty / `..` / `\`). Most are `_require_token`-gated.

### 1.2 Plugin CLI + dashboard helpers (`hermes_cli/plugins_cmd.py`)

Public-enough functions Brain4All will ride in-process (all verified present):

- `dashboard_install_plugin(identifier, *, force, enable) -> dict` (1762) —
  resolves the git URL (warns on `http://`/`file://`), `git clone --depth 1`
  into `~/.hermes/plugins/<name>`, reads manifest, sanitizes name, enforces a
  `manifest_version` ceiling, computes `missing_env`, and — **only if
  `enable=True`** — mutates the enabled/disabled sets. **No scan. No approval.
  No prompt.**
- `dashboard_set_agent_plugin_enabled(name, *, enabled) -> dict` (1895) —
  toggles `plugins.enabled`/`plugins.disabled`, then calls
  `_toggle_plugin_toolset(name, enable=...)` to add/remove the plugin's
  contributed toolset from `platform_toolsets`.
- `dashboard_update_user_plugin(name) -> dict` (1938) — `git pull`.
- `dashboard_remove_user_plugin(name) -> dict` (1987) — refuses bundled;
  else `shutil.rmtree` under `~/.hermes/plugins/`.
- State helpers: `_get_enabled_set` (745), `_get_disabled_set` (693),
  `_discover_all_plugins` (1044, returns `(name, version, description, source,
  dir_path, key)` tuples).

There is **no CLI hub-browse / marketplace command**. Plugin sourcing is
git-URL / `owner/repo` shorthand only. The "hub" exists solely as the
`_merged_plugins_hub()` aggregation in `web_server.py` (installed catalog +
runtime status + auth-required flags + providers).

### 1.3 Hooks — three layers

1. **Plugin callback hooks** — `VALID_HOOKS` in `hermes_cli/plugins.py:135`:
   `{pre_tool_call, post_tool_call, transform_terminal_output,
   transform_tool_result, transform_llm_output, pre_llm_call, post_llm_call, …}`
   plus a verification-loop gate. Installed plugins register Python callbacks on
   these events. This is the **core extensibility layer** referenced in the
   brief. Managing these = installing/enabling the plugin that provides them.
2. **Shell-command hooks** — `agent/shell_hooks.py`, exposed at
   `GET/POST/DELETE /api/ops/hooks` (`web_server.py` `list_hooks` 13907,
   `create_hook` 13966, `delete_hook` 14028). A hook is a config-`hooks:` entry
   `{command, matcher?, timeout?}` under a `VALID_HOOKS` event. **Approval-gated:**
   `create_hook` calls `shell_hooks._record_approval(event, command)` when
   `approve=True`; `delete_hook` calls `shell_hooks.revoke(command)`. A hook
   only fires if allowlisted. Bodies: `HookCreate {event, command, matcher?,
   timeout?, approve=True}`, `HookDelete {event, command}`.
   - Useful shell-hook symbols: `iter_configured_hooks(cfg)` (287),
     `allowlist_entry_for(event, command)` (861), `script_is_executable(command)`
     (888), `load_allowlist` (632), `_record_approval` (760), `revoke` (782),
     `ShellHookSpec` (161).
3. **Gateway lifecycle hooks** — `gateway/hooks.py` `HookRegistry` (52),
   dir-based (`~/.hermes/hooks/<name>/HOOK.yaml` + `handler.py`), events
   `gateway:startup`, `session:start|end|reset`, `agent:start|step|end`,
   `command:*`. `_register_builtin_hooks` (72) is a **no-op**;
   `gateway/builtin_hooks/` ships **no** built-in hooks (reserved extension
   point only). Observability only for this plan.

### 1.4 Native tool registry and toolset config

- Tool registry: `tools/registry.py` singleton `registry` (765).
  `registry.get_definitions(tool_names)` (530) returns only tools whose
  `check_fn()` passes (credential/binary presence, ~30 s TTL cache). Registration
  via `registry.register(name, toolset, …)` at import.
- Toolset catalog: **`toolsets.py`** (top-level). `TOOLSETS` dict (96),
  `_HERMES_CORE_TOOLS` (31), `resolve_toolset(name)` (689). Toolsets include
  `web`, `search`, `browser`, `image_gen`, `computer_use`, `code_execution`,
  `terminal`, `file`, `todo`, `tts`, `vision`, `video`, `video_gen`, `x_search`.
- **Per-agent/platform enablement lives in the profile `config.yaml`:**
  - `platform_toolsets:` — maps platform key (`cli`, `telegram`, …) to a preset
    or an explicit toolset list. Docs: `cli-config.yaml.example` 863–922.
  - `agent.disabled_toolsets:` — a subtractive denylist (`hermes_cli/config.py`
    default `[]` ~1124; honored in `hermes_cli/tools_config.py` 1986–1994).
  - Resolution: `tools_config._get_platform_tools(config, platform)` (1728) →
    `_get_effective_configurable_toolsets` (219) → `toolsets.resolve_toolset` →
    `registry.get_definitions`. Writer: `tools_config._save_platform_tools(
    config, platform, enabled_toolset_keys)` (2024).
  - Runtime per-tool gating is by each tool's `check_fn` (creds/binary), not a
    per-tool allow-list — so browser/image/computer-use appear only when their
    backing binary/key is present. Brain4All must surface this "available vs
    enabled vs ready" distinction honestly.
- **Plugin native-tool override opt-in:** `plugins.entries.<id>.allow_tool_override`
  in `config.yaml` (read `hermes_cli/plugins.py:470`, wired via
  `registry.register_plugin_override_policy`, enforced in `registry.register`
  402–417). The CLI enable path prompts for this; the **dashboard enable path
  does not** — a Brain4All safety concern (see §4).

## 2. What Brain4All has (templates) and lacks

### Has (the wiring template)
- MCP: `PlatformService.get_mcp/update_mcp` (`services/platform.py` 575–588),
  model `MCPConfig` (`models/api.py` 211), routes `mcp_get/mcp_put`
  (`routes/setup.py` 70–71). Pure profile-file feature.
- Skills (closest template): `PlatformService.list_skills/install_skill/
  set_skill_enabled/remove_skill/list_default_skills` (198–227), integration
  `AgentManager.list_skills/install_skill/set_skill_enabled/remove_skill`
  (`integrations/hermes.py`), root-profile `GlobalConfigManager.install_skill`
  (`integrations/config.py` 167, shells `hermes skills install … --yes`),
  models `SkillInstall`/`EnabledPatch`, routes `skills_*` (52–57).
- **Enable/disable persistence pattern**: a **denylist in `config.yaml`** —
  skills use `skills.disabled` (`_disabled_skills`/`_set_skill_enabled`/
  `_write_disabled_skills` in `integrations/hermes.py`). Native-tool toggles
  mirror this exactly via `agent.disabled_toolsets`.
- Persistence primitives: `FileRepository.atomic_write/atomic_json/atomic_yaml`
  and `snapshot(agent_id, kind, target, content)` (`repositories/files.py`
  73–260). **`snapshot()` `kind` is a hard whitelist `{memory, skills, config}`
  (files.py:75)** — adding a `"plugins"` kind requires editing that set.
- Dispatch: single `operations` dict in `handlers/api.py::_operation`; add one
  entry + one `Route(...)` (+ optional model) per endpoint.
- Provider forcing: `normalize_nine_router_config` on every config write
  (`integrations/nine_router.py` 58; called in both config managers). Any new
  config write path must preserve it.
- Native toolset vocabulary already trusted: `SAFE_TOOLSETS` in
  `services/platform.py:33` = `{browser, code_execution, file, image_gen,
  terminal, todo, tts, video, video_gen, vision, web, x_search}`. Toolsets are
  already threaded per run via the `--toolsets` chat flag (`integrations/
  hermes.py` 626–629).
- Per-agent `plugins/` dir already exists: `PROFILE_STATE_DIRS`/`SEED_DIRS`
  include `"plugins"` (`integrations/hermes.py` 57–66); copied into new agents.

### Lacks
- Any plugin management surface (no list/install/enable/disable/update/remove).
- Any hook management or observability surface.
- Any **persistent per-agent native-tool toggle** — toolsets are only passed
  per-run via `--toolsets`; nothing writes `agent.disabled_toolsets`/
  `platform_toolsets` from Brain4All.
- Any plugin scan/approval gate.

## 3. The exact gap

Brain4All exposes MCP + skills but **not** plugins, hooks, or the built-in tool
set. Hermes exposes all three natively, but its **dashboard plugin install/enable
path performs no malware/OSV scan, no approval, and no tool-override consent** —
only a token gate and an opt-in enabled/disabled allow-list. Plan 007 closes
the surface gap **and** the safety gap: it adds the plugin/hook/tool surfaces
and inserts a mandatory scan+approval gate (built from Hermes' own
`skills_guard`/`osv_check`) in front of every Brain4All enable path.

## 4. Security model

- **Scanners that exist** (reuse, do not reinvent):
  - `tools/skills_guard.py` — regex threat scanner. `scan_skill(path, source)`
    (632), `scan_skill_cached(...)` (716, content-hash cache + attestation),
    `should_allow_install(result, force) -> (True|False|None, reason)` (766;
    `None` = "ask"/needs approval; dangerous community/trusted cannot be
    force-overridden), `format_scan_report(result)` (810). Verdicts safe /
    caution / dangerous; `INSTALL_POLICY` matrix over trust levels. It scans a
    **directory tree** — a cloned plugin dir is exactly that shape.
  - `tools/osv_check.py` — `check_package_for_malware(command, args) ->
    Optional[str]` (26). Queries OSV for `MAL-*` advisories on npm/PyPI
    packages. Currently invoked **only** for MCP `npx`/`uvx` spawns
    (`tools/mcp_tool.py` ~2380). Fail-open on network error.
- **Neither scanner runs in the plugin install path today** (grep-confirmed).
  Plugin install = git clone + move.
- **Approval primitives to reuse:** shell-hook approval
  `shell_hooks._record_approval`/`revoke` (the native hook approval path);
  the `RunApproval` model shape (`models/api.py:162`, `choice: once|session|
  always|deny`) for the approval decision semantics; and
  `should_allow_install`'s allow/block/ask verdict as the decision engine.
- **Sandboxing:** plugins run arbitrary Python in-process; there is no runtime
  sandbox. The only containment is the enabled/disabled allow-list and the
  `allow_tool_override` opt-in. Therefore the **pre-enable scan + explicit
  approval is the primary control** and must be enforced in Brain4All's service,
  not merely offered in the UI.
- **Brain4All rule (mandatory, see [architecture.md](architecture.md) and
  [implementation.md](implementation.md)):** install never auto-enables; the
  service scans the cloned plugin dir with `skills_guard` (+ `osv_check` on any
  declared package deps); a `dangerous` verdict blocks; `caution`/`ask`
  requires an explicit user approval recorded (with the scanned content hash)
  before enable; `enable` re-verifies the content hash and refuses if the scan
  is missing, stale, dangerous, or unapproved. Brain4All never grants
  `allow_tool_override` automatically.

## 5. Compatibility APIs to pin and test

Phase 0 imports and asserts (see [implementation.md](implementation.md#phase-0)):

- `from hermes_cli import plugins_cmd` → `dashboard_install_plugin`,
  `dashboard_set_agent_plugin_enabled`, `dashboard_update_user_plugin`,
  `dashboard_remove_user_plugin`, `_get_enabled_set`, `_get_disabled_set`,
  `_discover_all_plugins`.
- `from hermes_cli.plugins import VALID_HOOKS`.
- `from agent import shell_hooks` → `iter_configured_hooks`, `_record_approval`,
  `revoke`, `allowlist_entry_for`, `script_is_executable`, `load_allowlist`,
  `ShellHookSpec`.
- `import toolsets` → `TOOLSETS`, `resolve_toolset`, `_HERMES_CORE_TOOLS`.
- `from tools.registry import registry` → `get_definitions`,
  `get_registered_toolset_names`, `check_toolset_requirements`.
- `from hermes_cli import tools_config` → `_get_platform_tools`,
  `_save_platform_tools`, `_get_effective_configurable_toolsets`.
- `from tools import skills_guard` → `scan_skill`, `scan_skill_cached`,
  `should_allow_install`, `format_scan_report`, `ScanResult`.
- `from tools import osv_check` → `check_package_for_malware`.

The test exercises an end-to-end lifecycle in a temporary `HERMES_HOME` using a
real local plugin directory (no network): discover → scan → enable-blocked →
approve → enable → disable → remove, plus a toolset toggle round-trip and a
shell-hook create/approve/list/delete round-trip.

## 6. Risks

- **Private/underscore symbols** (`_get_enabled_set`, `_discover_all_plugins`,
  `_get_platform_tools`, `_save_platform_tools`, `skills_guard._…`) may change
  between Hermes revisions. Mitigation: the pin + compatibility test; a single
  remediation message on failure; upstream a public shim if churn is high.
- **`_merged_plugins_hub` is a `web_server.py`-local function**, awkward to
  import. Mitigation: build the installed catalog from
  `plugins_cmd._discover_all_plugins` + the enabled/disabled sets; treat the
  richer hub fields (auth_required, providers) as optional. See
  [approaches.md](approaches.md).
- **Scanner false-negatives** — regex scanning cannot catch all malicious code.
  Communicate limits in the UI; keep enable opt-in and reversible.
- **`allow_tool_override`** — never auto-granted; a plugin that requests it is
  flagged in the scan report and requires a distinct, explicit approval.
- **Provider drift** — any new `config.yaml` write must call
  `normalize_nine_router_config`.
- **Snapshot-kind whitelist** — remember to extend `repositories/files.py:75`.

## 7. Open questions {#open-questions}

- Is a curated remote plugin registry desired later? Today "hub" = installed
  catalog + git-URL install. If yes, it is a separate plan with its own trust
  model.
- Plugin enable in Hermes is **global** (root `config.yaml`), while per-agent
  differentiation is expressed through the agent's toolset config. Confirm the
  product wants "install/enable globally, then enable-per-agent via toolsets"
  (the model this plan adopts) versus true per-agent installs (not supported by
  Hermes).

## 8. Deferred {#deferred}

- `PUT /api/dashboard/plugin-providers` (memory provider / context engine) — a
  settings concern, not plugin lifecycle; out of scope here.
- Editing plugin or hook handler source in-app.
