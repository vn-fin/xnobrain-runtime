# User Message Overflow Fix

## Problem

The main chat renders user messages in a `.user-bubble` with `width: fit-content`, but the bubble and its Markdown content do not allow unbroken strings to shrink or wrap. A long path or URL therefore expands the bubble beyond the chat viewport, and `.message-canvas { overflow: auto; }` exposes a page-level horizontal scrollbar.

The Team conversation modal already contains the desired containment rules and does not exhibit the same bug.

## Design

Apply the established Team conversation wrapping behavior to the main `.user-bubble`:

- allow the bubble to shrink within its parent with `min-width: 0`;
- wrap unbroken paths and URLs with `overflow-wrap: anywhere`;
- retain `word-break: break-word` as a compatibility fallback.

Do not hide horizontal overflow on the entire message canvas. Markdown tables, fenced code blocks, and display math must continue to use their existing internal horizontal scrolling.

## Verification

- Add a focused regression test that renders a user message containing a long unbroken path and confirms the wrapping/containment rules are present.
- Run the focused chat test suite.
- Run the frontend TypeScript/Vite build to verify the change does not break existing Markdown rendering or generated assets.

## Scope

Only the main chat user bubble and its regression coverage are in scope. No assistant-message layout changes or unrelated refactors are included.
