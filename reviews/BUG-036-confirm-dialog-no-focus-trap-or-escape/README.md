# BUG-036: Confirmation dialogs do not trap focus or close with Escape

## Severity

High for accessibility — keyboard users can interact with obscured controls and cannot use the expected safe dismissal key.

## Area

Shared confirmation dialog (reproduced with Delete Agent)

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

Open the retained QA agent’s options and select Delete, without confirming deletion.

## Reproduction

1. Open the Delete Agent alert dialog.
2. Inspect initial focus, press Escape, then press Tab repeatedly.

## Actual result

Initial focus remains on `body`; Escape does not close the dialog; Tab proceeds through sidebar and page controls outside the modal. Outside click and the explicit Cancel button do close safely, but focus is not restored to the invoking menu control.

## Expected result

Focus should enter the dialog (normally Cancel), cycle only among dialog controls, Escape should invoke the safe cancel path, background content should be inert, and focus should return to the trigger after dismissal.

## Reproducibility

Reproduced consistently on 2026-08-14. Delete was never confirmed.

## Impact

Keyboard and screen-reader users can lose context, reach hidden controls, or struggle to dismiss a destructive confirmation safely.

## Suggested fix

Use a tested focus-trap/dialog primitive, set initial focus to Cancel, handle Escape through the same cancellation callback, apply `inert`/appropriate aria hiding to the background, and restore the saved trigger element on unmount.

## Evidence

- [Delete confirmation during keyboard-focus test](../evidence/confirm-dialog-focus-escape-failure.png)
