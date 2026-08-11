import { request, requestRaw } from './client';
import { readSSE, type SSEEvent } from './stream';

export type TeamMember = {
  agent_id: string;
  role: string;
  allowed_tools: string[];
  enabled: boolean;
  diagnostic?: string;
};

export type Team = {
  id: string;
  name: string;
  description?: string;
  orchestrator_id: string;
  coordinator_prompt?: string;
  coordinator_allowed_tools?: string[];
  coordinator_skills?: string[];
  synthesis_agent_id?: string;
  synthesis_allowed_tools?: string[];
  synthesis_skills?: string[];
  members: TeamMember[];
  workflow?: TeamWorkflowStep[];
  shared_workspace: boolean;
  communication_level?: 0 | 1 | 2 | 3;
  synthesis_instruction?: string;
  max_parallel: number;
  max_depth: number;
  enabled: boolean;
};

export type TeamRun = {
  team_id: string;
  member_results: Array<{ id: string; agent_id: string; role: string; needs: string[]; status: 'completed' | 'failed'; summary?: string; error?: string }>;
  workflow_results: TeamRun['member_results'];
  orchestrator_summary: string;
  started_at: string;
  completed_at: string;
};

export type TeamWorkflowStep = {
  id: string;
  task: string;
  agent_id?: string;
  role?: string;
  needs?: string[];
  allowed_tools?: string[];
  skills?: string[];
};

export type TeamRunStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export type TeamRunStep = {
  id: string;
  agent_id: string;
  role: string;
  task: string;
  needs: string[];
  allowed_tools: string[];
  skills?: string[];
  status: TeamRunStatus;
  summary: string;
  summary_chars: number;
  error: string | null;
  conversation_id: string | null;
  started_at: string | null;
  ended_at: string | null;
};

export type TeamRunRecord = {
  id: string;
  team_id: string;
  status: TeamRunStatus;
  error: string | null;
  mode: 'async' | 'sync';
  task: string;
  synthesis_instruction: string;
  orchestrator_id: string;
  orchestrator_summary: string;
  coordinator_conversation_id?: string | null;
  synthesis_conversation_id?: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
  updated_at: string;
  revision: number;
  steps: TeamRunStep[];
};

export const RUN_TERMINAL_STATUSES: TeamRunStatus[] = ['completed', 'failed', 'cancelled'];

export function isRunTerminal(status: TeamRunStatus): boolean {
  return RUN_TERMINAL_STATUSES.includes(status);
}

export type TeamInput = Omit<Team, 'id'>;

function withTeamDescription(team: Team): Team {
  const count = (team.members?.length ?? 0) + 1;
  return {
    ...team,
    shared_workspace: team.shared_workspace ?? false,
    communication_level: team.communication_level ?? 1,
    coordinator_prompt: team.coordinator_prompt ?? '',
    synthesis_agent_id: team.synthesis_agent_id || team.orchestrator_id,
    synthesis_instruction: team.synthesis_instruction?.trim()
      || 'Synthesize these workflow results into one final answer.',
    description: team.description?.trim()
      || `A coordinated team of ${count} agents for multi-stage work.`,
  };
}

export const teamsApi = {
  list: async () => (await request<Team[]>('/xnobrain/api/runtime/v1/teams')).map(withTeamDescription),
  create: async (team: TeamInput) => withTeamDescription(
    await request<Team>('/xnobrain/api/runtime/v1/teams', { method: 'POST', body: JSON.stringify(team) }),
  ),
  update: async (team: Team) => withTeamDescription(
    await request<Team>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(team.id)}`, { method: 'PUT', body: JSON.stringify(team) }),
  ),
  remove: (teamId: string) => request<{ deleted: boolean }>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}`, { method: 'DELETE' }),
  run: (teamId: string, task: string, workflow: TeamWorkflowStep[] = [], synthesis?: string) => request<TeamRun>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/run`, { method: 'POST', body: JSON.stringify({ task, workflow, synthesis }) }),
  startRun: (teamId: string, task: string, workflow: TeamWorkflowStep[] = [], synthesis?: string) =>
    request<TeamRunRecord>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/runs`, { method: 'POST', body: JSON.stringify({ task, workflow, synthesis }) }),
  listRuns: (teamId: string) => request<TeamRunRecord[]>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/runs`),
  getRun: (teamId: string, runId: string) => request<TeamRunRecord>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/runs/${encodeURIComponent(runId)}`),
  deleteRun: (teamId: string, runId: string) =>
    request<{ id: string; team_id: string; deleted: boolean }>(
      `/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/runs/${encodeURIComponent(runId)}`,
      { method: 'DELETE' },
    ),
  cancelRun: (teamId: string, runId: string) =>
    request<TeamRunRecord>(`/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
  async watchRun(
    teamId: string,
    runId: string,
    onEvent: (event: SSEEvent) => void,
    signal: AbortSignal,
    afterRevision?: number,
  ): Promise<void> {
    const query = afterRevision != null ? `?after=${encodeURIComponent(String(afterRevision))}` : '';
    const response = await requestRaw(
      `/xnobrain/api/runtime/v1/teams/${encodeURIComponent(teamId)}/runs/${encodeURIComponent(runId)}/events${query}`,
      { headers: { Accept: 'text/event-stream' }, signal },
    );
    await readSSE(response, onEvent, signal);
  },
};
