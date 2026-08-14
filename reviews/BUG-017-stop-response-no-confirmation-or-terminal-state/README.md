# BUG-017: Stop response cancels immediately and leaves no visible terminal state

## Severity

Medium — an accidental click irreversibly stops active workers, and the retained conversation does not explain why no answer exists.

## Area

Chat composer → active response Stop

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

- Retained QA session: `20260814_131256_3f4f23`
- One parent response with three delegated workers running and two queued

## Reproduction

1. Start a response that delegates five short tasks.
2. While three workers are running and two are queued, click the composer **Stop** control.
3. Reload the session.

## Actual result

The response is cancelled immediately; no confirmation is shown. After reload, the user message and a `Worked · 1 step` activity header remain, but there is no cancelled/stopped terminal event, explanation, delegation card, or partial worker state. The composer recovers and the agent status correctly returns to idle gray.

The durable run record is `cancelled`, proving this was not a normal completion.

## Expected result

Stopping a response with active/queued delegated work should require an explicit confirmation naming the affected worker counts. The retained timeline should show a clear cancelled terminal state and keep any completed/partial worker information available.

## Reproducibility

Reproduced once in the purpose-built retained QA session on 2026-08-14.

## Impact

Users can lose an expensive multi-agent run from a single click and cannot distinguish a cancelled run from a missing or corrupted assistant response after reload.

## Suggested fix

- Reuse the Cancel All confirmation pattern for Stop when delegation is active.
- Persist and render a `run.cancelled` terminal event with timestamp and worker summary.
- Preserve the delegation card and completed results after parent cancellation.

## Evidence

- [State immediately after clicking Stop (no confirmation)](../evidence/chat-stop-confirmation.png)
- [Cancel All confirmation that Stop should mirror](../evidence/delegation-cancel-all-confirmation.png)
