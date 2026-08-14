# BUG-029: Workspace file name accepts parent-directory traversal

## Severity

High — a file is created outside the directory shown in the create dialog.

## Area

Workspace → Create file

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

The Workspace panel is inside the retained `qa-2026-08-14` folder.

## Reproduction

1. Select **New file**.
2. Enter `../escape.txt` as File name.
3. Select **Create**.
4. Navigate up one directory.

## Actual result

Create remains enabled, the dialog closes without a warning, and a retained zero-byte `escape.txt` appears at the workspace root rather than inside `qa-2026-08-14`.

## Expected result

The visible file-name field must accept only a single safe path segment. Reject `.`, `..`, separators, encoded separators, absolute paths, and normalized paths that differ from the submitted name. The backend must independently enforce containment.

## Reproducibility

Reproduced once on 2026-08-14. A deeper traversal was deliberately not attempted after parent traversal was proven. The created file was retained per the QA preservation rule.

## Impact

Users can unintentionally or maliciously write outside the selected directory. Depending on backend root checks, variants could threaten unrelated workspace content.

## Suggested fix

- Validate with a strict basename policy in the UI and show an inline message.
- Resolve the target server-side, compare it against the canonical current-directory root, and reject any path that escapes it.
- Add tests for slash/backslash, repeated dots, absolute paths, URL encoding, Unicode separators, symlinks, and normalization differences.

## Evidence

- [Create permits the traversal name](../evidence/workspace-path-traversal-validation.png)
- [The file appears in the parent workspace directory](../evidence/workspace-path-traversal-created-parent-file.png)
