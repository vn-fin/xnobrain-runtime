import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Team, TeamRunRecord } from '../api/teams';
import type { useTeams } from '../hooks/useTeams';
import type { Agent } from '../types';
import { TeamsView } from './TeamsView';

const agents: Agent[] = [
  {
    id: 'lead',
    title: 'Team Lead',
    name: 'Team Lead',
    description: 'Coordinates the final answer',
    status: 'active',
    provider: 'openai',
    model: 'model',
    reasoningEffort: 'medium',
    approvalMode: 'auto',
    skillsWriteApproval: true,
    memoryWriteApproval: true,
    workspace: '',
    skills: [],
    conversations: [],
  },
  {
    id: 'researcher',
    title: 'Researcher',
    name: 'Researcher',
    description: 'Finds source material',
    status: 'active',
    provider: 'openai',
    model: 'model',
    reasoningEffort: 'medium',
    approvalMode: 'auto',
    skillsWriteApproval: true,
    memoryWriteApproval: true,
    workspace: '',
    skills: [],
    conversations: [],
  },
  {
    id: 'reviewer',
    title: 'Reviewer',
    name: 'Reviewer',
    description: 'Checks the findings',
    status: 'active',
    provider: 'openai',
    model: 'model',
    reasoningEffort: 'medium',
    approvalMode: 'auto',
    skillsWriteApproval: true,
    memoryWriteApproval: true,
    workspace: '',
    skills: [],
    conversations: [],
  },
];

function teamState(overrides: Partial<ReturnType<typeof useTeams>> = {}) {
  return {
    teams: [],
    status: 'ready',
    pending: false,
    error: '',
    runs: [],
    activeRun: undefined,
    runsStatus: 'idle',
    refresh: vi.fn(),
    create: vi.fn(),
    remove: vi.fn(),
    loadRuns: vi.fn(),
    openRun: vi.fn(),
    startRun: vi.fn(),
    cancelRun: vi.fn(),
    setActiveRun: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useTeams>;
}

describe('TeamsView', () => {
  it('builds a connected DAG by clicking agent cards and node ports', async () => {
    const create = vi.fn(async (input) => ({ id: 'team-1', ...input }));
    const state = teamState({ create });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /Create team/i }));
    fireEvent.change(screen.getByPlaceholderText('Product launch team'), { target: { value: 'Launch team' } });
    fireEvent.click(screen.getByRole('button', { name: /Researcher.*Finds source material/i }));
    fireEvent.click(screen.getByRole('button', { name: /Reviewer.*Checks the findings/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect from researcher' }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect to reviewer' }));
    fireEvent.click(screen.getByRole('button', { name: /Save team/i }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const input = create.mock.calls[0][0];
    expect(input.members.map((member) => member.agent_id)).toEqual(['researcher', 'reviewer']);
    expect(input.workflow).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: 'researcher', needs: [] }),
      expect.objectContaining({ id: 'reviewer', needs: ['researcher'] }),
    ]));
  });

  it('runs the workflow saved with the selected team', async () => {
    const team: Team = {
      id: 'team-1',
      name: 'Launch team',
      orchestrator_id: 'lead',
      members: [
        { agent_id: 'researcher', role: 'researcher', allowed_tools: ['web'], enabled: true },
        { agent_id: 'reviewer', role: 'reviewer', allowed_tools: ['web'], enabled: true },
      ],
      workflow: [
        { id: 'research', task: 'Research.', agent_id: 'researcher', role: 'researcher', needs: [] },
        { id: 'review', task: 'Review.', agent_id: 'reviewer', role: 'reviewer', needs: ['research'] },
      ],
      shared_workspace: false,
      max_parallel: 2,
      max_depth: 1,
      enabled: true,
    };
    const startRun = vi.fn(async () => undefined as never);
    const state = teamState({ teams: [team], startRun });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.change(screen.getByPlaceholderText(/Research the market/i), { target: { value: 'Compare launch plans' } });
    fireEvent.click(screen.getByRole('button', { name: /Run team/i }));

    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(1));
    expect(startRun).toHaveBeenCalledWith(
      'team-1',
      'Compare launch plans',
      expect.arrayContaining([
        expect.objectContaining({ id: 'review', needs: ['research'], task: expect.stringContaining('Team objective: Compare launch plans') }),
      ]),
    );
  });

  it('highlights active graph nodes, reveals returned output, and cancels the run', async () => {
    const team: Team = {
      id: 'team-1',
      name: 'Launch team',
      orchestrator_id: 'lead',
      members: [
        { agent_id: 'researcher', role: 'researcher', allowed_tools: ['web'], enabled: true },
        { agent_id: 'reviewer', role: 'reviewer', allowed_tools: ['web'], enabled: true },
      ],
      workflow: [],
      shared_workspace: false,
      max_parallel: 2,
      max_depth: 1,
      enabled: true,
    };
    const run: TeamRunRecord = {
      id: 'tr_live1234',
      team_id: team.id,
      status: 'running',
      error: null,
      mode: 'async',
      task: 'Compare launch plans',
      synthesis_instruction: 'Synthesize.',
      orchestrator_id: 'lead',
      orchestrator_summary: '',
      created_at: '2026-07-27T04:35:29Z',
      started_at: '2026-07-27T04:35:29Z',
      ended_at: null,
      updated_at: '2026-07-27T04:35:42Z',
      revision: 4,
      steps: [
        {
          id: 'research',
          agent_id: 'researcher',
          role: 'researcher',
          task: 'Research.',
          needs: [],
          allowed_tools: ['web'],
          status: 'completed',
          summary: 'Research found three viable launch plans.',
          summary_chars: 39,
          error: null,
          conversation_id: 'conversation-1',
          started_at: '2026-07-27T04:35:29Z',
          ended_at: '2026-07-27T04:35:42Z',
        },
        {
          id: 'review',
          agent_id: 'reviewer',
          role: 'reviewer',
          task: 'Review.',
          needs: ['research'],
          allowed_tools: ['web'],
          status: 'running',
          summary: '',
          summary_chars: 0,
          error: null,
          conversation_id: null,
          started_at: '2026-07-27T04:35:42Z',
          ended_at: null,
        },
      ],
    };
    const cancelRun = vi.fn(async () => undefined);
    const state = teamState({ teams: [team], runs: [run], activeRun: run, runsStatus: 'ready', cancelRun });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    expect(await screen.findByRole('button', { name: 'Reviewer: running' })).toBeInTheDocument();
    expect(screen.getByText('Working now')).toBeInTheDocument();
    expect(screen.getByText('1 active now')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Researcher: completed' }));
    expect(screen.getByText('Research found three viable launch plans.')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(cancelRun).toHaveBeenCalledWith('team-1', 'tr_live1234'));
  });
});
