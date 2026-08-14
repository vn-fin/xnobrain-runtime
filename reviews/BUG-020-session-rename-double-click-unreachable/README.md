# BUG-020: Session rename cannot be reached with a real double-click

## Severity

Medium — a documented/implemented session lifecycle action is effectively unavailable from the UI.

## Area

Chat session picker

## Environment

Local development started with `make dev`, headed Chromium 148, tested 2026-08-14.

## Prerequisites

An agent with at least one retained session shown in the session picker.

## Reproduction

1. Open the session picker on an existing selected session.
2. Double-click the session row/title.
3. Look for the rename textbox.

## Actual result

The first click selects the row and immediately closes the picker, so the second click never reaches the same mounted element. No rename input appears. The source attaches both the closing selection behavior to `onClick` and `startTabRename` to `onDoubleClick` on the same button, corroborating the manual behavior.

## Expected result

Rename should be available from a discoverable menu/action, or double-click should keep the picker mounted and enter rename mode. Enter, Escape, and blur behavior should then be testable and accessible.

## Reproducibility

Reproduced repeatedly on the retained research session on 2026-08-14.

## Impact

Users cannot rename sessions through ordinary mouse input and receive no hint that rename exists.

## Suggested fix

- Add an explicit Rename action beside Close session (preferred for accessibility).
- If double-click is retained, defer selection/close and distinguish the double-click gesture safely.
- Add keyboard and pointer end-to-end coverage for Enter, Escape, blur, and reload persistence.

## Evidence

- [Session picker where double-click closes instead of renaming](../evidence/session-rename-unreachable.png)
