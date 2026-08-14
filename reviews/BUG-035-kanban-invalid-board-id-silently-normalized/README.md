# BUG-035: Invalid board ID is silently normalized and created

## Severity

Medium — the saved identifier differs from the explicitly submitted identifier.

## Area

Kanban → Create task board

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

Open Create task board. The helper states that IDs accept lowercase letters, numbers, hyphens, and underscores.

## Reproduction

1. Enter a valid QA-prefixed board name.
2. Enter `BAD ID` in Board ID.
3. Observe that Create remains enabled and submit once.

## Actual result

The dialog closes and creates a retained board with ID `bad-id`. No validation or normalization preview tells the user that spaces/case will change, even though the manually edited field displayed `BAD ID`.

## Expected result

Either reject the value inline according to the documented character set, or normalize it live in the field before submission and clearly show the exact resulting ID. Never silently save a different identifier.

## Reproducibility

Reproduced once on 2026-08-14. The additive `QA duplicate board probe` / `board/bad-id` record was retained for review.

## Impact

Deep links, integrations, and user expectations may target an ID that was never actually saved; collisions can also emerge only after hidden normalization.

## Suggested fix

Use the same canonicalization function on input and submit, update the visible field immediately, and perform duplicate validation against the canonical value before enabling Create.

## Evidence

- [Created board shows the silently normalized ID](../evidence/kanban-invalid-board-id-normalized-created.png)
