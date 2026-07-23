import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from '../hooks/useKanban';
import { KanbanView } from './KanbanView';

function TestBoard() {
  const state = useKanban();
  return <KanbanView agents={[]} state={state} onClose={vi.fn()} />;
}

describe('KanbanView', () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.clear();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('/kanban/boards/provider-rollout/tasks/')) {
        return new Response(JSON.stringify({ success: true, data: { id: 'p-201', title: 'Verify provider callback', description: 'Check the callback.', status: 'in_progress', priority: 'high', assignee: 'provider-agent', assignees: ['provider-agent'], parents: [], tags: [], progress: 50, updated_at: new Date().toISOString() } }), { status: 200 });
      }
      if (path.includes('/kanban/boards')) {
        return new Response(JSON.stringify({ success: true, data: [
          { id: 'default', name: 'Task board', description: 'Real API fixture', color: '#4f8cff', tasks: [
            { id: 't-1042', title: 'Prepare the weekly report', description: 'Prepare the report.', status: 'in_progress', priority: 'high', assignee: 'research-agent', assignees: ['research-agent'], parents: [], tags: ['report'], progress: 50, updated_at: new Date().toISOString() },
            { id: 't-1051', title: 'Draft the report template', description: 'Draft a template.', status: 'todo', priority: 'low', assignee: 'research-agent', assignees: ['research-agent'], parents: [], tags: ['docs'], progress: 0, updated_at: new Date().toISOString() },
          ] },
          { id: 'provider-rollout', name: 'Provider rollout', description: 'Provider checks', color: '#34d399', tasks: [
            { id: 'p-201', title: 'Verify provider callback', description: 'Check the callback.', status: 'in_progress', priority: 'high', assignee: 'provider-agent', assignees: ['provider-agent'], parents: [], tags: [], progress: 50, updated_at: new Date().toISOString() },
          ] },
          { id: 'release-readiness', name: 'Release readiness', description: 'Release checks', color: '#c084fc', tasks: [] },
        ] }), { status: 200 });
      }
      return new Response(JSON.stringify({ success: true, data: [] }), { status: 200 });
    }));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('shows the available boards and switches the active task list', async () => {
    const user = userEvent.setup();
    render(<TestBoard />);

    const boardPicker = await screen.findByLabelText('Select task board');
    await waitFor(() => expect(boardPicker.querySelectorAll('option')).toHaveLength(3));

    await user.selectOptions(boardPicker, 'provider-rollout');

    expect(screen.getByRole('button', { name: 'Open p-201: Verify provider callback' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Open t-1042: Prepare the weekly report' })).toBeNull();
  });

  it('filters tasks and keeps task details available in the board', async () => {
    const user = userEvent.setup();
    render(<TestBoard />);

    const designTask = await screen.findByRole('button', {
      name: 'Open t-1042: Prepare the weekly report',
    });
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'low');

    expect(designTask).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open t-1051: Draft the report template' })).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Clear' }));
    const restoredTask = screen.getByRole('button', {
      name: 'Open t-1042: Prepare the weekly report',
    });
    await user.click(restoredTask);

    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByRole('heading', { name: 'Prepare the weekly report' })).toBeVisible();
    expect(within(drawer).getByText('Update the task status without leaving the board.')).toBeVisible();
  });
});
