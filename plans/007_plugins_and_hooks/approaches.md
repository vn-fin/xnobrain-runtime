# 007 — Approaches

Companion to [README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [implementation.md](implementation.md),
[validation.md](validation.md). Three decisions, each with options, then the
chosen path. Safety is the tie-breaker throughout.

## Decision A — How Brain4All reaches Hermes plugin/hook/tool functions

**A1. In-process import of Hermes helpers (chosen).**
`integrations/plugins.py` imports `hermes_cli.plugins_cmd`,
`agent.shell_hooks`, `toolsets`, `tools.registry`, `hermes_cli.tools_config`,
`tools.skills_guard`, `tools.osv_check` and calls them directly.
- Pros: matches precedent (`integrations/kanban.py` → `hermes_cli.kanban_db`);
  no second HTTP hop, no token juggling inside one process; single source of
  truth; Hermes owns the files and config.
- Cons: some symbols are underscore-private and can drift.
- Mitigation: the Phase 0 pin + compatibility test; one remediation message on
  break; upstream a public shim in `hermes_cli` if churn is high (allowed by the
  "make required Hermes changes in the current package" rule).

**A2. HTTP-proxy the native `/api/dashboard/*` and `/api/ops/hooks` routes.**
- Pros: only touches public HTTP surface; immune to internal refactors.
- Cons: a process calling its own `_require_token`-gated routes over localhost;
  couples to the SPA token model; extra serialization; still can't inject the
  scan+approval gate cleanly (the native route would enable directly).
- Rejected: awkward and it does not let us interpose the mandatory gate.

**A3. Manage plugin files/config directly from Brain4All.**
- Rejected outright: forbidden by the constraints (no forking/duplicating Hermes
  internals) and it would desync from `plugins.enabled`/toolset resolution.

## Decision B — Install/enable granularity

**B1. Global install + global enable + per-agent toolset enablement (chosen).**
Honors how Hermes actually works: plugins install to `~/.hermes/plugins/` and
enable in the **root** `config.yaml`; a plugin contributes a **toolset** that
each agent turns on/off in its own profile `config.yaml`.
- Pros: truthful to the runtime; the per-agent control is the same toolset
  toggle used for native tools, so one panel covers both; no invented state.
- Cons: "install once, then per-agent" needs clear UI wording.

**B2. Per-agent plugin installs.** Rejected: Hermes has no per-profile plugin
store; simulating it would fork the runtime and duplicate downloads.

**B3. Global-only, no per-agent notion.** Rejected: the brief explicitly wants
per-agent enablement, and the toolset mechanism already provides it cleanly.

## Decision C — How to gate hub / untrusted installs

**C1. Install-without-enable, then mandatory scan + explicit approval before
enable, built from Hermes' own scanners (chosen).**
- Flow: `dashboard_install_plugin(enable=False)` → `skills_guard.scan_skill_cached`
  (+ `osv_check` on declared deps) → `should_allow_install` verdict → record with
  content hash → explicit approval → enable re-verifies hash + approval.
- Pros: closes the real safety gap (Hermes' dashboard path has no scan); reuses
  vetted Hermes primitives rather than a home-grown scanner; enforced in the
  service, so the UI cannot bypass it; a stale/updated plugin must be re-scanned.
- Cons: two user steps (approve, then enable) — deliberate friction for code
  execution.

**C2. Scan at install, auto-enable if `allow`.** Rejected: removes the human
approval step for `caution`/`ask` verdicts and for anything that requests
`allow_tool_override`; the brief requires *explicit* approval.

**C3. Trust the token gate only (mirror Hermes dashboard).** Rejected: that is
exactly the gap — arbitrary code enabled with no malware check or consent.

## Chosen approach (summary)

- **A1 + B1 + C1.** Ride Hermes in-process; install/enable globally with
  per-agent enablement via toolsets; and interpose a **mandatory
  scan-and-approve-before-enable** gate constructed from `skills_guard` +
  `osv_check`, enforced in `services/plugins.py` and recorded under `DATA_DIR`.
- **Native tools** reuse the skills denylist pattern
  (`agent.disabled_toolsets`) and the trusted `SAFE_TOOLSETS` vocabulary,
  written through the existing config managers so `normalize_nine_router_config`
  keeps the provider pinned.
- **Hooks** expose approval-gated CRUD for shell hooks via
  `agent.shell_hooks` (reusing its native `_record_approval`/`revoke`) and
  read-only visibility of plugin (`VALID_HOOKS`) and gateway hooks.
- **Hub** = installed catalog + status + git-URL install (no remote
  marketplace; see [findings.md](findings.md#open-questions)).

### Why this is safest

The single most dangerous operation — running third-party Python in-process — is
reachable only after a passing scan whose content hash still matches the disk
and an explicit recorded approval. There is no service path that enables without
those checks, updates force a fresh gate, and `allow_tool_override` is never
granted automatically. The scanners and approval records are Hermes' own, so
Brain4All adds enforcement without inventing security-critical code.

### Rejected extras

- Runtime sandboxing of plugins — no Hermes primitive exists; out of scope.
- A curated remote registry with signatures — a future, separate plan.
