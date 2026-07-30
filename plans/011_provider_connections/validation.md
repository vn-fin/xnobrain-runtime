# 011 — Validation

How completion is proven. An item is complete only when its evidence line is
filled in; flipping a checkbox without evidence is not complete
([`plans/LOCAL_FEATURES_CHECKLIST.md`](../LOCAL_FEATURES_CHECKLIST.md)).

## 1. Phase 0 probe / compatibility test

```
python -m unittest brain4all.tests.test_nine_router_probe -v
```

- With no 9router running: every live test reports `skipped`, the pin test
  passes, exit code 0 (CI-safe).
- With the pinned `9router@0.5.40` running on `:20128`: all probes pass —
  `PUT /api/providers/{id}` accepts partial `{"isActive"}` / `{"priority"}`
  bodies, list fields (`priority`, `email`) confirmed, throwaway connection
  created/patched/deleted, usage and test shapes recorded.
- The observed shapes are written into the test-file header comment and
  [findings.md](findings.md) §6 answers are updated (including the
  round-robin vs priority conclusion that fixes the UI copy — Decision C in
  [approaches.md](approaches.md)).

## 2. Unit tests — adapter

```
python -m unittest brain4all.tests.test_nine_router -v
```

- Existing tests still pass (list/models/auto-combo/usage filtering —
  proves `_quota_list` extraction changed nothing).
- New: `priority`/`email` surfaced; `apiKey`/`data` never surfaced;
  `update_connection` sends the partial PUT and re-ensures the auto combo;
  `update_connection` with no fields raises 400;
  `usage_for_connection` returns all quota windows unfiltered by model.

## 3. Integration tests — routes via ASGI

```
python -m unittest brain4all.tests.test_fastapi -v
```

Using the stateful multi-connection `FakeRouter`
([implementation.md](implementation.md) 5b):

- List returns both accounts sorted by priority; `connected` reflects
  "≥1 active".
- Ownership guard: a codex connection id under `/providers/openai/...`
  returns 404 for PATCH, test, delete, and usage.
- Create on an API-key provider returns 201 with a filtered connection;
  create on an OAuth provider returns 400 `oauth_connect_required`.
- PATCH toggles `active`, reorders `priority`, and rejects an empty body.
- DELETE removes exactly one; provider-level disconnect still removes all.
- `GET /api/brain/v1/providers` is backward compatible (all previous
  fields identical; only `connection_count` added).
- **Response-scan allowlist test**: every new route's `data` payload contains
  only the allowlisted connection keys `{id, provider, auth_type, name,
  email, active, priority, default_model, test_status, last_error}` and the
  planted secret `sk-secret` appears in no response body.

## 4. Nothing stored in Brain4All

- In the integration test, snapshot the `DATA_DIR` tree (recursive file list
  + mtimes) before and after exercising all six new routes; assert it is
  unchanged. Evidence that this feature stores nothing in Brain4All and that
  snapshot rules are correctly out of scope.

## 5. Frontend build

```
npm run build
```

Type-checks the new `ProviderConnection`/`ConnectionUsage` types, hook state,
and component props. Component tests (if present in the suite) per
[implementation.md](implementation.md) 5c.

## 6. Full suite

```
make check
```

Passes from a clean tree. `make smoke-api` still passes (no existing route
changed shape).

## 7. Manual end-to-end (live 9router + real accounts)

Environment: `make run` (or `make backend` + local 9router `0.5.40`), UI on
the Connectors section (Settings → Connectors,
[`src/features/system/SystemView.tsx`](../../src/features/system/SystemView.tsx)).

1. **Connect two Codex accounts via OAuth.** Connect account A (existing
   flow). Card shows `connected · 1 account`. Click "Add account" → popup →
   authorize with account B → paste callback. Card shows
   `connected · 2 accounts`; expanding lists both rows with distinct
   emails. (`GET /api/brain/v1/providers/codex/connections` returns two
   ids.)
2. **Reorder.** Move account B up; reload the page; order persists (B first).
   Verify in 9router's own dashboard that priorities changed.
3. **Deactivate one.** Toggle account A inactive → provider stays connected;
   toggle B inactive too → card flips to disconnected and the model picker
   loses codex models (`/v1/models` gating). Re-activate B.
4. **Per-connection usage.** Expand usage on each row: account-specific quota
   windows (two different subscriptions show different numbers). Chat once
   through Hermes and observe which account's quota moves — record the
   observed rotation behavior and check it matches the shipped
   `connections.accountsHint` copy.
5. **Test.** Per-row Test on both accounts: status dots update; a revoked
   account shows red with the error on hover.
6. **Delete one.** Remove account A → only one row remains, provider still
   connected, account B still serves chats.
7. **Remove all.** Provider-level "Remove all accounts…" shows a confirm
   naming the count; confirming empties the list and disconnects the card.
8. **Add API-key second account.** For openai: add key 1, then "Add account"
   with key 2 → two rows; delete key 1's row → chats still work via key 2.
9. **Leak check.** Browser devtools network tab: no response of any
   `/providers` call contains key material; `docker logs`/backend logs show
   no key fragments after all of the above.

## Acceptance checklist

- [ ] Phase 0 probe passes against live 9router `0.5.40`; skips cleanly when
      down (evidence: probe run output pasted, both modes)
- [ ] Pins agree across `Dockerfile.backend` and `scripts/install-linux.sh`
      (evidence: `test_pins_agree` green)
- [ ] `PUT /api/providers/{id}` body shape confirmed and recorded in
      findings.md §6 (evidence: probe log + findings diff)
- [ ] Round-robin vs priority semantics observed and UI copy matches
      (evidence: manual step 4 note + shipped `accountsHint` string)
- [ ] All connections listed per provider with priority/email/test status
      (evidence: integration test + manual step 1 screenshot)
- [ ] Second account addable for both auth modes (evidence: manual steps 1
      and 8)
- [ ] Per-connection activate/deactivate, reorder, test, delete work and
      provider `connected` = ≥1 active (evidence: integration tests + manual
      steps 2/3/5/6)
- [ ] Provider-level disconnect = explicit remove-all with confirm; existing
      provider routes byte-compatible plus `connection_count` (evidence:
      backward-compat test + manual step 7)
- [ ] Per-connection usage rendered from `/api/usage/{connectionId}`
      (evidence: unit test + manual step 4)
- [ ] no api_key/token material ever appears in Brain4All responses or logs
      (evidence: response-scan test)
- [ ] Nothing stored in Brain4All — `DATA_DIR` unchanged by all new routes
      (evidence: section 4 test)
- [ ] `make check` and `npm run build` pass (evidence: command
      output)
