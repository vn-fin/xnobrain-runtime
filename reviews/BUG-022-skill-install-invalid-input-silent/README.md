# BUG-022: Install Skill silently ignores malformed identifiers

## Severity

Medium — the install flow accepts a submission gesture but gives no validation or recovery guidance.

## Area

Skills Library → Install new

## Reproduction

1. Open `/skills` and click **Install new**.
2. Enter `not a valid skill identifier ???` in the SKILL.md URL/hub identifier field.
3. Click **Install**.

## Actual result

Nothing visible happens: no inline error, toast, focus change, loading state, or network request. The invalid value remains in the input and no skill is installed. The blank state similarly disables Install without required-field guidance.

## Expected result

The UI should validate supported URL/hub identifier syntax, focus the field, and show a concise accessible error explaining accepted formats. The label should identify the field as required.

## Reproducibility

Reproduced twice on 2026-08-14.

## Impact

Users cannot distinguish invalid syntax from a broken button or network problem.

## Suggested fix

- Add client-side identifier/URL validation and an associated inline error.
- Show accepted examples and required-field notation.
- Disable duplicate submission only while a request is actually in progress and expose request errors.

## Evidence

- [Malformed identifier before submission](../../evidence/skill-install-invalid-before.png)
- [Unchanged silent state after submission](../../evidence/skill-install-invalid-after.png)

