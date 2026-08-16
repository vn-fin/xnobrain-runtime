# BUG-014: Authenticated application links open signed out in a new tab

## Severity

High — normal new-tab navigation cannot reuse the active login, so deep links and “open in new tab” workflows unexpectedly land on the sign-in screen.

## Area

Authentication persistence and application navigation

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

An authenticated application tab and a second same-browser tab opened to an application deep link.

## Reproduction

1. Sign in and keep an authenticated XNOBrain agent/session page open.
2. In the same Chrome profile and browser context, open an application agent URL in a new tab.
3. Compare the original and new tabs.

## Actual result

The original tab remains authenticated, but the new tab displays the sign-in screen. This indicates the authentication state is scoped to the existing tab (for example, session storage) rather than being available to another same-origin tab.

## Expected result

A same-origin tab opened from an authenticated browser profile should either inherit/recover the authenticated session or present an explicit secure handoff flow. Deep links should not silently discard the active login.

## Reproducibility

Reproduced consistently in a second tab while reload in the original authenticated tab continued to work.

## Impact

Shareable links and normal new-tab workflows unexpectedly require another login and cannot restore the requested route.

## Suggested fix

- Store the session in a secure same-origin cookie, or implement an explicit cross-tab session bootstrap.
- Keep access tokens out of URLs and client-visible logs.
- Add an end-to-end test that signs in, opens an agent/session URL in a new page in the same browser context, and verifies authenticated rendering.

## Evidence

- [New application tab displays sign-in](../evidence/new-tab-not-authenticated.png)


## Resolution (2026-08-15)

XNO refresh tokens are now copied to a dedicated same-origin `localStorage` key while access tokens remain scoped to `sessionStorage`. A new application tab can therefore refresh the XNO session even when BroadcastChannel handoff is unavailable or the original tab is not ready. Sign-out clears the shared refresh-token copy, and `src/auth.xno.test.tsx` covers restoration from a separate-tab storage state.
