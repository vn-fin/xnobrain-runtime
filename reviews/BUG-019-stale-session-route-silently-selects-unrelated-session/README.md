# BUG-019: A stale session URL silently redirects to an unrelated worker session

## Severity

High — a shared/bookmarked URL can display the wrong conversation without warning, creating a serious context and privacy ambiguity.

## Area

Conversation deep-link routing

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

An authenticated QA agent with retained normal and worker sessions plus a deliberately nonexistent session ID.

## Reproduction

1. While authenticated on agent `bjpvdd`, navigate in the same tab to `/agents/bjpvdd/sessions/QA_MISSING_20260814`.
2. Wait for session loading to finish.
3. Inspect the resulting URL and conversation content.

## Actual result

The route silently changes to `/agents/bjpvdd/sessions/20260814_131353_32d552` and displays an unrelated delegated worker conversation containing the QA-05 task/output. There is no not-found message, recovery choice, or indication that the requested session ID did not exist. Browser Back did not recover the prior research-session route after the internal replacement.

## Expected result

An unknown session ID should remain identifiable as unknown and render a recoverable not-found state with explicit actions such as return to session list or create a new session. It must never substitute another conversation silently.

## Reproducibility

Reproduced with a unique nonexistent ID on 2026-08-14.

## Impact

Users can believe they are reviewing one shared session while the application displays another session’s content. Automated links, audit trails, and browser history become unreliable.

## Suggested fix

- Treat a requested session ID as authoritative and render 404/recovery UI when lookup fails.
- Do not use latest/first-session fallback when the URL explicitly contains an ID.
- Add deep-link tests for missing sessions, wrong-agent session IDs, Back/Forward, and reload.

## Evidence

- [Stale route replaced by unrelated worker session](../evidence/stale-session-route.png)
