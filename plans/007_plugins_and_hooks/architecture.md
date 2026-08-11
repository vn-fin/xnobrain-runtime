# 007 — Architecture

Companion to [README.md](README.md), [findings.md](findings.md),
[approaches.md](approaches.md), [implementation.md](implementation.md),
[validation.md](validation.md).

## Layering fit

Plan 007 follows the fixed Brain4All boundaries — nothing new architecturally:

```
routes/setup.py      URL table only (the one route-assembly point)
      |
handlers/api.py      HTTP translation; one `operations` dict entry per route
      |
services/plugins.py  RULES — incl. the mandatory scan+approval-before-enable gate
services/platform.py (native-tool toggles live here, beside skills/MCP)
      |
integrations/plugins.py   ADAPT Hermes plugin/hook/tool/scanner functions in-process
integrations/hermes.py    (per-agent toolset read/write, beside skills.disabled)
      |
repositories/files.py     atomic writes + snapshot-before-mutation; approval record
      |
Hermes plugin store (~/.hermes/plugins, root config.yaml plugins.enabled/disabled)
per-agent profile config.yaml (platform_toolsets / agent.disabled_toolsets)
```

- **Ride, don't fork.** `integrations/plugins.py` imports Hermes' own
  `plugins_cmd.dashboard_*`, `skills_guard`, `osv_check`, `shell_hooks`,
  `toolsets`, `tools.registry`, and `tools_config` — the same in-process style
  as `integrations/kanban.py` importing `hermes_cli.kanban_db`. No HTTP hop, no
  copied logic.
- **One process.** Everything runs inside the FastAPI/Hermes app on :8642.
- **Provider stays 9router.** Any `config.yaml` write goes through the existing
  managers so `normalize_nine_router_config` still runs.

## Where state lives (no app DB)

| State | Owner | Location |
| --- | --- | --- |
| Installed plugin files | Hermes | `~/.hermes/plugins/<name>/` |
| Global enable/disable | Hermes | root `config.yaml` `plugins.enabled` / `plugins.disabled` |
| Plugin tool-override grant | Hermes | root `config.yaml` `plugins.entries.<id>.allow_tool_override` |
| Plugin visibility | Hermes | root `config.yaml` `dashboard.hidden_plugins` |
| Per-agent tool/plugin-toolset enablement | Hermes | profile `config.yaml` `platform_toolsets` / `agent.disabled_toolsets` |
| Shell hooks + approval allowlist | Hermes | config `hooks:` + `shell_hooks` allowlist file |
| **Brain4All scan + approval record** | Brain4All | `DATA_DIR/plugins/approvals/<name>.json` (atomic, snapshotted) |

The only new Brain4All-owned artifact is the **scan/approval record** — a small
JSON per plugin holding `{name, content_sha256, verdict, scanned_at,
approved: bool, approved_at, approval_choice, findings_summary}`. It is written
atomically via `FileRepository.atomic_json` and snapshotted before mutation
(reusing the skills pattern). It is the enforcement anchor for the enable gate;
it never stores plugin source or secrets.

## Data flow: install → scan → approve → enable

1. **Install** (`POST …/plugins/install`, `identifier` = git URL / `owner/repo`).
   Service calls `integrations/plugins.install(identifier, force)` which calls
   Hermes `dashboard_install_plugin(identifier, force=force, enable=False)` —
   **always `enable=False`**. The plugin lands in `~/.hermes/plugins/<name>` but
   is inert.
2. **Scan** (automatic, same request). Service calls
   `integrations/plugins.scan(name)` → `skills_guard.scan_skill_cached(dir,
   source="community")` on the cloned dir, plus `osv_check.check_package_for_malware`
   on any declared npm/PyPI deps in the manifest. Result → `should_allow_install`
   → verdict `allow` / `ask` / `block`. The service writes the scan record
   (content hash + verdict + sanitized findings summary) and returns it. A
   `block` (dangerous) verdict leaves the plugin installed-but-permanently-gated.
3. **Approve** (`POST …/plugins/{name}/approve`, body `PluginApproval`).
   Requires an existing non-`block` scan whose `content_sha256` still matches the
   on-disk dir. On `choice="approve"` the service records approval (and, when the
   plugin registers hooks/tool-overrides, records the corresponding
   `shell_hooks._record_approval` where applicable) and marks the record
   `approved=True`. `choice="deny"` clears any approval.
4. **Enable** (`POST …/plugins/{name}/enable`). The service **re-verifies**:
   scan record exists, verdict != dangerous, `content_sha256` matches current
   dir, `approved=True`. Only then calls Hermes
   `dashboard_set_agent_plugin_enabled(name, enabled=True)`. Any failed check
   raises a stable error (`plugin_scan_required` / `plugin_scan_stale` /
   `plugin_unsafe` / `plugin_approval_required`, all 409).

This makes "scan + approval before enable" a code invariant, not UI etiquette.
Disable / update / remove need no approval; **update invalidates the record**
(git pull changes the content hash) so a re-scan + re-approval is forced before
the updated plugin can be re-enabled.

## New Brain4All API contract

Base: `/api/brain/v1`. Envelope: existing `APIEnvelope`
(`{success, data, message, status_code}`). Errors: existing `failure()` with
stable `code`s. All new routes registered only in `brain4all/routes/setup.py`;
each maps to one `operations` entry in `handlers/api.py`.

### Plugins (global catalog + lifecycle)

| Method | Path | operation | Body model |
| --- | --- | --- | --- |
| GET | `/plugins` | `plugins_list` | — |
| GET | `/plugins/hub` | `plugins_hub` | — |
| POST | `/plugins/rescan` | `plugins_rescan` | — |
| POST | `/plugins/install` | `plugins_install` | `PluginInstall` |
| POST | `/plugins/{name}/scan` | `plugin_scan` | — |
| POST | `/plugins/{name}/approve` | `plugin_approve` | `PluginApproval` |
| POST | `/plugins/{name}/enable` | `plugin_enable` | — |
| POST | `/plugins/{name}/disable` | `plugin_disable` | — |
| POST | `/plugins/{name}/update` | `plugin_update` | — |
| DELETE | `/plugins/{name}` | `plugin_remove` | — |
| POST | `/plugins/{name}/visibility` | `plugin_visibility` | `PluginVisibility` |

### Per-agent native tools (and per-agent plugin-toolset enablement)

| Method | Path | operation | Body model |
| --- | --- | --- | --- |
| GET | `/agents-tools/{agent_id}` | `tools_list` | — |
| PATCH | `/agents-tools/{agent_id}/{toolset}` | `tools_patch` | `EnabledPatch` (reused) |

`tools_list` returns each toolset with `{toolset, label, source: "native"|"plugin",
enabled, available, ready, requires}` where `enabled` = configured for the agent,
`available` = registered, `ready` = `check_fn`/`check_toolset_requirements`
passes. A plugin's contributed toolset appears here with `source: "plugin"`, so
"per-agent plugin enablement" and "native tool toggle" are one honest control.

### Hooks

| Method | Path | operation | Body model |
| --- | --- | --- | --- |
| GET | `/hooks` | `hooks_list` | — |
| POST | `/hooks` | `hook_create` | `HookCreate` |
| DELETE | `/hooks` | `hook_delete` | `HookDelete` |

`hooks_list` returns `{shell: [{event, command_label, matcher, timeout,
approved, executable}], plugin: [{event, plugin, description}], gateway:
[{name, events}], valid_events: [...]}`. Plugin/gateway entries are read-only.
Hook commands are shown as a **sanitized label** (basename + hash), never the
full command string, in list responses and logs.

### New Pydantic models (`brain4all/models/api.py`, exported via `models/__init__.py`)

```python
class PluginInstall(BaseModel):
    identifier: str = Field(min_length=1, max_length=512)   # git URL or owner/repo
    force: bool = False

class PluginApproval(BaseModel):
    choice: Literal["approve", "deny"]
    content_sha256: str = Field(min_length=64, max_length=64)  # must match the scan

class PluginVisibility(BaseModel):
    hidden: bool

class HookCreate(BaseModel):
    event: str = Field(min_length=1, max_length=64)   # validated against VALID_HOOKS
    command: str = Field(min_length=1, max_length=2000)
    matcher: str | None = Field(default=None, max_length=256)
    timeout: int | None = Field(default=None, ge=1, le=600)
    approve: bool = True

class HookDelete(BaseModel):
    event: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=2000)
```
Native-tool toggles reuse the existing `EnabledPatch {enabled: bool}`.

## Integration adapter (`brain4all/integrations/plugins.py`, new)

`class PluginManager` — no policy, no HTTP; adapts Hermes and serializes to safe
dicts at the boundary. Constructed like the other managers (root/profiles roots
from env), stored on `Brain4AllApplication` and injected into the service.

Representative surface (thin wrappers, each pinned + compat-tested):

```python
class PluginManager:
    def list_plugins(self) -> list[dict]                 # _discover_all_plugins + enabled/disabled sets
    def browse_hub(self) -> dict                         # installed catalog + status (+ optional hub extras)
    def rescan(self) -> dict
    def install(self, identifier: str, *, force: bool) -> dict   # dashboard_install_plugin(enable=False)
    def plugin_dir(self, name: str) -> Path              # sanitized ~/.hermes/plugins/<name>
    def scan(self, name: str) -> dict                    # skills_guard + osv_check -> verdict + findings
    def enable(self, name: str) -> dict                  # dashboard_set_agent_plugin_enabled(enabled=True)
    def disable(self, name: str) -> dict
    def update(self, name: str) -> dict                  # dashboard_update_user_plugin
    def remove(self, name: str) -> dict                  # dashboard_remove_user_plugin
    def set_visibility(self, name: str, hidden: bool) -> dict
    # hooks
    def list_hooks(self) -> dict                         # shell_hooks + VALID_HOOKS + gateway registry
    def create_hook(self, event, command, matcher, timeout, approve) -> dict
    def delete_hook(self, event, command) -> dict
    # per-agent native tools
    def list_agent_toolsets(self, agent_id: str) -> list[dict]   # toolsets.TOOLSETS + registry + profile config
    def set_agent_toolset(self, agent_id: str, toolset: str, enabled: bool) -> dict  # writes agent.disabled_toolsets
```

`set_agent_toolset` mirrors skills' denylist writer: load the profile
`config.yaml`, add/remove the toolset from `agent.disabled_toolsets`, run
`normalize_nine_router_config`, then `atomic_yaml`. It rejects any toolset not in
the trusted set (`SAFE_TOOLSETS` ∪ enabled-plugin toolsets).

The adapter raises typed `ValueError` subclasses carrying `.status`/`.code`
(new `PluginAPIError`, added to `EXPECTED_ERRORS`) so the handler maps them
cleanly. It never returns raw command strings, plugin source, absolute stored
paths, or scanner internals.

## Service (`brain4all/services/plugins.py`, new)

`class PluginService` owns the **scan+approval rule** and the approval record.
Constructed with `(repository, plugins: PluginManager, agents: AgentManager)`.
Key methods and the invariant they enforce (pseudocode; full steps in
[implementation.md](implementation.md#phase-2)):

```python
def install(self, body) -> dict:
    result = self.plugins.install(body["identifier"], force=bool(body.get("force")))
    scan = self._scan_and_record(result["plugin_name"])   # never enables
    return {"plugin": result, "scan": scan}

def approve(self, name, body) -> dict:
    record = self._load_record(name)                       # 404 if no scan
    if record["verdict"] == "dangerous": raise unsafe
    if record["content_sha256"] != body["content_sha256"]: raise stale
    record["approved"] = body["choice"] == "approve"; record["approved_at"] = now
    self.repository.snapshot(...); self.repository.atomic_json(path, record)
    return record

def enable(self, name) -> dict:
    record = self._load_record(name)                       # 409 plugin_scan_required if absent
    if record["verdict"] == "dangerous": raise plugin_unsafe (409)
    if record["content_sha256"] != self.plugins.content_hash(name): raise plugin_scan_stale (409)
    if not record.get("approved"): raise plugin_approval_required (409)
    return self.plugins.enable(name)                       # ONLY reachable after all checks
```

`enable()` has **no path** that reaches `self.plugins.enable(name)` without a
passing, fresh, approved scan. `update()` deletes the approval record so the
next enable re-runs the gate. `disable()`/`remove()` are unconditional.

## React UI

Frontend source lives under the root `src/` directory. No react-router;
routing is the hand-rolled `hooks/useRouter.ts`.

- **Plugins page** — new `src/components/PluginsView.tsx`, cloned from
  `SkillsView.tsx`: a hub/catalog card grid (installed + status badges), an
  "Install from URL" panel, and per-card actions (scan report, approve, enable,
  disable, update, remove, hide). The **approval dialog** shows the sanitized
  scan verdict/findings and requires an explicit click before enable; a
  `dangerous` verdict disables the enable control entirely.
  Wire-in: add `'plugins'` to `CenterView` (`types.ts`), branches in
  `useRouter.ts` `parseRoute`/`computeUrl` (`/plugins`), a nav row in
  `components/Sidebar.tsx` `navItems`, and a branch in `App.tsx`'s center-view
  ternary. New client `src/api/plugins.ts` (clone `api/skills.ts`,
  `ROOT = '/api/brain/v1/plugins'`), hook `src/hooks/usePlugins.ts`
  (clone the skills logic in `useAssistants.ts`, optimistic-then-reconcile).
- **Per-agent Tools/Capabilities panel** — new `'tools'` tab in
  `components/RightPanel.tsx`, cloned from its `rightView === 'skills'` block:
  a toggle list of toolsets with `enabled`/`available`/`ready` badges (a
  not-ready tool shows why, e.g. "needs FAL_KEY"). Add `'tools'` to `RightView`
  and to the `panel` whitelist in `useRouter.ts`. New client
  `src/api/tools.ts` and hook `src/hooks/useAgentTools.ts`.
- **Hooks visibility** — a read-mostly section (in the Plugins page or a
  Settings subsection) listing shell/plugin/gateway hooks; shell hooks get
  create-with-approval and delete controls.
- **i18n** — add `pluginsView`, `agentTools`, and `hooks` key namespaces to all
  7 locale JSONs under `src/locales/` (`en, vi, es, fr, de, ja, zh`),
  mirroring the `skillsView`/`agentSkills` structure.

## Safe-install sequence diagram

```
User        PluginsView      Handler         PluginService        PluginManager        Hermes / Scanners
 |  install(url)  |               |                 |                   |                      |
 |--------------->|  POST         |                 |                   |                      |
 |               | /plugins/install                |                   |                      |
 |               |-------------->| plugins_install |                   |                      |
 |               |               |---------------->| install(body)     |                      |
 |               |               |                 | install(url,force)|                      |
 |               |               |                 |------------------>| dashboard_install_   |
 |               |               |                 |                   | plugin(enable=False) |
 |               |               |                 |                   |--------------------->| git clone -> ~/.hermes/plugins
 |               |               |                 |                   |<---------------------| {plugin_name, missing_env}
 |               |               |                 | _scan_and_record()|                      |
 |               |               |                 |------------------>| scan(name)           |
 |               |               |                 |                   | skills_guard.scan +  |
 |               |               |                 |                   | osv_check ---------->| verdict, findings
 |               |               |                 |<------------------| verdict, sha256      |
 |               |               |                 | atomic_json(record: approved=False)      |
 |               |<--------------|<----------------| {plugin, scan}    |                      |
 |  (review scan report; verdict != dangerous)     |                   |                      |
 |  approve(sha) |  POST /plugins/{n}/approve       |                   |                      |
 |-------------->|-------------->| plugin_approve  |                   |                      |
 |               |               |---------------->| approve(name,body)|                      |
 |               |               |                 | verify sha == record.sha; mark approved  |
 |               |               |                 | snapshot + atomic_json(record)           |
 |               |<--------------|<----------------| record(approved=True)                    |
 |  enable       |  POST /plugins/{n}/enable        |                   |                      |
 |-------------->|-------------->| plugin_enable   |                   |                      |
 |               |               |---------------->| enable(name)      |                      |
 |               |               |                 | GATE: record exists? verdict ok?         |
 |               |               |                 |       sha matches disk? approved?        |
 |               |               |                 |  all true --------------->| enable(name) |
 |               |               |                 |                   | dashboard_set_agent_ |
 |               |               |                 |                   | plugin_enabled(True) |
 |               |               |                 |                   |--------------------->| plugins.enabled += name
 |               |<--------------|<----------------|<------------------|<---------------------| {ok, enabled}
```

If any gate check fails, `enable` returns a 409 with a stable code and the
plugin stays inert — Hermes' `dashboard_set_agent_plugin_enabled` is never
reached.
