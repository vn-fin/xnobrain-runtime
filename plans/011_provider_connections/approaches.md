# 011 — Approaches

Options considered and the chosen ones. Context in
[findings.md](findings.md); the resulting design in
[architecture.md](architecture.md).

## Decision A — Widen the 6-provider allowlist?

9router supports 40+ providers; XNOBrain curates six
(`SUPPORTED_ROUTER_PROVIDERS` in
[`xnobrain/integrations/nine_router.py`](../../xnobrain/integrations/nine_router.py)
lines 25–27).

### A1. Keep the curated six (chosen — now)

Safest. Everything downstream is built and tested for exactly these six:

- The OAuth flows XNOBrain drives (`start_provider_connect` /
  `submit_provider_connect` in
  [`xnobrain/services/platform.py`](../../xnobrain/services/platform.py)
  lines 619–653) hardcode per-provider redirect URIs and the
  authorize/exchange choreography for `claude`/`codex`/`antigravity` only.
- The UI auth copy, brand icons (`providerBrand()` in
  [`src/api/mappers/providers.ts`](../../src/api/mappers/providers.ts)),
  and the api-key placeholder text are written per provider.
- `ROUTER_MODEL_ALIASES` (lines 30–37 of the adapter) is a **hardcoded**
  provider→model-owner mapping used by `list_models()` and `usage()` for
  owner matching; an unlisted provider would silently produce zero models.

Multi-account is orthogonal to provider count; coupling the two would make
this plan untestable.

### A2. Allowlist configurable via env (follow-up flag — recommended later)

E.g. `XNOBRAIN_EXTRA_ROUTER_PROVIDERS=openrouter,groq` merged into the
frozensets at import. Cheap to add once A1 ships, but each added provider
still needs an alias entry, brand fallback, and auth-mode classification, so
it must ship with a "best-effort, api-key-only, generic branding" contract.
Deliberately **out of scope here**; noted as the natural follow-up.

### A3. Dynamically accept whatever `/api/providers` returns (rejected)

Zero-config but wrong today: unknown auth modes would render broken connect
buttons, `ROUTER_MODEL_ALIASES` misses mean invisible models, and the OAuth
adapter would advertise flows it cannot drive. Also makes the UI contract
depend on an unpinned upstream list.

**Decision: A1 now, A2 as a separate follow-up flag.** This plan does not
change `SUPPORTED_ROUTER_PROVIDERS` or `SUPPORTED_PROVIDERS`.

## Decision B — Priority UI

### B1. Explicit editable priority numbers (rejected)

Exposes 9router's raw integers; users must invent a numbering scheme, and
collisions/gaps (`priority` is nullable in the schema) leak into the UI.

### B2. Up/down buttons writing normalized 0..n-1 (chosen)

On every move, the frontend recomputes the full order for that provider and
PATCHes only the rows whose priority changed (usually two). Backend clamps to
0..999 and treats absent as 0. Deterministic, keyboard-accessible, trivial to
test, no drag dependency. Ties (two accounts both priority 0 from before) are
resolved on first reorder.

### B3. Drag-and-drop reorder (rejected for now)

Nicer for many rows, but adds a dependency or bespoke DnD code for lists that
will typically hold 2–3 rows. Can layer on top of B2 later without API
changes.

**Decision: B2.** Contingency: if Phase 0 finds no working PUT for
`priority` (findings.md §6.1), hide the reorder controls and ship
active/test/delete/usage only — the API contract (`ConnectionPatch.priority`)
stays, returning 502 from the adapter, and this file gets updated.

## Decision C — What the UI says about rotation vs priority

The router's README says accounts **round-robin** per provider; the
`(provider, priority)` index suggests priority orders selection/fallback.
Which effect the user actually observes is Phase 0 item 3.

### C1. Say nothing (rejected)

Users will assume "top account is used" and misread quota consumption on the
second account as a bug.

### C2. Claim strict priority ordering (rejected)

Unverified; contradicts the README's round-robin claim.

### C3. Honest combined label, finalized by Phase 0 (chosen)

Ship the caption **"Active accounts rotate; order sets fallback
preference."** as the working copy, and make the Phase 0 probe result the
authority: if probing shows pure round-robin with no priority effect, the
copy becomes "Active accounts rotate automatically" and reorder is hidden
(see B); if it shows strict priority, the copy says so. The i18n key
(`connections.accountsHint`) is added once; only its text changes.

**Decision: C3.** The copy is part of the Phase 0 exit criteria — Phase 4
must not ship the label unreviewed against the probe log.

## Decision D — Provider-level disconnect: keep delete-all or remove it?

### D1. Keep provider-level disconnect as explicit delete-all with confirm (chosen)

`disconnect_provider()`
([`xnobrain/services/platform.py`](../../xnobrain/services/platform.py)
lines 662–669) already means "delete every connection"; existing API clients
depend on that route. Keeping it preserves backward compatibility and gives a
legitimate "start over with this provider" action. The fix is honesty in the
UI: the button becomes **"Remove all accounts…"** with a confirm dialog that
names the count ("This removes all 2 Codex accounts from 9router."). One
account → confirm still shown, copy degrades naturally.

### D2. Remove the provider-level route, per-connection delete only (rejected)

Breaks the published `/providers/{id}/disconnect` contract and the
onboarding/reset flows for no gain; "remove everything" would take N clicks.

**Decision: D1.** Per-connection delete is the new normal path; delete-all
survives as the explicitly-labeled bulk action.

## Decision E — Where "add an OAuth account" lives

Considered a new `POST .../connections` variant for OAuth (body-less, returns
the login URL). Rejected: it would duplicate
`start_provider_connect`/`submit_provider_connect` (which already create a
new 9router row per completed flow, with state/verifier handling and the
popup UX built around them). **Chosen: `POST .../connections` accepts
API-key bodies only; OAuth additions re-run the existing connect flow**, with
the frontend simply offering it while already connected
(architecture.md sequence diagram). One flow, one state machine, no drift.
