# BUG-023: Custom provider form enables Save for a malformed Base URL

## Severity

High — invalid endpoint configuration can be persisted and then fail only at connection/use time.

## Area

Settings → Connectors → OpenAI-compatible

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

Open the custom-compatible provider form without entering or saving real credentials.

## Reproduction

1. Open the OpenAI-compatible Add API key form.
2. Enter `not-a-valid-url` as Base URL.
3. Enter a harmless unsent QA marker in API key.
4. Blur Base URL and inspect Save; do not submit.

## Actual result

Save remains enabled and there is no inline validation or malformed-URL message. The visible form also exposes no display-name, model-list, or default-model fields.

## Expected result

Base URL should require an allowed absolute HTTP(S) URL and Save should be guarded with an accessible inline error. The form should make provider identity/model discovery behavior explicit.

## Reproducibility

Reproduced on 2026-08-14. No invalid provider data was saved.

## Impact

Users can create broken custom connections and receive delayed failures. Combined with browser autofill (BUG-010), unrelated values can appear valid enough to submit.

## Suggested fix

- Parse with `URL`, allow only supported schemes, and reject relative strings.
- Normalize the `/v1` policy and show examples.
- Associate inline errors with the field and focus it on invalid submission.
- Add tests for missing scheme, whitespace, unsupported schemes, and valid localhost URLs.

## Evidence

- [Malformed custom-provider URL with enabled Save](../evidence/custom-provider-invalid-url.png)
