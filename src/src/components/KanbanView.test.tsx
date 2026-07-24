import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/events/stream')) {
        return new Response('id: 0\nevent: connected\ndata: {"cursor":0}\n\n', {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      if (path.includes('/kanban/boards/') && path.includes('/tasks/')) {
        const requested = init?.body ? JSON.parse(String(init.body)) as { status?: string } : {};
        if (path.endsWith('/move') && requested.status === 'backlog') {
          return new Response(JSON.stringify({
            success: false,
            message: 'active tasks cannot be returned to Backlog',
            error: { code: 'invalid_transition' },
            status_code: 409,
          }), { status: 409, headers: { 'Content-Type': 'application/json' } });
        }
        const archived = path.endsWith('/archive');
        const providerTask = path.includes('/provider-rollout/');
        return new Response(JSON.stringify({ success: true, data: {
          id: providerTask ? 'p-201' : 't-1042',
          title: providerTask ? 'Verify provider callback' : 'Prepare the weekly report',
          description: providerTask ? 'Check the callback.' : 'Prepare the report.',
          status: archived ? 'archived' : 'running',
          priority: 'high',
          assignee: providerTask ? 'provider-agent' : 'research-agent',
          assignees: [providerTask ? 'provider-agent' : 'research-agent'],
          parents: [],
          tags: providerTask ? [] : ['report'],
          progress: archived ? 100 : 50,
          updated_at: new Date().toISOString(),
        } }), { status: 200 });
      }
      if (path.includes('/kanban/boards')) {
        return new Response(JSON.stringify({ success: true, data: [
          { id: 'default', name: 'Task board', description: 'Real API fixture', color: '#4f8cff', tasks: [
            { id: 't-1042', title: 'Prepare the weekly report', description: 'Prepare the report.', status: 'running', priority: 'high', assignee: 'research-agent', assignees: ['research-agent'], parents: [], tags: ['report'], progress: 50, updated_at: new Date().toISOString() },
            { id: 't-1051', title: 'Draft the report template', description: 'Draft a template.', status: 'todo', priority: 'low', assignee: 'research-agent', assignees: ['research-agent'], parents: [], tags: ['docs'], progress: 0, updated_at: new Date().toISOString() },
          ] },
          { id: 'provider-rollout', name: 'Provider rollout', description: 'Provider checks', color: '#34d399', tasks: [
            { id: 'p-201', title: 'Verify provider callback', description: 'Check the callback.', status: 'running', priority: 'high', assignee: 'provider-agent', assignees: ['provider-agent'], parents: [], tags: [], progress: 50, updated_at: new Date().toISOString() },
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
    expect(within(drawer).getByText('Running', { selector: '.kb-column-state' })).toBeVisible();
    expect(within(drawer).getByText('Running', { selector: '.kb-substate' })).toBeVisible();
  });

  it('shows five columns and confirms before dropping a task into Archived', async () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard />);

    const card = await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    expect(screen.getAllByRole('region', { name: / column$/ })).toHaveLength(5);
    const archiveColumn = screen.getByRole('region', { name: 'Archived column' });
    const dataTransfer = {
      effectAllowed: 'move',
      dropEffect: 'move',
      setData: vi.fn(),
      getData: vi.fn(() => 't-1042'),
    };

    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.drop(archiveColumn, { dataTransfer });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/archive'))).toBe(false);

    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.drop(archiveColumn, { dataTransfer });
    expect(confirm).toHaveBeenCalledTimes(2);
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/archive'))).toBe(true));
    expect(await screen.findByRole('status')).toHaveTextContent('Moved “Prepare the weekly report” to Archived.');
  });

  it('restores a conflicting move and shows the clean API message', async () => {
    render(<TestBoard />);
    const card = await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    const backlogColumn = screen.getByRole('region', { name: 'Backlog column' });
    const dataTransfer = {
      effectAllowed: 'move',
      dropEffect: 'move',
      setData: vi.fn(),
      getData: vi.fn(() => 't-1042'),
    };

    fireEvent.dragStart(card, { dataTransfer });
    fireEvent.drop(backlogColumn, { dataTransfer });

    expect(await screen.findByRole('alert')).toHaveTextContent('active tasks cannot be returned to Backlog');
    expect(screen.getByRole('button', { name: 'Open t-1042: Prepare the weekly report' })).toBeVisible();
  });
});
