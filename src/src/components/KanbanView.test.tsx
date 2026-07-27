import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from '../hooks/useKanban';
import type { Team } from '../api/teams';
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

const launchTeam: Team = {
  id: 'launch-team',
  name: 'Launch Team',
  description: 'Researches evidence and reviews launch plans.',
  orchestrator_id: 'research-agent',
  members: [{ agent_id: 'research-agent', role: 'researcher', allowed_tools: ['web'], enabled: true }],
  workflow: [{ id: 'research', task: 'Research the launch', agent_id: 'research-agent', role: 'researcher', needs: [] }],
  shared_workspace: false,
  max_parallel: 2,
  max_depth: 4,
  enabled: true,
};

function TestBoard({ agents = [], teams = [] }: { agents?: Agent[]; teams?: Team[] }) {
  const state = useKanban();
  return <KanbanView teams={teams} agents={agents} state={state} onClose={vi.fn()} />;
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
        const requestedSchedule = requested.schedule as Record<string, unknown> | undefined;
        return new Response(JSON.stringify({ success: true, data: {
          id: 't-created',
          ...requested,
          status: requested.status === 'backlog' ? 'triage' : requested.status === 'scheduled' ? 'scheduled' : 'todo',
          kanban_status: requested.status === 'scheduled' ? 'todo' : requested.status,
          schedule: requestedSchedule ? {
            recurrence: requestedSchedule.recurrence,
            next_run_at: requestedSchedule.scheduled_at,
            interval_minutes: requestedSchedule.interval_minutes ?? null,
            timezone: requestedSchedule.timezone,
            enabled: true,
            occurrence_count: 0,
            last_run_at: null,
          } : null,
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
          conversation: providerTask || archived ? null : {
            id: '20260727_140600_abcdef',
            agent_id: 'research-agent',
            url: '/agents/research-agent/conversations/20260727_140600_abcdef',
          },
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

  it('shows conversation tracking in task details and cancels a running task', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard agents={[researchAgent]} />);

    await user.click(await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' }));
    const drawer = screen.getByRole('dialog');
    await waitFor(() => expect(within(drawer).getByText('Conversation tracking')).toBeVisible());
    expect(within(drawer).getByRole('link', {
      name: /agents\/research-agent\/conversations\/20260727_140600_abcdef/,
    })).toBeVisible();

    await user.click(within(drawer).getByRole('button', { name: 'View conversation' }));
    expect(screen.getByRole('dialog', { name: 'Prepare the weekly report' })).toBeVisible();
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([input]) => String(input).includes(
        '/conversations/20260727_140600_abcdef/messages?agent=research-agent',
      ))).toHaveLength(1);
      expect(fetchMock.mock.calls.filter(([input]) => String(input).includes(
        '/conversations/20260727_140600_abcdef/usage?agent=research-agent',
      ))).toHaveLength(1);
    });
    await user.click(screen.getByRole('button', { name: 'Close task conversation' }));
    await user.click(within(drawer).getByRole('button', { name: 'View conversation' }));
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes(
      '/conversations/20260727_140600_abcdef/messages?agent=research-agent',
    ))).toHaveLength(1);
    await user.click(screen.getByRole('button', { name: 'Close task conversation' }));

    await user.click(within(drawer).getByRole('button', { name: 'Cancel task' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith('/tasks/t-1042/cancel') && init?.method === 'POST'
    )).toBe(true));
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

  it('selects a saved team, previews its DAG, and creates a grouped team task', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard agents={[researchAgent]} teams={[launchTeam]} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog');
    await user.click(within(modal).getByRole('button', { name: 'Agent team' }));
    await user.click(within(modal).getByRole('button', { name: /Choose a saved team/ }));
    const teamOption = within(modal).getByRole('option', { name: /Launch Team.*Researches evidence and reviews launch plans/i });
    expect(teamOption).toBeVisible();
    await user.click(teamOption);
    expect(within(modal).getByText('Launch Team', { selector: '.kb-team-preview strong' })).toBeVisible();
    expect(within(modal).getByText(
      'Researches evidence and reviews launch plans.',
      { selector: '.kb-team-preview > p' },
    )).toBeVisible();
    expect(within(modal).getByText('Synthesis')).toBeVisible();

    await user.type(within(modal).getByLabelText('Title'), 'Prepare launch');
    await user.type(within(modal).getByLabelText(/Description/), 'Produce an evidence-backed launch plan.');
    await user.click(within(modal).getByRole('button', { name: /Create task/ }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith('/kanban/boards/default/tasks') && init?.method === 'POST'
      );
      const body = JSON.parse(String(request?.[1]?.body));
      expect(body.team_id).toBe('launch-team');
      expect(body.assignee).toBeNull();
      expect(body.skills).toEqual([]);
    });
  });

  it('creates a scheduled task with a visible database-backed run time', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard agents={[researchAgent]} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog');
    await user.selectOptions(within(modal).getByLabelText('Status'), 'scheduled');
    expect(within(modal).getByText('Run later')).toBeVisible();
    expect(within(modal).getByLabelText('First run')).toHaveValue();

    await user.type(within(modal).getByLabelText('Title'), 'Run the scheduled audit');
    await user.type(
      within(modal).getByLabelText(/Description/),
      'Inspect the account and report any problems.',
    );
    await user.click(within(modal).getByRole('button', { name: /Create task/ }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith('/kanban/boards/default/tasks')
        && init?.method === 'POST'
        && JSON.parse(String(init.body)).status === 'scheduled'
      );
      expect(request).toBeDefined();
      const body = JSON.parse(String(request?.[1]?.body));
      expect(body.schedule.recurrence).toBe('once');
      expect(body.schedule.scheduled_at).toBeTruthy();
      expect(body.schedule.timezone).toBeTruthy();
    });
    expect(screen.getByText(/Next:/)).toBeVisible();
  });
});
