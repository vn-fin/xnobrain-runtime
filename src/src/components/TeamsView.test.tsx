import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { conversationsApi } from '../api/conversations';
import type { Team, TeamRunRecord } from '../api/teams';
import { systemApi } from '../features/system/api';
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
    deleteRun: vi.fn(),
    startRun: vi.fn(),
    cancelRun: vi.fn(),
    setActiveRun: vi.fn(),
    ...overrides,
  } as unknown as ReturnType<typeof useTeams>;
}

describe('TeamsView', () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('builds a connected DAG by clicking agent cards and node ports', async () => {
    const create = vi.fn(async (input) => ({ id: 'team-1', ...input }));
    const state = teamState({ create });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /Create team/i }));
    fireEvent.change(screen.getByPlaceholderText('Product launch team'), { target: { value: 'Launch team' } });
    fireEvent.change(screen.getByPlaceholderText('Describe what this team is best at…'), {
      target: { value: 'Researches and reviews product launches.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Researcher.*Finds source material/i }));
    fireEvent.click(screen.getByRole('button', { name: /Reviewer.*Checks the findings/i }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect from researcher' }));
    fireEvent.click(screen.getByRole('button', { name: 'Connect to reviewer' }));
    fireEvent.click(screen.getByRole('button', { name: /Save team/i }));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    const input = create.mock.calls[0][0];
    expect(input.description).toBe('Researches and reviews product launches.');
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

    expect(screen.getByLabelText('Live team workflow')).toBeVisible();
    expect(screen.queryByText('Agent team', { exact: true })).not.toBeInTheDocument();
    const composer = screen.getByRole('textbox', { name: 'Team objective' });
    fireEvent.change(composer, { target: { value: 'Compare launch plans' } });
    fireEvent.keyDown(composer, { key: 'Enter' });

    await waitFor(() => expect(startRun).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('Run this team')).not.toBeInTheDocument();
    expect(startRun).toHaveBeenCalledWith(
      'team-1',
      'Compare launch plans',
      expect.arrayContaining([
        expect.objectContaining({ id: 'review', needs: ['research'], task: expect.stringContaining('Team objective: Compare launch plans') }),
      ]),
    );
  });

  it('renames and removes a Team through the overflow menu with confirmation', async () => {
    const team: Team = {
      id: 'team-menu',
      name: 'Menu Team',
      description: 'A Team managed from its overflow menu.',
      orchestrator_id: 'lead',
      members: [{ agent_id: 'researcher', role: 'researcher', allowed_tools: ['web'], enabled: true }],
      workflow: [],
      shared_workspace: false,
      max_parallel: 1,
      max_depth: 1,
      enabled: true,
    };
    const rename = vi.fn(async () => ({ ...team, name: 'Renamed Team' }));
    const remove = vi.fn(async () => undefined);
    const state = teamState({ teams: [team], rename, remove });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Menu Team options' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Rename' }));
    const renameInput = screen.getByRole('textbox', { name: 'Rename Menu Team' });
    fireEvent.change(renameInput, { target: { value: 'Renamed Team' } });
    fireEvent.keyDown(renameInput, { key: 'Enter' });
    await waitFor(() => expect(rename).toHaveBeenCalledWith('team-menu', 'Renamed Team'));

    fireEvent.click(screen.getByRole('button', { name: 'Menu Team options' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Remove' }));
    expect(screen.getByText('Remove this Team?')).toBeVisible();
    expect(screen.getByText(/assistants in this Team will not be deleted/i)).toBeVisible();
    expect(remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Remove Team' }));
    await waitFor(() => expect(remove).toHaveBeenCalledWith('team-menu'));
  });

  it('exports a Team snapshot and offers creation from an exported snapshot', async () => {
    const team: Team = {
      id: 'team-export',
      name: 'Export Team',
      description: 'A portable Team.',
      orchestrator_id: 'lead',
      members: [{ agent_id: 'reviewer', role: 'reviewer', allowed_tools: ['web'], enabled: true }],
      workflow: [],
      shared_workspace: false,
      max_parallel: 1,
      max_depth: 1,
      enabled: true,
    };
    const exportSnapshot = vi.spyOn(systemApi, 'export').mockResolvedValue({
      blob: new Blob(['team'], { type: 'application/zip' }),
      filename: 'team.zip',
    });
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:team'),
      revokeObjectURL: vi.fn(),
    });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    const state = teamState({ teams: [team] });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Export Team options' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Export' }));
    await waitFor(() => expect(exportSnapshot).toHaveBeenCalledWith([], undefined, ['team-export']));

    fireEvent.click(screen.getByRole('button', { name: /Create from snapshot/i }));
    expect(screen.getByRole('dialog', { name: 'Create from Team snapshot' })).toBeVisible();
    expect(screen.getByText(/credentials are never taken from the archive/i)).toBeVisible();
  });

  it('deletes a completed execution only after confirmation', async () => {
    const team: Team = {
      id: 'team-history',
      name: 'History Team',
      orchestrator_id: 'lead',
      members: [{ agent_id: 'researcher', role: 'researcher', allowed_tools: [], enabled: true }],
      workflow: [],
      shared_workspace: false,
      max_parallel: 1,
      max_depth: 1,
      enabled: true,
    };
    const run: TeamRunRecord = {
      id: 'tr_delete1234',
      team_id: team.id,
      status: 'completed',
      error: null,
      mode: 'async',
      task: 'Finished work',
      synthesis_instruction: 'Synthesize.',
      orchestrator_id: 'lead',
      orchestrator_summary: 'Done.',
      created_at: '2026-07-27T04:35:29Z',
      started_at: '2026-07-27T04:35:29Z',
      ended_at: '2026-07-27T04:35:42Z',
      updated_at: '2026-07-27T04:35:42Z',
      revision: 4,
      steps: [],
    };
    const deleteRun = vi.fn(async () => undefined);
    const state = teamState({
      teams: [team],
      runs: [run],
      activeRun: run,
      runsStatus: 'ready',
      deleteRun,
    });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete run delete12' }));
    expect(screen.getByText('Delete this execution?')).toBeVisible();
    expect(screen.getByText(/Agent conversations will not be deleted/i)).toBeVisible();
    expect(deleteRun).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Delete execution' }));
    await waitFor(() => expect(deleteRun).toHaveBeenCalledWith('team-history', 'tr_delete1234'));
  });

  it('creates a Team from an inspected snapshot and selects the imported copy', async () => {
    vi.spyOn(systemApi, 'upload').mockResolvedValue({
      upload_id: 'upload-team',
      filename: 'team.zip',
      size: 100,
      sha256: '0'.repeat(64),
      chunk_size: 100,
      total_parts: 1,
      complete: true,
      preview: {
        inspection: {
          manifest: {
            export_id: 'export-team',
            source_version: '0.2.0',
            agents: [{ id: 'lead', name: 'Lead' }],
            teams: [{ id: 'source-team', name: 'Source Team' }],
          },
          files: 4,
          expanded_bytes: 100,
          warnings: [],
        },
        collisions: [],
        approval_resets: 1,
        paused_cron_jobs: 0,
        providers_reset: 1,
        quarantined_code: [],
        storage_required: 100,
        missing_environment: [],
      },
    });
    const apply = vi.spyOn(systemApi, 'applyUpload').mockResolvedValue({
      export_id: 'export-team',
      agent_id_mappings: { lead: 'lead-copy' },
      team_id_mappings: { 'source-team': 'source-team-copy' },
      disabled_team_members: 0,
      paused_cron_jobs: 0,
      approval_resets: 1,
      providers_reset: 1,
      quarantined_code: [],
      warnings: [],
    });
    const refresh = vi.fn(async () => undefined);
    const state = teamState({ refresh });
    render(<TeamsView agents={agents} state={state} onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /Create from snapshot/i }));
    const file = new File(['snapshot'], 'team.zip', { type: 'application/zip' });
    fireEvent.change(screen.getByLabelText(/Choose a .zip Team snapshot/i), {
      target: { files: [file] },
    });
    fireEvent.click(screen.getByRole('button', { name: /Upload & inspect/i }));
    expect(await screen.findByText('1 Team snapshot ready')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /Create Team from snapshot/i }));

    await waitFor(() => expect(apply).toHaveBeenCalledWith('upload-team', {}));
    expect(refresh).toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create from Team snapshot' })).toBeNull());
  });

  it('highlights active graph nodes, reveals returned output, and cancels the run', async () => {
    vi.spyOn(conversationsApi, 'messages').mockResolvedValue([
      { id: 'user-1', role: 'user', content: 'Research the launch options.' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: 'I found three viable launch plans.',
        reasoning: 'I compared the available evidence.',
        toolCalls: JSON.stringify([{
          id: 'call-1',
          function: { name: 'skill_view', arguments: JSON.stringify({ name: 'product-research' }) },
        }]),
      },
      { id: 'tool-1', role: 'tool', content: 'Skill instructions loaded.', toolName: 'skill_view', toolCallId: 'call-1' },
    ]);
    vi.spyOn(conversationsApi, 'usage').mockResolvedValue({
      conversationId: 'conversation-1',
      messages: 3,
      steps: 1,
      apiCalls: 2,
      model: 'model',
      totalTokens: 1234,
      totalCostUsd: 0,
      provider: 'openai',
      plan: '',
      quotaAvailable: true,
      quotaMessage: '',
      limits: [],
    });
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

    fireEvent.click(screen.getByRole('button', { name: 'View Researcher conversation' }));
    const dialog = await screen.findByRole('dialog', { name: 'Researcher' });
    expect(dialog).toHaveTextContent('1,234');
    expect(dialog).toHaveTextContent('product-research');
    expect(dialog).toHaveTextContent('Steps');
    expect(dialog).toHaveTextContent('Messages');
    expect(dialog).toHaveTextContent('I found three viable launch plans.');
    fireEvent.click(screen.getByRole('button', { name: 'Close node conversation' }));

    fireEvent.click(screen.getByRole('button', { name: 'Cancel run' }));
    await waitFor(() => expect(cancelRun).toHaveBeenCalledWith('team-1', 'tr_live1234'));

    fireEvent.click(screen.getByRole('button', { name: /Execution details/i }));
    expect(screen.queryByLabelText('Live team workflow')).not.toBeInTheDocument();
  });
});
