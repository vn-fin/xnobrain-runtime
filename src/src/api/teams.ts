import { request } from './client';

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
  orchestrator_id: string;
  members: TeamMember[];
  shared_workspace: boolean;
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
};

export type TeamInput = Omit<Team, 'id'>;

export const teamsApi = {
  list: () => request<Team[]>('/api/v1/teams/'),
  create: (team: TeamInput) => request<Team>('/api/v1/teams/', { method: 'POST', body: JSON.stringify(team) }),
  update: (team: Team) => request<Team>(`/api/v1/teams/${encodeURIComponent(team.id)}`, { method: 'PUT', body: JSON.stringify(team) }),
  remove: (teamId: string) => request<{ deleted: boolean }>(`/api/v1/teams/${encodeURIComponent(teamId)}`, { method: 'DELETE' }),
  run: (teamId: string, task: string, workflow: TeamWorkflowStep[] = [], synthesis?: string) => request<TeamRun>(`/api/v1/teams/${encodeURIComponent(teamId)}/run`, { method: 'POST', body: JSON.stringify({ task, workflow, synthesis }) }),
};
