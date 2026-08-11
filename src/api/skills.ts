import { request, requestWithMeta, type ResponsePagination } from './client';
import type {
  AgentSkillDTO,
  AgentSkillInstallRequestDTO,
  AgentSkillListResponseDTO,
  AgentSkillsOverviewResponseDTO,
} from './contracts/agentGateway';
import type { AgentSkill } from '../types';

const ROOT = '/xnobrain/api/runtime/v1/agents-skills';
const encoded = (value: string) => encodeURIComponent(value);

export type SkillsPage = { skills: AgentSkill[]; pagination?: ResponsePagination };
export type SkillsOverview = { skills: AgentSkill[]; agents: Record<string, AgentSkill[]> };
export type SkillSyncAgentPlan = {
  agent_id: string;
  added: string[];
  updated: string[];
  removed: string[];
  preserved: string[];
  unchanged: string[];
};
export type SkillSyncPreview = {
  source_revision: string;
  common: { enabled: number; disabled: number };
  agents: SkillSyncAgentPlan[];
  totals: Record<'added' | 'updated' | 'removed' | 'preserved' | 'unchanged', number>;
};
export type SkillSyncResult = {
  source_revision: string;
  status: 'completed' | 'partial';
  agents: Array<SkillSyncAgentPlan & { status: 'completed' | 'failed'; error?: string }>;
  completed: number;
  failed: number;
};

function mapSkill(dto: AgentSkillDTO): AgentSkill {
  return {
    skill_id: dto.skill_id ?? dto.name ?? '',
    name: dto.name ?? dto.skill_id ?? '',
    category: dto.category ?? 'skills',
    description: dto.description ?? '',
    enabled: dto.enabled ?? false,
    installed: dto.installed ?? true,
    path: dto.path ?? '',
    version: dto.version ?? undefined,
  };
}

function skills(data: AgentSkillListResponseDTO | AgentSkillDTO[] | undefined): AgentSkill[] {
  const list = Array.isArray(data) ? data : data?.skills ?? [];
  return list.map(mapSkill);
}

export const skillsApi = {
  async listOverview(): Promise<SkillsOverview> {
    const data = await request<AgentSkillsOverviewResponseDTO>(`${ROOT}?include_agents=true`);
    return {
      skills: skills(data?.skills),
      agents: Object.fromEntries(
        Object.entries(data?.agents ?? {}).map(([agentId, agentSkills]) => [
          agentId,
          skills(agentSkills),
        ]),
      ),
    };
  },

  async previewSync(agentIds: string[]): Promise<SkillSyncPreview> {
    return request<SkillSyncPreview>(`${ROOT}/sync/preview`, {
      method: 'POST',
      body: JSON.stringify({ agent_ids: agentIds }),
    });
  },

  async sync(agentIds: string[], expectedSourceRevision: string): Promise<SkillSyncResult> {
    return request<SkillSyncResult>(`${ROOT}/sync`, {
      method: 'POST',
      body: JSON.stringify({
        agent_ids: agentIds,
        expected_source_revision: expectedSourceRevision,
      }),
    });
  },

  async listDefault(page?: number): Promise<SkillsPage> {
    const query = page != null ? `?page=${encodeURIComponent(String(page))}` : '';
    const { data, pagination } = await requestWithMeta<AgentSkillListResponseDTO | AgentSkillDTO[]>(
      `${ROOT}${query}`,
    );
    return { skills: skills(data), pagination };
  },

  async list(agentId: string, page?: number): Promise<SkillsPage> {
    const query = page != null ? `?page=${encodeURIComponent(String(page))}` : '';
    const { data, pagination } = await requestWithMeta<AgentSkillListResponseDTO | AgentSkillDTO[]>(
      `${ROOT}/${encoded(agentId)}${query}`,
    );
    return { skills: skills(data), pagination };
  },

  async installDefault(input: AgentSkillInstallRequestDTO): Promise<AgentSkill[]> {
    return skills(
      await request<AgentSkillListResponseDTO>(ROOT, {
        method: 'POST',
        body: JSON.stringify(input),
      }),
    );
  },

  async setDefaultEnabled(skillId: string, enabled: boolean): Promise<AgentSkill[]> {
    return skills(
      await request<AgentSkillListResponseDTO>(`${ROOT}/${encoded(skillId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      }),
    );
  },

  async install(agentId: string, input: AgentSkillInstallRequestDTO): Promise<AgentSkill[]> {
    return skills(
      await request<AgentSkillListResponseDTO>(`${ROOT}/${encoded(agentId)}`, {
        method: 'POST',
        body: JSON.stringify(input),
      }),
    );
  },

  async setEnabled(agentId: string, skillId: string, enabled: boolean): Promise<AgentSkill[]> {
    return skills(
      await request<AgentSkillListResponseDTO>(`${ROOT}/${encoded(agentId)}/${encoded(skillId)}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled }),
      }),
    );
  },

  async remove(agentId: string, skillId: string): Promise<AgentSkill[]> {
    return skills(
      await request<AgentSkillListResponseDTO>(`${ROOT}/${encoded(agentId)}/${encoded(skillId)}`, {
        method: 'DELETE',
      }),
    );
  },
};
