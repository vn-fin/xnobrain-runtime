# BUG-021: Task notification bell completely overlaps Test Agent

## Severity

High — the Test Agent action cannot be activated with ordinary pointer input on desktop.

## Area

Agent header and global task notifications

## Environment

Local development started with `make dev`, headed Chromium 148 at the recorded desktop viewport, tested 2026-08-14.

## Prerequisites

An agent page with both Test Agent and the global notification bell rendered.

## Reproduction

1. Open an agent at a 1600×1000 desktop viewport.
2. Move/click the visible **Test agent** header action.

## Actual result

The global `.kanban-notifications` control is positioned directly over the Test Agent button and intercepts all pointer events. Measured boxes are nearly identical:

- Test Agent: `x=1500, y=11.5, width=34, height=34`
- Notifications: `x=1502, y=10, width=38, height=38`, `z-index: 240`

Repeated real click attempts hit the bell SVG instead, so the model/provider test cannot be launched.

## Expected result

Global notification and page-specific header actions should occupy distinct layout slots, remain visible, and have non-overlapping hit targets at supported viewport sizes.

## Reproducibility

Reproduced consistently on the Approval Agent after reload on 2026-08-14.

## Impact

Users cannot test an agent connection/model from the primary desktop header and may activate notifications unintentionally.

## Suggested fix

- Put all right-header controls into one flex/grid action container rather than independent fixed/absolute positions.
- Add collision/viewport tests that assert element bounding boxes do not overlap.
- Preserve at least 44×44 CSS-pixel hit targets without layering one action above another.

## Evidence

- [Overlapping Test Agent and notification controls](../evidence/test-agent-notification-overlay.png)
