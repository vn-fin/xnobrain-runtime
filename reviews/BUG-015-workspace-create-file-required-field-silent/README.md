# BUG-015: Create-file dialog silently disables submission for a required name

## Severity

Medium — the user cannot create a file but receives no indication of what is required or how to proceed.

## Area

Agent workspace → Create file

## Reproduction

1. Open an agent’s Workspace panel and enter a directory.
2. Click **New file**.
3. Leave **File name** blank and inspect/attempt the **Create** action.

## Actual result

**Create** is disabled. The File name label has no required `*`, there is no inline validation message, and the disabled button has no explanatory accessible state or tooltip.

## Expected result

The label should identify the field as required and the dialog should expose concise inline validation (immediately or after a submission attempt), including an accessible relationship between the input and error.

## Reproducibility

Reproduced consistently in the local development build on 2026-08-14.

## Impact

Users can interpret the inactive button as a broken interface, particularly when using keyboard or assistive technology.

## Suggested fix

- Add `File name *` and descriptive validation text.
- Prefer allowing submission and then focusing the invalid input, or give the disabled action an accessible explanation.
- Reuse the same required-field component and behavior across agent, automation, Blend, and workspace forms.

## Evidence

- [Blank create-file dialog](../../evidence/workspace-create-file-required-silent.png)

