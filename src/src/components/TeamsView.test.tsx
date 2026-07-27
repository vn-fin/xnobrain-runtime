import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Team } from '../api/teams';
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
});
