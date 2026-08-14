# BUG-010: Login password autofills the provider API-key field

## Severity

Critical — a user can accidentally submit their account password to a third-party provider endpoint as an API key.

## Area

Settings → Connectors → Add API key

## Reproduction

1. Log in through Chrome using stored or recently entered credentials.
2. Open `/settings/connectors`.
3. Scroll to the persistent **Add API key** form.
4. Inspect the masked API-key control and **Save** state without editing the form.

## Actual result

- Chrome autofills the account login password into the provider API-key field.
- The provider defaults to OpenAI.
- Because the password field is now non-empty, **Save** becomes enabled.
- Clicking Save would store the account password as a provider credential and could send it to the provider during connection testing.
- The issue reproduced through the legacy `/connections` route. After selecting OpenAI-compatible, Chrome also autofilled the account email into Base URL and the account password into API key.

The QA pass did not submit the form. All autofilled fields were immediately cleared, and the report intentionally does not contain the credential value.

## Expected result

Authentication credentials must never autofill unrelated provider secrets. The API-key field should start empty and Save should remain disabled until the user deliberately enters a key during the current interaction.

## Suggested fix

- Mark provider secret inputs with a unique non-login `name` and `autocomplete="new-password"` (validate Chrome behavior; `off` alone is often ignored for password fields).
- Give the login form correct `autocomplete="username"` and `autocomplete="current-password"` tokens so the password manager has unambiguous targets.
- Do not accept an initial browser-autofilled value as user input; require a trusted input event or explicit reveal/edit before enabling Save.
- Clear secret draft state on route entry and provider changes.
- Add an end-to-end Chrome test with saved login credentials asserting API-key inputs remain empty and Save disabled.

## Evidence

The screenshot shows only the browser-masked value; no secret is visible.

![Masked autofilled provider secret](../../evidence/api-key-password-autofill.png)
