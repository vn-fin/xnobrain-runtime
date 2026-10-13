# Publish an installed agent skill

The Community publication form selects one personal agent and one installed custom
skill, including disabled custom skills. It loads the selected agent's inventory through the
existing `GET /xnobrain/api/runtime/v1/agents-skills/{agent}` facade. It never
infers installed files from the global library or enabled-skill flags.

Skill inventories include an additive boolean `bundled`, derived from the installed
engine's bundled catalog and the profile's `.bundled_manifest` seed history.
Locally edited defaults remain bundled. Custom skills installed in the common/root
profile are not defaults solely because they are shared. The publication picker
only offers entries with `bundled: false`; older responses without the field are
not offered until Runtime is updated. Other skill-management lists remain complete.
This is a publication-picker filter, not a change to export authorization.

## Export validation errors

Runtime export failures retain safe machine-readable reasons through Control's
draft endpoint. Runtime's HTTP 422 maps to HTTP 400 with a localized UI message:
`skill_export_text_only`, `skill_export_unreadable`, `skill_export_unsafe_file`,
`skill_export_ambiguous_tree`, `skill_export_missing_manifest`, or
`skill_export_credentials_detected`. The last maps Runtime's existing
`marketplace_export_credentials_detected` code. Unknown or malformed Runtime
validation responses map to `skill_export_invalid_source`. Raw Runtime messages,
file contents, and credentials are never forwarded. Files are not silently removed
or modified to bypass validation. The UI preserves the selection for an explicit retry.

## Contract

The existing authenticated Control draft endpoint accepts `source_type=runtime_export`
and a JSON-encoded string in `runtime_export_reference`:

```json
{"version":1,"agent_id":"writer","skill_path":"office/writing"}
```

`skill_path` is exactly the profile-relative `path` returned by the skill inventory,
relative to `skills/`. It is neither an absolute filename nor a workspace selector.
Unknown reference fields, versions and traversal are rejected. Control resolves
the caller's personal workspace and relays `POST /xnobrain/api/runtime/v1/skill-community/exports`
with `{"reference":"<JSON string above>"}` and the verified principal signature.
The request is capped at 4096 bytes, the reference at 2048 characters.

Runtime requires a valid Control HMAC principal and rejects organization context.
Ordinary profiles are scoped by the server-resolved personal workspace. Imported
profiles must additionally match their `.community-profile-owner.json` recipient.
Native, root `big-brother` and legacy profiles use the existing agent resolver.
Reserved coordinator skills are absent from the exportable inventory.

HTTP 200 returns the normal envelope with `data.files`, `data.excluded`, and
`data.provenance={source_type:"runtime_export",source_digest:"sha256:..."}`.
Files contain relative `path`, UTF-8 `content`, `encoding:"utf-8"`, byte `size`,
and `digest`. The source digest hashes the sorted path/size/digest manifest;
Control independently validates files and computes the publication digest.
Responses are `Cache-Control: private, no-store`.

## File and publication boundaries

- Only the selected skill tree is copied. It must contain `SKILL.md` and cannot
  contain another nested skill. References, scripts, assets and license text
  remain files; nothing is executed or enabled.
- Hidden entries, credentials, sessions, conversations, memory, caches, runtime
  state and personal profile configuration are excluded. Detected credential-like
  content and known profile credentials copied into skill text are rejected.
- Symlinks, hardlinks and special files are rejected. Directory traversal uses
  `openat`-style directory descriptors with `O_NOFOLLOW`. File names are bounded,
  relative, unique ignoring case, and at most 12 segments deep.
- Limits: 200 files, 1 MiB per file, 10 MiB total, 1000 directory entries visited.
  Binary content is rejected because the existing Community draft format accepts
  UTF-8 source only. Limits return 413; malformed/unsafe files return 422; missing
  inventory entries return 404; invalid ownership returns 403. Control maps 422
  to invalid input, 413 to limit exceeded, and unsupported/unavailable Runtime
  responses to its existing capability-unavailable error.
- Export creates no files or publication in Runtime. Control creates a private
  draft; the UI shows included file contents and excluded counts, then retains
  the existing validation, review, digest consent and explicit publish flow.
- Selecting another agent clears its skill selection. Reads are deduplicated
  under StrictMode and cancelled on replacement/sign-out. Draft writes are not
  automatically replayed. Candidate failure preserves the created private draft.

## Source verification

Focused Runtime suites: `test_skill_exports`, `test_skill_community`,
`test_marketplace`, `test_api_routes`. UI: `SkillCommunityView.test.tsx`,
`api/skills.test.ts`, `i18n.test.ts`; browser fixture:
`scripts/check-skill-publication-browser.mjs`. Control: package
`internal/services/skillcommunity`, including export adapter and error mapping.
Browser acceptance uses synthetic HTTP and never creates a real publication.
Managed deployment and real-account publication require separate verification.

Observed locally on 2026-10-12 (Docker source containers):

- `RUNTIME_CUSTOM_PAGE_STORAGE_MOUNT= python -m unittest xnobrain.tests.test_skill_exports xnobrain.tests.test_skill_community xnobrain.tests.test_marketplace xnobrain.tests.test_api_routes -q`: 73 tests passed.
- `go test ./internal/services/skillcommunity -count=1` and
  `go vet ./internal/services/skillcommunity`: passed.
- `npx tsc --noEmit` and `npx vitest run src/components/SkillCommunityView.test.tsx src/api/skills.test.ts src/i18n.test.ts --reporter=dot`: passed, 16 tests.
- Chromium fixture: selected agent/skill reached the real UI API serializer,
  private draft and file preview rendered, zero publish requests, no page errors,
  and no document overflow at 390px. HTTP was synthetic, not a managed account.
- Ruff check/format and focused diff whitespace checks passed.
- Full Runtime `make test PYTHON_BIN=python` did **not** pass: it encountered
  `custom pages are disabled` and stalled in
  `test_custom_page_locks.RunLifecycleTests.test_budget_wait_then_removed_profile_cannot_recreate_run_files`.
  Interrupting after approximately five minutes exposed its fixture callback's
  `TypeError: budget() got an unexpected keyword argument 'context_id'`.
  That unrelated suite was stopped; full-suite completion is not claimed.
