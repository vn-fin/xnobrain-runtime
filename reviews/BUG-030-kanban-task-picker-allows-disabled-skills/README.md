# BUG-030: Kanban task picker allows disabled skills

## Severity

Medium — a task can request capabilities disabled on its assignee profile.

## Area

Kanban → New task → Skills used for this task

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

Select `QA 2026-08-14 Primary Agent`, which has a mix of enabled and disabled installed skills.

## Reproduction

1. Open **New task** and choose **One agent**.
2. Select the QA Primary Agent.
3. Inspect the skill picker and any disabled-skill checkbox.

## Actual result

All 80 installed skills are listed. Entries labelled `DISABLED` have enabled checkboxes and can be selected. Enabled skills are preselected, but disabled skills are not filtered or guarded.

## Expected result

Only enabled installed skills should be selectable for the chosen assignee. If disabled skills are shown for context, their checkboxes must be disabled and the UI should explain how to enable them.

## Reproducibility

Reproduced consistently on 2026-08-14. No disabled skill was selected during the retained task creation.

## Impact

Tasks may save an impossible or policy-conflicting skill configuration and then fail or silently ignore requested capabilities at runtime.

## Suggested fix

Filter the picker using the effective per-agent enabled state returned by the skills API, validate the submitted skill IDs server-side, and add an assignee-change reconciliation warning for selections that become unavailable.

## Evidence

- [Disabled skills remain selectable in the task picker](../evidence/kanban-task-skill-picker-disabled-selectable.png)
