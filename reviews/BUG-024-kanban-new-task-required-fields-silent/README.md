# BUG-024: New Kanban task silently disables Create for required fields

## Severity

Medium — users receive incomplete and inconsistent guidance when a task cannot be created.

## Area

Kanban → New task

## Reproduction

1. Open a board and click **New task**.
2. Leave Title and Description blank.
3. Inspect/attempt **Create task**.

## Actual result

Create task is disabled with no inline errors or explanatory accessible state. Description shows the word `REQUIRED`, while Title is also required but has no marker. Neither field uses the application’s requested `*` convention.

## Expected result

Every required label should use a consistent `*`, and submission should focus the first invalid field with concise associated validation messages.

## Reproducibility

Reproduced consistently on 2026-08-14. A valid retained backlog task was subsequently created successfully.

## Impact

Users can mistake the disabled action for a broken form and cannot discover all missing requirements reliably.

## Suggested fix

- Reuse one required-field component across Kanban, agent, automation, Blend, skill, and workspace forms.
- Provide inline errors with `aria-describedby`/`aria-invalid` and an error summary where appropriate.
- Do not rely solely on a disabled primary action for validation communication.

## Evidence

- [Blank New task form](../../evidence/kanban-new-task-required-silent.png)

