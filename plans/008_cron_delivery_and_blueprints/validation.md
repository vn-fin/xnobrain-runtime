# 008 — Validation

How to prove the feature works and stays inside the constraints. Nothing here
uses mock or demo data — every check exercises the real pinned Hermes catalog and
real Kanban SQLite in a temporary `HERMES_HOME`.

Cross-links: [README.md](README.md) · [findings.md](findings.md) ·
[architecture.md](architecture.md) · [approaches.md](approaches.md) ·
[implementation.md](implementation.md).

## 1. Compatibility test (Phase 0 gate)

`brain4all/tests/test_cron_delivery.py::CronDeliveryCompatibilityTests`
(skipped unless Hermes importable). Assert the imports and shapes listed in
[implementation.md](implementation.md#phase-0):

- `cron.blueprint_catalog` exports and `CATALOG` non-empty;
- `blueprint_catalog_entry` keys present;
- `fill_blueprint` returns `prompt/schedule/name/deliver`;
- `cron.scheduler.cron_delivery_targets` importable, entries well-shaped;
- `gateway.delivery.{DeliveryTarget,DeliveryRouter}` and
  `gateway.config.Platform.{EMAIL,LOCAL}` present.

Run: `make test` (or a focused `python -m pytest
brain4all/tests/test_cron_delivery.py -k Compatibility`).

Fail behavior: a broken import/shape must fail startup readiness with one concise
remediation message — verify by running with an intentionally wrong pin locally.

## 2. Unit tests

- **Model validation:** `CronDeliveryTargetCreate` rejects an unknown
  `target_type` (422); accepts each of `channel|email|kanban|file`.
- **Adapter blueprint mapping:** `cron_delivery.list_blueprints()` returns
  snake_case `schedule_human`/`app_url`; `fill("<key>", <bad values>)` raises the
  typed invalid error mapped to 422; unknown key → not-found mapped to 404.
- **Delivery key idempotency:** two calls of `on_occurrence_complete` for the
  same occurrence deliver each target once.
- **File-target path guard:** a `file` destination with `../` or an absolute
  path is rejected before persistence.
- **Degraded channel:** `deliver_channel` with no gateway config returns a
  `degraded` result and does not raise.

## 3. Integration tests (temp `HERMES_HOME`, real Hermes)

Same harness as `brain4all/tests/test_kanban.py` (real `Brain4AllApplication`,
`ASGITransport`, real Kanban SQLite). Cover:

- `GET /api/brain/v1/cron/blueprints` returns real catalog entries with
  fields and human schedule.
- `POST /api/brain/v1/cron/blueprints/instantiate` with a valid agent +
  values creates exactly one `scheduled` template on the `default` board,
  visible via `GET /cron/jobs`; invalid slot values → 422; unknown blueprint →
  404; missing/unknown agent → same error as `create_cron`.
- `GET /cron/delivery-targets` lists `kanban` and `file` always; channel/email
  only when the gateway is configured.
- `POST /cron/jobs/{id}/delivery-targets` for `email`, `kanban` (valid board),
  `file` (valid workspace path) persists on the template and returns
  `available=true`; a `channel` target with no connected platform persists with
  `available=false` + `degraded_reason`; a `kanban` target with an unknown board
  → 404; a `file` target with traversal → 400/422.
- `GET /cron/jobs/{id}/delivery-targets` reflects adds; `DELETE .../{target_id}`
  removes.
- `POST /cron/jobs/{id}/trigger` creates a run; `GET /cron/jobs/{id}/runs`
  returns the run with per-target delivery status.
- **Post-run delivery**: after an occurrence completes, a `kanban` target
  creates a card on the target board (assert via the Kanban API); a `file`
  target writes the expected workspace file; an `email` target records a
  delivered/failed result (mock only the SMTP/adapter transport at the boundary,
  never the catalog or Kanban DB); re-running does not double-deliver.
- **Leakage**: assert no response body or captured log contains prompts, tool
  args/output, credentials, SMTP secrets, home-target env values, or absolute
  stored paths (reuse the redaction assertions in `test_kanban.py`).
- **Restart**: attached targets and delivery records survive an app restart
  (re-open the same `HERMES_HOME`) without duplication.

## 4. Repo checks

- `make check` — full backend + repo checks pass.
- `make test` — focused backend suite including the new file.
- `npm run build` and `npm test -- crons` — frontend
  type/build + component/hook tests.
- `make smoke-api` — the API smoke path still passes with the new routes
  registered (no route-assembly or import regressions).

## 5. Manual end-to-end journey

Against a real stack (`make run`), on a profile with an agent:

1. Open Settings > Automations; confirm the **blueprint gallery** shows real
   Hermes blueprints grouped by category with human schedules.
2. **Instantiate a blueprint** (e.g. a daily brief): pick an agent, set the time,
   submit. Confirm a new scheduled template appears on the default Kanban board
   and in `GET /cron/jobs`.
3. **Attach a delivery target**: add an `email` target (configured home address)
   or a `kanban` target (pick a board) via the "Deliver to" selector. Confirm it
   shows as available. Add a `channel` target with no connected platform; confirm
   it shows "connect a channel first" (degraded) and is still saved.
4. **Trigger** the job (Run now). Wait for the occurrence to reach Done.
5. **Confirm delivery**: for the `kanban` target, a summary card appears on the
   target board; for `email`, the message is delivered (or a delivered result is
   recorded); the `channel` target records a degraded reason (not an error).
6. Open the **runs panel**; confirm a run record exists with per-target delivery
   status. Trigger again and confirm no duplicate delivery for an
   already-delivered target.
7. Restart the app; confirm targets, the template, and run/delivery history
   persist without duplication.

## 6. Acceptance checklist

Mark `[x]` only with the evidence noted beside each item.

- [ ] Hermes pinned to an immutable commit exporting the required modules —
  evidence: `Dockerfile.backend` diff + `docs/development.md` version note.
- [ ] Compatibility test passes and fails readiness on a bad pin — evidence:
  test output + a local wrong-pin run showing the remediation message.
- [ ] Blueprint list + instantiate work against the real catalog — evidence:
  integration test + manual step 1–2 screenshots.
- [ ] `target_type` validation closed to `channel|email|kanban|file`; unknown
  rejected — evidence: unit + integration 422 case.
- [ ] `email`, `kanban`, `file` targets deliver without Plan 005 — evidence:
  integration post-run delivery tests + manual step 5.
- [ ] `channel` target persists and degrades cleanly pre-Plan-005 (no error, no
  silent drop) — evidence: integration degraded-channel test + manual step 3/5.
- [ ] Attach/remove target persists on the Kanban template in the same board
  SQLite (no new DB/file store) — evidence: integration test + inspection of
  `kanban.db`.
- [ ] Trigger produces a run record; runs route returns it with delivery status
  — evidence: integration test + manual step 6.
- [ ] Delivery is at-most-once across retries/restart (deterministic key) —
  evidence: re-trigger and restart tests show no duplicate.
- [ ] No credentials/prompts/tool output/absolute paths in responses or logs —
  evidence: redaction assertions.
- [ ] `file` target path guarded beneath the profile workspace root — evidence:
  traversal-rejection test.
- [ ] Only one scheduler runs (Kanban dispatcher); no native `run_job`/Chronos
  loop started — evidence: code review + no second-scheduler thread in the
  process.
- [ ] New routes added only in `routes/setup.py` under the `Cron` tag —
  evidence: setup.py diff.
- [ ] `make check`, `make test`, `make smoke-api`, and `npm run build` pass —
  evidence: command output.
- [ ] Docs updated (`docs/api.md`, `docs/architecture.md`, `docs/development.md`)
  with the new routes, delivery targets, blueprints, and the Plan 005 dependency
  for channels — evidence: docs diff.
