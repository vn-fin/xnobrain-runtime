# BUG-014: Authenticated application links open signed out in a new tab

## Severity

High — normal new-tab navigation cannot reuse the active login, so deep links and “open in new tab” workflows unexpectedly land on the sign-in screen.

## Area

Authentication persistence and application navigation

## Reproduction

1. Sign in and keep an authenticated XNOBrain agent/session page open.
2. In the same Chrome profile and browser context, open an application agent URL in a new tab.
3. Compare the original and new tabs.

## Actual result

The original tab remains authenticated, but the new tab displays the sign-in screen. This indicates the authentication state is scoped to the existing tab (for example, session storage) rather than being available to another same-origin tab.

## Expected result

A same-origin tab opened from an authenticated browser profile should either inherit/recover the authenticated session or present an explicit secure handoff flow. Deep links should not silently discard the active login.

## Suggested fix

- Store the session in a secure same-origin cookie, or implement an explicit cross-tab session bootstrap.
- Keep access tokens out of URLs and client-visible logs.
- Add an end-to-end test that signs in, opens an agent/session URL in a new page in the same browser context, and verifies authenticated rendering.

## Evidence

- [New application tab displays sign-in](../../evidence/new-tab-not-authenticated.png)

