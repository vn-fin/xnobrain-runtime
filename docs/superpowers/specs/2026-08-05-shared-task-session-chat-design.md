# Shared Task Session Chat Design

## Goal

Give a Kanban task's tracked Hermes session the same interactive conversation
experience as a Team run node. Users can inspect the full transcript and
reasoning trace, continue the conversation, stop an active response, and attach
workspace files without leaving the Task Board.

## Scope

- Replace the Kanban read-only session modal with the interactive Team session
  experience.
- Preserve the current Kanban task drawer, tracking URL, route state, and task
  lifecycle actions.
- Preserve the Team conversation modal's existing behavior and appearance.
- Do not add or change backend routes or conversation contracts.

## Architecture

Extract the reusable conversation UI and behavior from `TeamsView.tsx` into a
shared session modal component. The component owns live conversation state via
`useConversation`, transcript rendering, usage metrics, auto-scroll, composer
state, stopping, and workspace uploads. Team and Kanban remain responsible for
adapting their domain objects into the component's small presentation contract.

The shared component receives:

- agent and conversation identifiers;
- title, subtitle, execution status, and optional elapsed-time display;
- an optional fallback transcript and usage snapshot for Team run history;
- a close callback.

It does not depend on a Team step or Kanban task. This keeps domain-specific
status mapping in the parent views and prevents the shared component from
accumulating branching rules.

## User Experience

Selecting **View session** in a Kanban task opens the same modal shell used by a
Team node. It shows:

- tokens, reasoning steps, execution time, and message count;
- user messages, assistant answers, and collapsible reasoning/tool runs;
- live streaming content and inline errors;
- a composer that supports Enter-to-send, Shift+Enter for a newline, stopping
  an active response, file/folder drag-and-drop, file selection, upload
  progress, and attachment removal.

The composer sends to the existing task conversation identified by
`conversation.agent_id` and `conversation.id`. It is disabled if the linked
agent or conversation is unavailable. Closing the modal leaves the user in the
Kanban task drawer. The existing tracking URL remains visible in the drawer.

## Data Flow

1. The Kanban detail endpoint continues returning the conversation identifier,
   agent identifier, and tracking URL.
2. `KanbanView` resolves the linked agent and opens the shared modal.
3. The shared modal calls `useConversation(agentId, conversationId, model,
   false)` to load and stream the session without changing the main Chat view's
   active-session marker.
4. Messages and stop requests use the existing conversation APIs and stream
   store. Uploads use the existing Workspace API and reference uploaded paths in
   the outgoing message, matching Team behavior.
5. Team may supply its already-loaded historical transcript as a fallback until
   the live session request resolves; Kanban relies on the live session directly.

## Error Handling

- A failed session load shows the existing inline unavailable state.
- If older messages are available, a refresh/stream error is shown without
  hiding the transcript.
- Upload failures remain local to the composer and do not close the modal.
- Missing agent or conversation identifiers disable interaction instead of
  sending an invalid request.

## Testing

- Shared component tests cover transcript/run rendering, sending, stopping,
  attachments, and disabled state.
- Kanban tests verify **View session** opens the interactive modal for the task's
  linked agent/session and that a submitted message targets that session.
- Team tests verify the refactor preserves its existing interactive modal.
- Run focused Vitest suites for the shared component, `KanbanView`, and
  `TeamsView`, followed by `npm run build`.

## Non-Goals

- Creating a conversation for a task that has not started one.
- Changing task execution, assignment, scheduling, or conversation URLs.
- Moving the user to the main Chat screen.
- Adding backend persistence or API fields.
