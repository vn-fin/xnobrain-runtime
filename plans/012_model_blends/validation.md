# 012 — Validation

How completion is proven. An item is complete only with recorded evidence
(command + observed output), per
[`plans/LOCAL_FEATURES_CHECKLIST.md`](../LOCAL_FEATURES_CHECKLIST.md).
Cross-links: [README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [implementation.md](implementation.md).

## 1. Phase-0 probe / compatibility

Run with the pinned 9router 0.5.40 (`Dockerfile.backend` line 18) running
locally:

```
python -m unittest brain4all.tests.test_blends_probe -v
```

- All 12 probes from [findings.md](findings.md) §10 pass (combos CRUD shapes,
  name charset, duplicate handling, partial PUT, delete, `owned_by:"combo"`
  in `/v1/models`, settings GET/PATCH shape, whole-map `comboStrategies`
  replacement, fusion entry, global sticky limit, cleanup).
- With 9router stopped, the same command reports the suite **skipped** (not
  errored).
- Any divergence from findings.md is recorded there before dependent code is
  written; if the pin is ever bumped, this suite is the gate.

## 2. Unit tests — adapter and service (fake router)

```
python -m unittest brain4all.tests.test_blends -v
```

Cases per [implementation.md](implementation.md) Phase 5.1. Must-pass
highlights:

- `set_combo_strategy` issues `GET /api/settings` then
  `PATCH /api/settings` whose body contains the **entire merged**
  `comboStrategies` map (asserted on the recorded request), never a lone
  entry.
- `combo_settings()` result contains only
  `combo_strategy`/`combo_strategies`/`combo_sticky_limit` — no other
  settings keys, proving the whitelist (no credentials/instance config
  exposure).
- `list_models()` output: `auto` first, blend entries
  `{"id": <name>, "provider": "blend"}` next, provider models after; and the
  auto-combo selection loop never picks a blend.
- Create with default `fallback` strategy performs **no** settings write.
- Compensation: settings-write failure after create deletes the just-created
  combo.
- Guards: bad charset → 400 `invalid_blend_name`; collision with an existing
  blend or a real model id → 409 `blend_name_conflict`; fusion with 1 model
  or without judge → 400; `judge_model` without fusion → 400;
  `sticky_limit` without round-robin → 400.

## 3. `auto` immutability

Same suite, dedicated tests:

- `update_blend(<auto combo id>, {...})` and
  `delete_blend(<auto combo id>)` raise `ServiceError` with `status == 403`,
  `code == "blend_reserved"`, and the fake router recorded **zero** `PUT`/
  `DELETE` `/api/combos/*` requests for it.
- `create_blend({"name": "AUTO", ...})` (case-insensitive) → 403.
- After the rejections, `ensure_auto_combo()` still reconciles `auto`
  normally (unchanged behavior of the refactored `_ensure_auto_combo`,
  covered by the existing
  `brain4all/tests/test_nine_router.py::test_models_are_filtered_and_auto_combo_is_created`,
  which must keep passing unmodified in intent).

## 4. Integration tests — routes via ASGI

Extended `brain4all/tests/test_fastapi.py`:

```
python -m unittest brain4all.tests.test_fastapi -v
```

- Envelope shape and status for all five routes (`200/201` happy paths;
  `400/403/404/409` guard paths; `503` with
  `error.code == "nine_router_unavailable"` when the fake raises).
- `GET /agent-gateway/v1/blends/available-models` contains no
  `provider == "blend"` entry and no `auto`.
- Pass-through: `PATCH /agent-gateway/v1/agents-configs/{agent_id}` with
  `{"model": "<blend name>"}` returns success and the temp profile's
  `config.yaml` contains `model: {default: <blend name>}` (and a config
  snapshot was created — existing snapshot path).
- Route order: `available-models` resolves to `blends_available_models`, not
  to `blends_patch`/`blends_delete` with `blend_id == "available-models"`.

## 5. No sensitive data exposed

- Grep-level review + test assertion: no blends response or log line contains
  the raw 9router settings payload, API keys, tokens, or `x-9r-cli-token`
  material. The unit test in §2 (whitelist) is the executable proof.
- `GET /agent-gateway/v1/blends` responses contain only the DTO fields listed
  in [architecture.md](architecture.md) § Blend DTO.

## 6. Suite and smoke

```
make test
make check
cd src && npm run build
make smoke-api   # with the stack running
```

All green; record outputs. Frontend build proves the new
`blends.ts`/`useBlends.ts`/`BlendsSection.tsx`/`BlendEditorDialog.tsx`/
`ChatArea.tsx` changes type-check.

## 7. Manual end-to-end (real stack: `make run`)

Prereq: at least two provider models connected (e.g. one `cc/...` and one
`cx/...`).

1. **Create.** Settings → Model Blends → New blend: name `duo`, pick two real
   models in order, strategy `fallback`. Evidence: 201 in the network tab;
   `curl -s http://127.0.0.1:20128/api/combos` (with CLI token) shows the
   combo; the blend row appears with a `fallback` chip; `duo` appears in the
   composer model picker under "Blends".
2. **Use in an agent.** Open an agent, pick `duo` in the composer model
   picker. Evidence: `profiles/<agent-id>/config.yaml` shows
   `model.default: duo`.
3. **Chat + failover.** Send a chat. Evidence: a reply arrives and the
   9router log shows `Combo "duo" with 2 models (strategy: fallback,
   sticky: ...)`. To see failover, temporarily disconnect/invalidate the
   first model's provider and chat again: the reply still arrives and the log
   shows the failover to the second model.
4. **Switch to round-robin.** Edit `duo` → strategy `round-robin`, sticky
   limit 1. Send two chats. Evidence: log lines show
   `strategy: round-robin` and alternating upstream models.
5. **Fusion smoke.** Edit `duo` → strategy `fusion`, judge = one real model.
   Confirm the editor shows the cost/tools warning. Send one short chat.
   Evidence: log line `Combo "duo" with 2 models (strategy: fusion)`; a
   single combined reply.
6. **Analytics tie-in.** Open the Analytics dashboard (plan 009): the
   by-model table lists `duo` as a model with the sessions just run.
7. **Auto protection.** In the Blends panel, `Auto` shows a System badge with
   edit/delete disabled; `curl -X DELETE .../agent-gateway/v1/blends/<auto id>`
   returns the 403 envelope.
8. **Down behavior.** Stop 9router; the Blends panel shows the unavailable
   banner and `GET /agent-gateway/v1/blends` returns the 503 envelope; start
   it again and refresh recovers.
9. **Delete.** Delete `duo` (confirm dialog). Evidence: gone from
   `GET /api/combos`, from the picker, and its `comboStrategies` entry gone
   from `GET /api/settings`. The agent whose model was `duo` now fails
   gracefully at chat time with the provider error surfaced (expected —
   record the observed message; no orphan-repair is in scope).

## Acceptance checklist

- [ ] Phase-0 probe suite passes against pinned 9router 0.5.40 and skips
      cleanly when 9router is down (evidence: test output for both runs).
- [ ] Public adapter combo CRUD + settings accessors exist and
      `_ensure_auto_combo` uses them with unchanged behavior (evidence:
      existing `test_nine_router.py` auto-combo test still passes).
- [ ] `list_models()` surfaces custom combos as
      `{id, provider: "blend", name}` while per-provider lists and the auto
      combo remain unpolluted (evidence: unit test names + output).
- [ ] Blends CRUD works end-to-end through
      `/agent-gateway/v1/blends*` with the standard envelope (evidence: ASGI
      integration test output + manual step 1).
- [ ] Strategy round-trips: create/read/update across
      `fallback` → `round-robin` → `fusion` with judge, written via 9router
      settings `comboStrategies` whole-map merge (evidence: unit test
      asserting the PATCH body + manual steps 4–5 log lines).
- [ ] "auto" cannot be modified or deleted via the blends API (evidence:
      409/403 test — the suite in §3 asserting `status == 403`,
      `code == "blend_reserved"`, zero upstream mutation requests; plus
      manual step 7).
- [ ] Blend names collide with nothing: reserved `auto`, existing blends, and
      real model ids are all rejected (evidence: guard tests in §2).
- [ ] An agent configured with a blend name chats through the blend
      (evidence: manual steps 2–3 — `config.yaml` content + 9router
      `Combo "duo" ...` log line).
- [ ] Fusion is exposed with the cost/tools warning copy and works (evidence:
      manual step 5 screenshot/log).
- [ ] Analytics by-model shows the blend name for sessions run under it, with
      zero analytics code change (evidence: manual step 6).
- [ ] 9router-down returns the 503 envelope and the UI shows the unavailable
      state, with no fabricated/stale blend data (evidence: manual step 8 +
      integration 503 test).
- [ ] No credentials or raw 9router settings payload in any response or log
      (evidence: whitelist unit test in §5).
- [ ] `make test`, `make check`, `cd src && npm run build`, and
      `make smoke-api` all pass (evidence: command outputs).
- [ ] `docs/api.md` and `docs/architecture.md` updated (evidence: diff).
