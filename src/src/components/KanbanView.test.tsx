import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from '../hooks/useKanban';
import type { Agent } from '../types';
import { KanbanView } from './KanbanView';

const researchAgent: Agent = {
  id: 'research-agent',
  title: 'Research Agent',
  name: 'research-agent',
  description: 'Finds and writes useful information',
  status: 'active',
  provider: 'nine-router',
  model: 'auto',
  reasoningEffort: 'medium',
  approvalMode: 'auto',
  skillsWriteApproval: true,
  memoryWriteApproval: true,
  workspace: '',
  conversations: [],
  skills: [
    { skill_id: 'writing', name: 'Writing', category: 'office', description: 'Draft clear documents', enabled: true, installed: true, path: '' },
    { skill_id: 'web-research', name: 'Web research', category: 'research', description: 'Research information online', enabled: true, installed: true, path: '' },
    { skill_id: 'disabled-skill', name: 'Disabled skill', category: 'other', description: '', enabled: false, installed: true, path: '' },
  ],
};

function TestBoard({ agents = [] }: { agents?: Agent[] }) {
  const state = useKanban();
  return <KanbanView agents={agents} state={state} onClose={vi.fn()} />;
}

describe('KanbanView', () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.clear();
    let archivedTask = false;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input);
      if (path.endsWith('/events/stream')) {
        return new Response('id: 0\nevent: connected\ndata: {"cursor":0}\n\n', {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        });
      }
      if (path.endsWith('/kanban/boards/default/tasks') && init?.method === 'POST') {
        const requested = JSON.parse(String(init.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({ success: true, data: {
          id: 't-created',
          ...requested,
          status: requested.status === 'backlog' ? 'triage' : 'todo',
          kanban_status: requested.status,
          assignees: requested.assignee ? [requested.assignee] : [],
          parents: [],
          tags: [],
          progress: 0,
          updated_at: new Date().toISOString(),
        } }), { status: 201, headers: { 'Content-Type': 'application/json' } });
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
        const archived = path.endsWith('/archive') || (archivedTask && path.includes('/t-1042'));
        if (path.endsWith('/archive')) archivedTask = true;
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
    expect(within(drawer).getByText('Only valid next steps are enabled.')).toBeVisible();
    expect(within(drawer).getByText('In Progress', { selector: '.kb-column-state' })).toBeVisible();
    expect(within(drawer).getByText('In Progress', { selector: '.kb-substate' })).toBeVisible();
  });

  it('shows four current columns and archives through the matching confirmation dialog', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard />);

    const card = await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    expect(screen.getAllByRole('region', { name: / column$/ })).toHaveLength(4);
    expect(screen.queryByRole('region', { name: 'Archived column' })).toBeNull();

    await user.click(card);
    await user.click(screen.getByRole('button', { name: 'Archive task' }));
    const confirm = screen.getByRole('alertdialog', { name: 'Archive this task?' });
    expect(confirm).toHaveTextContent('remain available in the Archived tab');
    await user.click(within(confirm).getByRole('button', { name: 'Keep task' }));
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/archive'))).toBe(false);

    await user.click(screen.getByRole('button', { name: 'Archive task' }));
    await user.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Archive task' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/archive'))).toBe(true));
    expect(await screen.findByRole('status')).toHaveTextContent('Moved “Prepare the weekly report” to Archived.');

    await user.click(screen.getByRole('tab', { name: /Archived/ }));
    expect(screen.getByRole('button', { name: 'Open t-1042: Prepare the weekly report' })).toBeVisible();
  });

  it('disables invalid backward moves for an active task', async () => {
    const fetchMock = vi.mocked(fetch);
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

    expect(screen.getByRole('button', { name: 'Open t-1042: Prepare the weekly report' })).toBeVisible();
    expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith('/move'))).toBe(false);

    fireEvent.click(card);
    const moveSelect = screen.getByLabelText('Move task to');
    expect(within(moveSelect).getByRole('option', { name: 'Backlog' })).toBeDisabled();
    expect(within(moveSelect).getByRole('option', { name: 'Todo' })).toBeDisabled();
    expect(within(moveSelect).getByRole('option', { name: 'Done' })).toBeEnabled();
  });

  it('enables every enabled agent skill by default and sends unchecked selections', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard agents={[researchAgent]} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog');
    await user.click(within(modal).getByRole('button', { name: /Unassigned/ }));
    await user.click(within(modal).getByRole('option', { name: /Research Agent/ }));

    const writing = within(modal).getByRole('checkbox', { name: /Writing/ });
    const webResearch = within(modal).getByRole('checkbox', { name: /Web research/ });
    expect(writing).toBeChecked();
    expect(webResearch).toBeChecked();
    expect(within(modal).queryByText('Disabled skill')).toBeNull();

    await user.click(webResearch);
    await user.type(within(modal).getByLabelText('Title'), 'Create a short brief');
    await user.type(within(modal).getByLabelText(/Description/), 'Write a concise brief for the user.');
    await user.click(within(modal).getByRole('button', { name: /Create task/ }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith('/kanban/boards/default/tasks') && init?.method === 'POST'
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.[1]?.body)).skills).toEqual(['writing']);
    });
  });
});
