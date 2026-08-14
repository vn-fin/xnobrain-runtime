# 007 — Plugins, hooks, and native tool toggles

Priority: **P2**. Independent product surface; does not block the Kanban
program (plans 001–004). It depends only on the existing XNOBrain layering and
the pinned Hermes runtime.

Sibling files (read together):

- [findings.md](findings.md) — what Hermes provides, what XNOBrain has and
  lacks, the exact gap, the security model, compatibility APIs to pin.
- [architecture.md](architecture.md) — layering fit, data flow, the new API
  contract, integration adapter, React UI, and the safe-install sequence.
- [approaches.md](approaches.md) — options, trade-offs, and the chosen approach.
- [implementation.md](implementation.md) — ordered, file-by-file steps.
- [validation.md](validation.md) — tests, checks, and the acceptance checklist.

## Goal

Let a non-developer manage the Hermes **extensibility layer** from XNOBrain:

1. **Plugins** — browse the installed/available plugin catalog ("hub"),
   install a plugin from a git URL, enable/disable it, update it, remove it,
   and control per-agent enablement — with a **mandatory malware/OSV scan and
   explicit user approval before any plugin is enabled**.
2. **Hooks** — surface the Hermes hook layer (plugin callbacks on events such
   as `pre_llm_call`, `pre_tool_call`, `transform_llm_output`; shell-command
   hooks; and gateway lifecycle hooks). Provide safe, approval-gated CRUD for
   shell-command hooks and read-only observability of plugin/gateway hooks.
3. **Native tools** — expose Hermes' built-in **toolsets** (browser, image
   generation, code execution, computer use, web search, and the rest) as
   per-agent capability toggles. XNOBrain currently exposes only MCP and
   skills; the built-in tool set is unmanaged.

XNOBrain rides Hermes' native plugin, hook, tool-registry, and security
primitives in-process. It never forks or reimplements them.

## Non-goals

- **No remote plugin marketplace.** Hermes has no curated registry; the "hub"
  is the aggregation of installed/discoverable plugins plus install-by-git-URL.
  See the open question in [findings.md](findings.md#open-questions).
- **No new plugin format, hook format, or tool format.** XNOBrain manages
  Hermes' existing artifacts; it does not define its own.
- **No second API process, database, ORM, Go, or PostgreSQL.** One
  FastAPI/Hermes process (:8642) and one 9router process (:20128).
- **No copied/forked Hermes plugin, hook, tool-registry, or scanner code.**
- **No authoring UI for plugin Python or hook handler code.** Plugins are
  installed from source; XNOBrain does not edit their code.
- **No weakening of the enable gate.** A plugin cannot become enabled through a
  XNOBrain path without a passing scan and an explicit approval record.
- **No exposure of privileged/undeclared toolsets** beyond the vetted set the
  service already trusts (`SAFE_TOOLSETS`).

## Priority and dependencies

- Requires the pinned Hermes runtime (same pin the Kanban program uses) and the
  Phase 0 compatibility test in [implementation.md](implementation.md).
- Reuses the existing route/handler/service/integration/model layering and the
  `repository.snapshot` + atomic-write persistence primitives.
- No dependency on plans 001–006, 008, or 009. Can ship in parallel.

## Scope

| Area | In scope | Where |
| --- | --- | --- |
| Plugin catalog | list installed, browse hub, rescan | Hermes `plugins_cmd` discovery |
| Plugin lifecycle | install (git URL), enable, disable, update, remove, visibility | Hermes `dashboard_*` helpers |
| Plugin safety | OSV/malware scan + approval gate before enable | Hermes `skills_guard` + `osv_check` |
| Per-agent plugin enablement | toggle the plugin's contributed toolset per agent | profile `config.yaml` toolsets |
| Native tools | list + per-agent enable/disable of built-in toolsets | profile `config.yaml` toolsets |
| Hooks (shell) | list, create (approval-gated), delete | Hermes `agent.shell_hooks` |
| Hooks (plugin/gateway) | read-only observability | `VALID_HOOKS`, `gateway/hooks.py` |

Out of scope for this plan: memory-provider/context-engine switching (the
`plugin-providers` endpoint), which is a separate settings concern noted in
[findings.md](findings.md#deferred).

## Phase overview

- **Phase 0 — Pin and prove.** Pin Hermes; add a compatibility test that
  imports and exercises the plugin, hook, tool-registry, scanner, and approval
  symbols. Fail readiness with one remediation message if incompatible.
  ([implementation.md](implementation.md#phase-0))
- **Phase 1 — Models + integration adapter.** New `models` entries and a new
  `xnobrain/integrations/plugins.py` adapter over the Hermes helpers.
  ([implementation.md](implementation.md#phase-1))
- **Phase 2 — Service with the scan+approval rule.** New
  `xnobrain/services/plugins.py` (or `PlatformService` methods) that make the
  scan-and-approve-before-enable step mandatory in code.
  ([implementation.md](implementation.md#phase-2))
- **Phase 3 — Handlers + routes.** New `operations` dict entries and `Route(...)`
  lines in `xnobrain/routes/setup.py`.
  ([implementation.md](implementation.md#phase-3))
- **Phase 4 — Native tool toggles.** Per-agent toolset list + toggle.
  ([implementation.md](implementation.md#phase-4))
- **Phase 5 — Frontend.** A Plugins page and a per-agent Tools/Capabilities
  panel, plus hooks visibility. ([implementation.md](implementation.md#phase-5))
- **Phase 6 — Tests + validation.** ([validation.md](validation.md))

## Definition of done

- Compatibility test pins and exercises every Hermes symbol this plan imports
  (plugin lifecycle, discovery, `VALID_HOOKS`, shell hooks, toolset config,
  `skills_guard`, `osv_check`) and fails readiness cleanly when incompatible.
- The XNOBrain plugin API can list, browse, install (without enabling),
  scan, approve, enable, disable, update, and remove a plugin, all through
  Hermes' own functions, with the response envelope and error model.
- **No code path enables a plugin without a passing scan and a recorded
  approval.** A test proves enable is rejected when the scan is missing,
  stale, dangerous, or unapproved.
- A plugin installed/enabled through XNOBrain is visible to the Hermes plugin
  CLI, and vice versa, without import or synchronization.
- Native toolsets are listable per agent and toggle persistently in the agent's
  profile `config.yaml`; a toggle is visible to a subsequent Hermes run.
- Shell hooks can be listed, created with approval, and deleted; plugin and
  gateway hooks are visible read-only. Hook commands, plugin source, and scan
  internals are never leaked in logs or responses.
- No credentials, tokens, prompts, tool arguments/output, or absolute stored
  paths appear in responses, logs, or errors.
- Frontend Plugins page and per-agent Tools panel work with loading, empty,
  error, and approval states, translated across all 7 shipped locales.
- `make check` and the focused backend/frontend tests pass; `make smoke-api`
  covers the new routes.
