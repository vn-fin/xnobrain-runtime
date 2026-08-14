# 007 — Validation

Verification for [README.md](README.md). Cross-links:
[findings.md](findings.md), [architecture.md](architecture.md),
[approaches.md](approaches.md), [implementation.md](implementation.md).
"Verified" means the evidence exists, not that the code looks plausible.

## 1. Compatibility test (gate)

`xnobrain/tests/test_plugins_compat.py` (real pinned Hermes, temp `HERMES_HOME`,
committed local fixtures, no network):

- All Phase 0 symbols import with the expected signatures; `VALID_HOOKS`
  contains `pre_llm_call`, `pre_tool_call`, `transform_llm_output`.
- Local plugin lifecycle: install-from-local → scan(`safe`) →
  **enable blocked before approval** → approve → enable → present in
  `_get_enabled_set()` → disable → remove.
- `dangerous_plugin` fixture: scan verdict `dangerous`; enable rejected with
  `plugin_unsafe`; approval rejected with `plugin_unsafe`.
- Toolset round-trip: `set_agent_toolset(agent, "browser", False)` then re-read
  effective toolsets shows `browser` disabled for that profile.
- Shell-hook round-trip: create(`approve=True`) → `allowlist_entry_for` present
  → list → delete → revoked.

Failure here fails readiness with one remediation message (Phase 0 step 3).

## 2. Unit tests — the scan+approval gate (most important)

`xnobrain/tests/test_plugins_service.py`, with the `PluginManager` faked so no
real clone happens but the **gate logic runs for real**:

- `enable` raises `plugin_scan_required` (409) when no record exists.
- `enable` raises `plugin_unsafe` (409) when `verdict == "dangerous"`.
- `enable` raises `plugin_scan_stale` (409) when the on-disk `content_hash`
  differs from the record.
- `enable` raises `plugin_approval_required` (409) when `approved` is False.
- `enable` reaches `PluginManager.enable` **only** when scan is fresh,
  non-dangerous, and approved (assert the fake was called exactly once, and
  never in any of the rejection cases).
- `approve` requires a matching `content_sha256`; a mismatched hash is rejected.
- `update` deletes the approval record → a subsequent `enable` re-requires the
  full gate.
- `install` never calls the manager with `enable=True` (assert the kwarg).
- Native tools: `set_agent_toolset` rejects a toolset outside
  `SAFE_TOOLSETS`; `normalize_nine_router_config` still ran (provider unchanged).
- Redaction: scan findings and hook lists carry no absolute paths, no matched
  source snippets, no full command strings.

## 3. Handler tests

`xnobrain/tests/test_fastapi.py` (extend) — envelope + status + error mapping
for every new route: success shapes, `PluginAPIError`/`ServiceError` → correct
HTTP code, validation 4xx for bad bodies, 404 for unknown plugin/agent,
409 for each gate rejection. Assert the response envelope
(`{success, data, message, status_code}`) and that error responses expose only
`{code}` + message, never internals.

## 4. Integration lifecycle test

`xnobrain/tests/test_plugins_integration.py` (real Hermes, ASGI `AsyncClient`
like `test_kanban.py`): drive the full HTTP flow —
`POST /plugins/install` → `POST /plugins/{name}/scan` (or read the install
response scan) → `POST /plugins/{name}/enable` returns 409 →
`POST /plugins/{name}/approve` → `POST /plugins/{name}/enable` 200 →
`GET /plugins` shows enabled → cross-check the Hermes CLI/`_get_enabled_set`
sees it → `PATCH /agents-tools/{agent}/{toolset}` toggles a native tool →
`POST /hooks` (approve) → `GET /hooks` shows it → `DELETE /hooks`.
Also: a plugin enabled via the Hermes CLI appears in `GET /plugins` without sync.

## 5. Frontend tests

`npm test` — clone the skills test patterns:
- `PluginsView` renders catalog, install panel, and disables the enable control
  for a `dangerous` verdict; the approval dialog gates enable.
- `usePlugins` optimistic toggle reconciles on error.
- RightPanel `tools` tab renders toolset toggles with ready/not-ready badges.
- `useRouter` parses/produces `/plugins`.
- `npm run build` passes (types).
- All 7 locales contain the new keys (a key-parity test if one exists).

## 6. Repository checks

- `make check` — full suite green.
- `make test` — backend focused suites (compat, service, handler, integration).
- `make smoke-api` — extend to hit `GET /plugins`, `GET /agents-tools/{agent}`,
  and `GET /hooks` and assert 200 + envelope. **No mock/demo plugin records** in
  the running app; the smoke path must exercise a real fixture, not a synthetic
  injection.
- `make run` + container health check unaffected; chat streaming, stop, and
  9router behavior unregressed.

## 7. Manual verification

1. `make run`. Open the Plugins page.
2. Install a real plugin from a git URL. Confirm it lands **disabled** and a
   scan report appears.
3. Try to enable without approving — the UI prevents it and the API returns 409.
4. Approve, then enable. Confirm it becomes active and the Hermes CLI
   (`hermes plugins list`) shows it enabled.
5. Install a plugin whose code trips a threat pattern — confirm `dangerous`
   blocks enable with no override.
6. Open an agent's Tools panel; toggle `browser` and `image_gen`; confirm a
   not-ready tool shows the reason; confirm the toggle persists in the profile
   `config.yaml` and affects the next run.
7. Create a shell hook with approval; confirm it lists; delete it.
8. Confirm no logs/responses contain credentials, tokens, prompts, tool
   arguments/output, full hook commands, or absolute plugin paths.

## 8. Acceptance checklist

- [ ] Hermes pinned to an immutable revision; pin + required symbols recorded in
      `docs/development.md`. Evidence: diff of `Dockerfile.backend`, doc.
- [ ] `test_plugins_compat.py` passes against the pinned Hermes and fails
      readiness cleanly when incompatible. Evidence: test run + forced-failure
      message.
- [ ] `GET /plugins`, `/plugins/hub`, `POST /plugins/rescan` return real
      installed-plugin data (no mock records). Evidence: integration test +
      `make smoke-api`.
- [ ] `POST /plugins/install` installs **disabled** and returns a scan result.
      Evidence: integration test asserts `enabled == False` + scan present.
- [ ] **SECURITY:** no code path enables a plugin without a passing, fresh,
      approved scan; `dangerous` cannot be overridden; `update` forces re-scan.
      Evidence: `test_plugins_service.py` gate cases (§2) + integration 409-then-200.
- [ ] Enable/disable/update/remove/visibility work through Hermes' own
      functions; a XNOBrain-enabled plugin is visible to the Hermes CLI and
      vice versa. Evidence: integration test.
- [ ] Native toolsets list per agent with `enabled/available/ready`; a toggle
      persists in the profile `config.yaml` and is honored by a later run;
      provider stays 9router. Evidence: integration + service test.
- [ ] Shell hooks list/create(approval)/delete; plugin + gateway hooks visible
      read-only. Evidence: integration test.
- [ ] No credentials, tokens, prompts, tool args/output, full hook commands, or
      absolute paths in any response, log, or error. Evidence: redaction test §2.
- [ ] Frontend Plugins page + per-agent Tools panel handle loading/empty/error/
      approval states; strings translated in all 7 locales. Evidence: component
      tests + `npm run build` + locale parity.
- [ ] `make check`, `make test`, `make smoke-api` pass; `make run` healthy with
      no regression to chat streaming, stop, approvals, or 9router. Evidence:
      CI/logs.

## 9. Explicit security acceptance item

- [ ] **A plugin cannot be enabled through any XNOBrain path without (a) a
      malware/OSV scan that did not return a `dangerous` verdict, (b) a content
      hash that still matches the scanned bytes, and (c) an explicit recorded
      user approval — enforced in `services/plugins.py::enable`, proven by a
      test asserting `PluginManager.enable` is never called in the scan-missing,
      scan-stale, dangerous, and unapproved cases, and that Hermes'
      `dashboard_set_agent_plugin_enabled` is therefore never reached.**
      `allow_tool_override` is never granted automatically. Evidence:
      `test_plugins_service.py` + `test_plugins_integration.py`.
