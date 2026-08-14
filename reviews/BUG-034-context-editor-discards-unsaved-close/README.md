# BUG-034: Context editor silently discards unsaved changes on close

## Severity

Medium — potentially substantial context edits are lost without warning.

## Area

Settings → Context files

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

Open an existing QA-agent `SOUL.md` or `AGENTS.md` and enter Edit mode.

## Reproduction

1. Append harmless text without saving.
2. Select the editor’s **Close** icon.
3. Reopen the same file.

## Actual result

The editor closes immediately with no warning. Reopening shows the original content; the unsaved draft is gone. The QA marker was intentionally not saved.

## Expected result

When the draft differs from loaded content, closing via X, backdrop, Escape, agent switch, or route navigation should ask whether to discard or continue editing. Clean drafts may close immediately.

## Reproducibility

Reproduced consistently on 2026-08-14.

## Impact

Users can lose personality or workspace-instruction changes through an accidental close or backdrop click.

## Suggested fix

Derive a dirty flag from `draft !== content`, route every close path through one guard, add a discard confirmation with Continue editing as the safe default, and cover X/backdrop/Escape/route changes in tests.

## Evidence

- [Unsaved marker before closing](../evidence/context-unsaved-close-before.png)
- [File reopened without a warning or persisted marker](../evidence/context-unsaved-close-no-warning.png)
