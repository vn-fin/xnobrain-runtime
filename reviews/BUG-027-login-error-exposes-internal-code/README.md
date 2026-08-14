# BUG-027: Invalid login displays an internal error code

## Severity

Low — authentication is rejected correctly, but the message is not useful to a user.

## Area

Login screen

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

An unauthenticated application tab. The retained invalid values used for this test were cleared immediately after submission.

## Reproduction

1. Open the login screen.
2. Enter an obviously invalid email and password.
3. Select **Sign in**.

## Actual result

The form displays the backend identifier `INVALID_LOGIN_CREDENTIALS` verbatim. The password is not rendered or leaked in the page.

## Expected result

Show concise user-facing guidance such as “Email or password is incorrect. Try again.” while retaining the internal code only in structured diagnostics.

## Reproducibility

Reproduced once on 2026-08-14 with an intentionally invalid `.invalid` address.

## Impact

Users see implementation terminology instead of actionable authentication guidance, and localization cannot provide a natural message.

## Suggested fix

Map known authentication codes to localized UI copy in the login error boundary. Use a generic safe fallback for unknown server errors and never interpolate submitted credentials.

## Evidence

- [Invalid-login inline error](../evidence/login-invalid-credentials-inline-error.png)
