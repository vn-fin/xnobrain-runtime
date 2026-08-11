# User Message Overflow Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep long unbroken paths and URLs inside the main chat user bubble without changing the internal scrolling behavior of tables, code blocks, or display math.

**Architecture:** Reuse the wrapping rules already proven in the Team conversation modal and apply them directly to the shared `.user-bubble` rule used by the main chat. Cover the regression through the rendered component and the real application stylesheet.

**Tech Stack:** React 18, TypeScript, CSS, Vitest, Testing Library, jsdom, Vite.

## Global Constraints

- Only the main chat user bubble and its regression coverage are in scope.
- Do not hide overflow on `.message-canvas`.
- Preserve existing internal horizontal scrolling for Markdown tables, fenced code blocks, and display math.
- Do not modify or stage unrelated untracked files or existing stashes.

---

### Task 1: Contain long user-message text

**Files:**
- Modify: `src/components/ChatArea.test.tsx`
- Modify: `src/styles.css:2155`

**Interfaces:**
- Consumes: `ChatArea` user messages rendered as `.user-bubble > .markdown-body`.
- Produces: a `.user-bubble` whose computed styles allow shrinking and wrapping of unbroken text.

- [ ] **Step 1: Write the failing regression test**

Import the application stylesheet in `src/components/ChatArea.test.tsx`, render `ChatArea` with a user message containing a long path, and assert the real computed styles:

```tsx
it('wraps a long unbroken user message inside the chat viewport', () => {
  const longPath = '/home/user/workspaces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/SCRATCHPAD.md';
  const agent: Agent = {
    id: 'agent-one', name: 'agent-one', title: 'Research', description: '', status: 'ready',
    provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
    skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations: [],
  };
  render(<ChatArea
    agent={agent} agents={[agent]} activeConversation={undefined} providers={[]}
    runs={[]} messages={[{ id: 'long-path', role: 'user', content: longPath, timestamp: 1 }]}
    usage={null} chatStatus="ready" chatError="" streaming={false} canStop={false}
    onSend={vi.fn()} onStop={vi.fn()} onResolveRunApproval={vi.fn()} onRetry={vi.fn()}
    onSelectModel={vi.fn()} onSelectAgent={vi.fn()} onTestAgent={vi.fn()}
    onSelectConversation={vi.fn()} onCreateConversation={vi.fn()} onDeleteConversation={vi.fn()}
    onRenameConversation={vi.fn()} onOpenFile={vi.fn()}
  />);

  const bubble = screen.getByText(longPath).closest('.user-bubble');
  expect(bubble).not.toBeNull();
  const style = getComputedStyle(bubble as Element);
  expect(style.minWidth).toBe('0px');
  expect(style.overflowWrap).toBe('anywhere');
  expect(style.wordBreak).toBe('break-word');
});
```

Import `../styles.css` so the assertion exercises the real application stylesheet.

- [ ] **Step 2: Run the test and verify the red state**

Run:

```bash
npm test -- src/components/ChatArea.test.tsx
```

Expected: the new test fails because the current `.user-bubble` computed styles do not include the containment rules.

- [ ] **Step 3: Implement the minimal CSS fix**

Update the existing `.user-bubble` rule in `src/styles.css`:

```css
.user-bubble {
  min-width: 0;
  overflow-wrap: anywhere;
  word-break: break-word;
}
```

Keep the existing width, max-width, spacing, padding, radius, background, and line-height declarations unchanged.

- [ ] **Step 4: Verify the green state and frontend build**

Run:

```bash
npm test -- src/components/ChatArea.test.tsx
npm run build
```

Expected: the focused tests pass and the TypeScript/Vite build exits with status 0.

- [ ] **Step 5: Inspect scope and commit**

Run:

```bash
git diff --check
git status --short
git diff -- src/components/ChatArea.test.tsx src/styles.css
```

Commit only the test and CSS fix:

```bash
git add src/components/ChatArea.test.tsx src/styles.css
git commit -m "fix: wrap long user messages"
```
