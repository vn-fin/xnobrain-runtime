# BUG-028: Unsupported workspace files have no preview fallback

## Severity

Medium — users cannot tell whether opening the file failed or is still loading.

## Area

Workspace file browser and preview

## Environment

- Local development started with `make dev`
- Chromium 148, headed browser
- Tested 2026-08-14

## Prerequisites

The retained `qa-2026-08-14/qa-unsupported.xyz` fixture uploaded through the visible Workspace UI.

## Reproduction

1. Open Workspace and enter `qa-2026-08-14`.
2. Select `qa-unsupported.xyz`.

## Actual result

Selection silently does nothing. No preview panel, unsupported-format explanation, error, or download fallback appears, and the interface remains otherwise responsive.

## Expected result

Open a lightweight fallback panel that identifies the unsupported type and offers a safe Download action with the original name and size.

## Reproducibility

Reproduced consistently on 2026-08-14.

## Impact

Users cannot distinguish unsupported content from a broken click and must discover a different download path on their own.

## Suggested fix

Return a typed unsupported-preview state from the preview dispatcher and always render metadata plus Download. Keep size checks ahead of content decoding so large files use the same bounded fallback.

## Evidence

- [Unsupported file remains in the list after selection](../evidence/workspace-unsupported-preview-fallback.png)
