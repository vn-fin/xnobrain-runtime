# BUG-018: Assistant feedback and More buttons are inert

## Severity

Medium — three visible message actions imply functionality but provide no feedback or result.

## Area

Chat → completed assistant message actions

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

A completed retained assistant response with Copy, feedback, Retry, and More controls visible.

## Reproduction

1. Open a session containing a completed assistant response.
2. Click **Good response**.
3. Click **Bad response**.
4. Click **More**.

## Actual result

None of the controls produce visible state, a menu, a toast, or an accessible pressed state. **More** opens no menu. Source inspection corroborates the manual result: the three buttons in `ChatArea.tsx` have no event handlers. Copy works correctly and copies only the assistant response text; Retry is separately wired.

## Expected result

Feedback actions should visibly persist/toggle their rating and announce success or failure. More should open an accessible menu of implemented actions. If functionality is unavailable, the controls should not render.

## Reproducibility

Reproduced consistently on the completed retained research session on 2026-08-14.

## Impact

Users cannot tell whether feedback was accepted and repeatedly click controls that do nothing. The inert More button harms keyboard and assistive-technology discoverability.

## Suggested fix

- Wire feedback to a persisted API and expose `aria-pressed` plus a success/error announcement.
- Implement an accessible More menu with focus management and Escape/outside dismissal.
- Hide unfinished actions behind a feature flag until they are functional.

## Evidence

- [Assistant action controls](../evidence/assistant-actions-noop.png)
