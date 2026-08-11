import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from '../hooks/useKanban';
import { streamStore } from '../chat/streamStore';
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

let taskDetailSkills: string[] = [];

function TestBoard({
  agents = [],
  teams = [],
  onLoadAgentSkills,
  onNavigate,
  onOpenChat,
}: {
  agents?: Agent[];
  teams?: Team[];
  onLoadAgentSkills?: (agentId: string) => Promise<Agent['skills']>;
  onNavigate?: (taskId: string, agentId?: string, conversationId?: string) => void;
  onOpenChat?: (agentId: string, conversationId: string) => void;
}) {
  const state = useKanban();
  return (
    <KanbanView
      teams={teams}
      agents={agents}
      state={state}
      onLoadAgentSkills={onLoadAgentSkills}
      onNavigate={onNavigate}
      onOpenChat={onOpenChat}
      onClose={vi.fn()}
    />
  );
}

describe('KanbanView', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  beforeEach(() => {
    window.localStorage.clear();
    taskDetailSkills = [];
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
      if (path.endsWith('/kanban/boards') && init?.method === 'POST') {
        const requested = JSON.parse(String(init.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({ success: true, data: {
          id: requested.slug,
          ...requested,
          tasks: [],
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
          skills: taskDetailSkills,
          tags: providerTask ? [] : ['report'],
          progress: archived ? 100 : 50,
          conversation: providerTask || archived ? null : {
            id: '20260727_140600_abcdef',
            agent_id: 'research-agent',
            url: '/agents/research-agent/sessions/20260727_140600_abcdef',
          },
          runs: providerTask ? [] : [{
            id: 1,
            profile: 'research-agent',
            status: 'completed',
            outcome: 'completed',
            summary: 'Prepared the report.',
            started_at: '2026-07-31T05:12:00Z',
            ended_at: '2026-07-31T05:13:00Z',
          }],
          updated_at: new Date().toISOString(),
        } }), { status: 200 });
      }
      if (path.includes('/kanban/boards')) {
        return new Response(JSON.stringify({ success: true, data: [
          { id: 'default', name: 'Task board', description: 'Real API fixture', color: '#4f8cff', tasks: [
            { id: 't-1042', title: 'Prepare the weekly report', description: 'Prepare the report.', status: 'running', priority: 'high', assignee: 'research-agent', assignees: ['research-agent'], parents: [], tags: ['report'], progress: 50, updated_at: new Date().toISOString() },
            { id: 't-1051', title: 'Draft the report template', description: 'Draft a template.', status: 'todo', priority: 'low', assignee: 'research-agent', assignees: ['research-agent'], parents: [], tags: ['docs'], progress: 0, updated_at: new Date().toISOString() },
            { id: 't-blocked', title: 'Compute 1 + 1', description: 'Needs user input.', status: 'blocked', kanban_status: 'done', allowed_kanban_statuses: ['todo', 'archived'], state_detail: { kind: 'needs_input', label: 'Needs input', reason: 'Confirm the expected answer.' }, priority: 'medium', assignee: null, assignees: [], parents: [], tags: [], progress: 0, updated_at: new Date().toISOString() },
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
    await user.click(boardPicker);
    const options = await screen.findByRole('listbox', { name: 'Task boards' });
    expect(within(options).getAllByRole('option')).toHaveLength(3);
    await user.click(within(options).getByRole('option', { name: /Provider rollout/ }));

    expect(screen.getByRole('button', { name: 'Open p-201: Verify provider callback' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Open t-1042: Prepare the weekly report' })).toBeNull();
  });

  it('creates and selects a new task board from the board menu', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard />);

    await user.click(await screen.findByLabelText('Select task board'));
    await user.click(screen.getByRole('button', { name: /Create board/ }));

    const dialog = screen.getByRole('dialog', { name: 'Create task board' });
    await user.type(within(dialog).getByLabelText('Board name'), 'Launch Planning');
    expect(within(dialog).getByLabelText('Board ID')).toHaveValue('launch-planning');
    await user.type(within(dialog).getByLabelText(/Description/), 'Coordinate the product launch.');
    await user.click(within(dialog).getByRole('button', { name: 'Create board' }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) => {
      if (!String(input).endsWith('/kanban/boards') || init?.method !== 'POST') return false;
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      return body.slug === 'launch-planning' && body.name === 'Launch Planning';
    })).toBe(true));
    expect(await screen.findByRole('status')).toHaveTextContent('Created “Launch Planning”.');
    expect(screen.getByText('board/launch-planning')).toBeVisible();
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
    expect(within(drawer).getByText('In Progress', { selector: '.kb-substate' })).toBeVisible();
    expect(drawer.querySelector('.kb-column-state')).toBeNull();
  });

  it('shows a compact skill preview and expands the full task skill list on demand', async () => {
    taskDetailSkills = Array.from({ length: 12 }, (_, index) => `skill-${index + 1}`);
    const user = userEvent.setup();
    render(<TestBoard />);

    await user.click(await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' }));
    const drawer = screen.getByRole('dialog');

    expect(within(drawer).getByText('skill-8')).toBeVisible();
    expect(within(drawer).queryByText('skill-9')).toBeNull();
    await user.click(within(drawer).getByRole('button', { name: 'Show 4 more skills' }));
    expect(within(drawer).getByText('skill-12')).toBeVisible();
    expect(within(drawer).getByRole('button', { name: 'Show fewer skills' })).toBeVisible();
  });

  it('uses kanban_status for placement and native status as the only visible label', async () => {
    const user = userEvent.setup();
    render(<TestBoard />);

    const doneColumn = await screen.findByRole('region', { name: 'Done column' });
    const blockedCard = within(doneColumn).getByRole('button', { name: 'Open t-blocked: Compute 1 + 1' });
    expect(within(blockedCard).getByText('Blocked', { selector: '.kb-substate' })).toBeVisible();
    expect(within(blockedCard).queryByText('Done')).toBeNull();
    expect(screen.queryByText('Blocked (Error)')).toBeNull();

    await user.click(screen.getByRole('tab', { name: /List/ }));
    const blockedRow = screen.getByRole('button', { name: /Compute 1 \+ 1.*Blocked/ });
    expect(within(blockedRow).getByText('Blocked', { selector: '.kb-substate' })).toBeVisible();
    expect(screen.getByText(/grouped by Kanban status/)).toBeVisible();
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
    const sendSpy = vi.spyOn(streamStore, 'send').mockImplementation(() => undefined);
    const onOpenChat = vi.fn();
    render(<TestBoard agents={[researchAgent]} onOpenChat={onOpenChat} />);

    await user.click(await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' }));
    const drawer = screen.getByRole('dialog');
    await waitFor(() => expect(within(drawer).getByText('Session tracking')).toBeVisible());
    expect(within(drawer).getByText('Research Agent', { selector: '.kb-run-agent-name' })).toBeVisible();
    const runAgentTooltip = within(drawer).getByRole('tooltip');
    expect(runAgentTooltip).toHaveTextContent('Agent ID: research-agent');
    expect(runAgentTooltip).toHaveTextContent('Finds and writes useful information');
    expect(within(drawer).queryByRole('link', {
      name: /agents\/research-agent\/sessions\/20260727_140600_abcdef/,
    })).not.toBeInTheDocument();

    await user.click(within(drawer).getByRole('button', { name: 'View session' }));
    const sessionDialog = screen.getByRole('dialog', { name: 'Prepare the weekly report' });
    expect(sessionDialog).toBeVisible();
    expect(within(sessionDialog).getByPlaceholderText('Message Research Agent…')).toBeEnabled();
    expect(within(sessionDialog).getByRole('button', { name: 'Upload files' })).toBeEnabled();
    await user.click(within(sessionDialog).getByRole('button', { name: 'View session' }));
    expect(onOpenChat).toHaveBeenCalledWith('research-agent', '20260727_140600_abcdef');
    await waitFor(() => {
      expect(fetchMock.mock.calls.filter(([input]) => String(input).includes(
        '/sessions/20260727_140600_abcdef/messages?agent=research-agent',
      ))).toHaveLength(1);
      expect(fetchMock.mock.calls.filter(([input]) => String(input).includes(
        '/sessions/20260727_140600_abcdef/usage?agent=research-agent',
      ))).toHaveLength(1);
    });
    await user.type(within(sessionDialog).getByPlaceholderText('Message Research Agent…'), 'Continue from the task board');
    await user.click(within(sessionDialog).getByRole('button', { name: 'Send message to Research Agent' }));
    expect(sendSpy).toHaveBeenCalledWith(
      'research-agent',
      '20260727_140600_abcdef',
      'Continue from the task board',
      researchAgent.model,
    );
    await user.click(screen.getByRole('button', { name: 'Close task session' }));
    await user.click(within(drawer).getByRole('button', { name: 'View session' }));
    expect(fetchMock.mock.calls.filter(([input]) => String(input).includes(
      '/sessions/20260727_140600_abcdef/messages?agent=research-agent',
    ))).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Close task session' }));

    await user.click(within(drawer).getByRole('button', { name: 'Cancel task' }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([input, init]) =>
      String(input).endsWith('/tasks/t-1042/cancel') && init?.method === 'POST'
    )).toBe(true));
  });

  it('shows enabled and disabled agent skills, selecting only enabled skills by default', async () => {
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
    const disabledSkill = within(modal).getByRole('checkbox', { name: /Disabled skill/ });
    expect(writing).toBeChecked();
    expect(webResearch).toBeChecked();
    expect(disabledSkill).not.toBeChecked();
    expect(modal.querySelector('.kb-skill-picker-head > span')).toHaveTextContent('2 of 3 enabled');
    expect(within(modal).getByText('disabled', { selector: '.kb-skill-name em' })).toBeVisible();

    await user.click(webResearch);
    await user.click(disabledSkill);
    await user.type(within(modal).getByLabelText('Title'), 'Create a short brief');
    await user.type(within(modal).getByLabelText(/Description/), 'Write a concise brief for the user.');
    await user.click(within(modal).getByRole('button', { name: /Create task/ }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith('/kanban/boards/default/tasks') && init?.method === 'POST'
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.[1]?.body)).skills).toEqual(['writing', 'disabled-skill']);
    });
  });

  it('filters task skills by name without changing their selected state', async () => {
    const user = userEvent.setup();
    render(<TestBoard agents={[researchAgent]} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog');
    await user.click(within(modal).getByRole('button', { name: /Unassigned/ }));
    await user.click(within(modal).getByRole('option', { name: /Research Agent/ }));

    const search = within(modal).getByRole('searchbox', { name: 'Search skills by name' });
    await user.type(search, 'web');
    expect(within(modal).getByRole('checkbox', { name: /Web research/ })).toBeChecked();
    expect(within(modal).queryByRole('checkbox', { name: /Writing/ })).toBeNull();

    await user.clear(search);
    expect(within(modal).getByRole('checkbox', { name: /Writing/ })).toBeChecked();
  });

  it('hydrates skills when the agent list was loaded without skill details', async () => {
    const user = userEvent.setup();
    const loadAgentSkills = vi.fn().mockResolvedValue(researchAgent.skills);
    const agentWithoutSkills = { ...researchAgent, skills: [] };
    render(<TestBoard agents={[agentWithoutSkills]} onLoadAgentSkills={loadAgentSkills} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog');
    await user.click(within(modal).getByRole('button', { name: /Unassigned/ }));
    await user.click(within(modal).getByRole('option', { name: /Research Agent/ }));

    await waitFor(() => {
      expect(loadAgentSkills).toHaveBeenCalledWith('research-agent');
      expect(within(modal).getByRole('checkbox', { name: /Writing/ })).toBeChecked();
      expect(within(modal).getByRole('checkbox', { name: /Disabled skill/ })).not.toBeChecked();
    });
  });

  it('submits every enabled skill when an agent has more than 64', async () => {
    const skills = Array.from({ length: 85 }, (_, index) => ({
      skill_id: `skill-${index}`,
      name: `Skill ${index}`,
      category: 'other',
      description: '',
      enabled: true,
      installed: true,
      path: '',
    }));
    const agent = { ...researchAgent, skills } as Agent;
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard agents={[agent]} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog', { name: 'New task' });
    await user.click(within(modal).getByRole('button', { name: /Unassigned/ }));
    await user.click(within(modal).getByRole('option', { name: /Research Agent/ }));
    await user.type(within(modal).getByLabelText('Title'), 'Task using every skill');
    await user.type(within(modal).getByLabelText(/Description/), 'Run with the complete agent skill set.');
    await user.click(within(modal).getByRole('button', { name: /Create task/ }));

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith('/kanban/boards/default/tasks') && init?.method === 'POST');
      expect(request).toBeDefined();
      const body = JSON.parse(String(request?.[1]?.body)) as { skills: string[] };
      expect(body.skills).toHaveLength(85);
      expect(body.skills).toEqual(skills.map((skill) => skill.skill_id));
    });
  });

  it('selects a saved team, previews its DAG, and creates a grouped team task', async () => {
    const user = userEvent.setup();
    const fetchMock = vi.mocked(fetch);
    render(<TestBoard agents={[researchAgent]} teams={[launchTeam]} />);

    await screen.findByRole('button', { name: 'Open t-1042: Prepare the weekly report' });
    await user.click(screen.getByRole('button', { name: /New task/ }));
    const modal = screen.getByRole('dialog');
    await user.selectOptions(within(modal).getByLabelText('Status'), 'backlog');
    await user.click(within(modal).getByRole('button', { name: 'Agent team' }));
    expect(within(modal).getByLabelText('Status')).toHaveValue('backlog');
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
      expect(body.status).toBe('backlog');
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
