# BUG-032: Unconfigured email delivery can be submitted

## Severity

Medium — the form permits a destination the UI explicitly says is unavailable.

## Area

Automation detail → Add delivery destination

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

No configured email delivery connector. The target picker lists `Email — not configured` as disabled.

## Reproduction

1. Open a retained automation and select **Add target**.
2. Change the type to **Email**.
3. Inspect the target selection and **Add destination** state.

## Actual result

The disabled `Email — not configured` option is programmatically selected with value `email`, and **Add destination** is enabled. The destructive/invalid submission was not made.

## Expected result

An unavailable option must never become the selected destination, and Add must remain disabled until a configured, available target is explicitly selected.

## Reproducibility

Reproduced consistently on 2026-08-14.

## Impact

Users can save a delivery target that cannot deliver, leading to misleading Pending/failed automation states.

## Suggested fix

When type changes, initialize `targetDestination` to an available option only; otherwise use an empty string. Validate availability again in the submit handler and API.

## Evidence

- [Unavailable email selected while Add remains enabled](../evidence/automation-unconfigured-email-add-enabled.png)
