# 012 — Approaches

Options considered for each decision, then the chosen path. Cross-links:
[README.md](README.md), [findings.md](findings.md),
[architecture.md](architecture.md), [implementation.md](implementation.md).

## Decision A — Product name for the feature

The upstream noun is "combo", which we do not want in the product: it is
jargon, collides with UI "combo box", and says nothing about behavior. The
name must cover all three strategies (ordered failover, rotation, fusion).
Options:

### A1. "Model Blend" / "Blend" (chosen)

- Pros: friendly and self-explanatory ("a blend of models"); reads naturally
  in every surface ("New blend", "Blends" picker group, "strategy of this
  blend"); fits **fusion** especially well while still being honest for
  fallback/round-robin (a blend behaves as one model however it mixes);
  short; not overloaded elsewhere in the app.
- Cons: slightly soft/marketing-flavored; a pedant could read "blend" as
  implying all models run at once (only true for fusion) — mitigated by the
  strategy chip being visible everywhere a blend is listed.

### A2. "Virtual Model"

- Pros: technically the most precise (it *is* a virtual model id).
- Cons: dry; describes the mechanism, not the value; awkward verb forms
  ("create a virtual model" reads like an ML task); risks confusion with
  model aliases.

### A3. "Routing Profile"

- Pros: enterprise-familiar.
- Cons: "profile" is already a loaded word in this codebase (Hermes
  *profiles* are agents — `/api/brain/v1/profiles` exists); guaranteed
  confusion. Rejected outright for the collision alone.

### A4. "Model Group"

- Pros: neutral, obvious.
- Cons: bland; undersells that the group is *usable as a model* and has
  behavior; "group" suggests mere organization/foldering.

**Chosen: A1 — "Model Blend", short form "Blend".** Consistency rules
(applied throughout [architecture.md](architecture.md)):

- API paths `/api/brain/v1/blends*`, tag `Blends`, models
  `BlendCreate`/`BlendPatch`, service `BlendService`, ops `blends_*`,
  frontend `blends.ts`/`useBlends.ts`/`BlendsSection.tsx`, picker group
  "Blends", `provider: "blend"` in model lists.
- **Upstream ids/kinds/keys unchanged**: 9router calls remain `/api/combos*`,
  settings keys remain `comboStrategy`/`comboStrategies`/
  `comboStickyRoundRobinLimit`, adapter methods say `combo` (they adapt the
  combo API); the service is where the word flips to blend.
- The reserved `auto` combo is presented as a built-in blend labeled
  **"Auto"**, `system: true`, and stays managed by `ensure_auto_combo()`.

## Decision B — Where strategy lives in the Brain4All API

Upstream splits the data: model list in the combo row, strategy in
`settings.comboStrategies[<name>]` (findings.md §4, §6).

### B1. Embed strategy in the blend DTO, hydrated from settings (chosen)

`GET /blends` returns each blend with its resolved `strategy`, `judge_model`,
`sticky_limit`; `POST`/`PATCH` accept the same fields and the service fans the
write to `/api/combos*` and `/api/settings` as needed.

- Pros: one concept for the user ("a blend has a strategy") matching how
  9router *resolves* it per combo name; one round trip for the UI; the
  upstream storage split stays an implementation detail behind the service;
  create is atomic-feeling (with the compensating delete on strategy-write
  failure, architecture.md service rule 6).
- Cons: a PATCH can partially fail across two upstream writes (documented
  ordering + compensation); the DTO carries a `sticky_limit_scope: "global"`
  wrinkle because the sticky limit is genuinely instance-global.

### B2. Separate strategy endpoint (`PUT /blends/{id}/strategy`)

- Pros: maps 1:1 to the storage split; each endpoint does one upstream write.
- Cons: two calls for the common "create blend with strategy" flow; leaks the
  upstream storage layout into the public contract of a 0.x dependency —
  exactly the coupling the adapter exists to hide; more routes for no user
  value.

**Chosen: B1.** The storage split is upstream trivia; the product concept is
"a blend has a strategy".

## Decision C — Protecting the reserved `auto` combo

`ensure_auto_combo()` continuously reconciles `auto` (recreates/updates it on
provider changes — findings.md §7). Options:

### C1. Read-only through the blends surface, visible as a system blend (chosen)

List `auto` in `GET /blends` decorated `system: true, read_only: true`,
labeled "Auto"; reject create/update/delete touching it with 403
`blend_reserved`.

- Pros: users see the whole truth (Auto *is* a blend and appears in pickers,
  so hiding it from the management panel would be inconsistent); no fight
  between a user edit and the reconciler, which would silently overwrite any
  manual change on the next provider event — a guaranteed
  confusing-data-loss bug; keeps this plan's non-goal ("no auto-blend
  redesign") crisp.
- Cons: users cannot tune Auto's order/strategy — by design; the escape hatch
  is creating their own blend.

### C2. Allow editing `auto`

- Pros: maximum flexibility.
- Cons: requires teaching `ensure_auto_combo()` to preserve manual edits
  (i.e. an auto-blend redesign — explicit non-goal), else edits are silently
  reverted. Rejected.

### C3. Hide `auto` from the blends API entirely

- Pros: smallest surface.
- Cons: `auto` still shows in every model picker; a management panel that
  lists "all blends" but omits one the picker shows reads as a bug. Rejected.

**Chosen: C1.** Evidence requirement in [validation.md](validation.md): a test
proves `auto` cannot be modified or deleted via the blends API (403), and
`ensure_auto_combo()` still manages it afterwards.

## Decision D — Failure behavior when 9router is down

### D1. Surface the standard 503 failure envelope; UI unavailable banner (chosen)

The adapter already maps connection failure to
`NineRouterAPIError(status=503, code="nine_router_unavailable")` and
`EXPECTED_ERRORS` already converts that into the standard failure envelope
(findings.md §7). The blends UI shows a "9router is not reachable" banner,
disables mutations, and offers retry — the same pattern the Connectors view
uses when `providers()` degrades.

- Pros: zero new machinery; honest (no stale data shown as live); consistent
  with every other 9router-backed surface.
- Cons: the panel is empty while 9router is down — acceptable, since blends
  cannot be *used* while it is down either.

### D2. Serve a cached last-known snapshot

- Cons: requires Brain4All-side persistence of 9router state — violates the
  "Brain4All stores nothing" rule of this plan and the no-mock/no-stale
  principle; invalidation bugs. Rejected.

**Chosen: D1.**

## Decision E — How much of fusion to expose

### E1. Expose fusion fully, with cost warning (chosen)

Strategy radio includes fusion; judge-model select; the verified caveat copy
("Fusion runs every model in the blend on each request (higher cost); tools
are disabled for fusion" — tools-stripping verified in findings.md §4).
`fusionTuning` is *not* given a UI: until Phase 0 records its schema
(findings.md §10 item 10), the API passes it through opaquely when present
and never fabricates it.

- Pros: the capability is verified working upstream in the pinned version;
  it is the headline reason "blend" is a good name; hiding a working feature
  helps no one; the cost risk is a *disclosure* problem, solved with copy,
  not a capability problem.
- Cons: users can burn quota fast — mitigated by the warning, by fusion being
  listed last, and by plan-009 analytics attributing the spend to the blend
  name so it is visible.

### E2. Hide fusion initially (fallback + round-robin only)

- Pros: smaller Phase-0 surface (no judge/tuning probes).
- Cons: ships a strategy picker that misses the most differentiated option;
  users who saw fusion in the 9router dashboard would find Brain4All
  inexplicably behind; the settings write path is identical work anyway
  (same `comboStrategies` entry), so the saving is one select and one probe.

**Chosen: E1** — expose, warn, and keep `fusionTuning` opaque until probed.
