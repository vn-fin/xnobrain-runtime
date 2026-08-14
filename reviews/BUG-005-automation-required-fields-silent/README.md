# BUG-005: New automation hides required-field validation

## Severity

Medium — users cannot discover why the primary action is unavailable.

## Area

Automation → New cron

## Reproduction

1. Open `/automations`.
2. Select **New cron**.
3. Leave Name and Prompt empty.
4. Inspect or attempt to activate **Create cron**.

## Actual result

- The form labels read `Name` and `Prompt` with no required indicator.
- **Create cron** is disabled.
- No inline message explains which fields are missing.

## Expected result

Required labels should include `*`, and attempting an incomplete submission should identify every missing field, move focus to the first error, and expose an accessible error message.

## Suggested fix

- Render `Name *` and `Prompt *` consistently with the product’s other required forms.
- Prefer an enabled submit action that runs validation, or render persistent helper/error text while it is disabled.
- Add `required`, `aria-invalid`, and `aria-describedby` associations to the native controls.
- Add a UI regression test for blank, whitespace-only, and corrected submissions.

## Evidence

![New automation form](../../evidence/automation-new-form.png)
