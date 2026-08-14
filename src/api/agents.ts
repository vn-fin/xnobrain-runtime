import { request } from './client';
import type {
  AgentConfigDTO,
  AgentConfigUpdateRequestDTO,
  AgentActivityResponseDTO,
  AgentCreateRequestDTO,
  AgentDTO,
  AgentMetadataUpdateRequestDTO,
  AgentTestResponseDTO,
} from './contracts/agentGateway';
import { mapAgent } from './mappers/agents';
import type { Agent } from '../types';

export type MCPConfig = { servers: Record<string, Record<string, unknown>> };

const ROOT = '/xnobrain/api/runtime/v1';
const RUNTIME_PROVIDER = 'nine-router';
const encoded = (value: string) => encodeURIComponent(value);

function routedConfig(input: AgentConfigUpdateRequestDTO): AgentConfigUpdateRequestDTO {
  return input.provider === undefined ? input : { ...input, provider: RUNTIME_PROVIDER };
}

export const agentsApi = {
  async list(): Promise<Agent[]> {
    const data = await request<AgentDTO[]>(`${ROOT}/agents`);
    return (Array.isArray(data) ? data : []).map(mapAgent);
  },

  async activity(): Promise<Record<string, 'running' | 'idle'>> {
    const data = await request<AgentActivityResponseDTO>(`${ROOT}/agents/activity`);
    return data.agents ?? {};
  },

  async detail(id: string): Promise<Agent> {
    return mapAgent(await request<AgentDTO>(`${ROOT}/agents/${encoded(id)}/detail`));
  },

  async create(displayName: string, description: string): Promise<Agent> {
    const body: AgentCreateRequestDTO = { display_name: displayName, description };
    return mapAgent(await request<AgentDTO>(`${ROOT}/agents`, { method: 'POST', body: JSON.stringify(body) }));
  },

  async updateMetadata(id: string, input: AgentMetadataUpdateRequestDTO): Promise<Agent> {
    return mapAgent(
      await request<AgentDTO>(`${ROOT}/agents/${encoded(id)}/metadata`, {
        method: 'PATCH',
        body: JSON.stringify(input),
      }),
    );
  },

  async rename(id: string, displayName: string): Promise<Agent> {
    return agentsApi.updateMetadata(id, { display_name: displayName });
  },

  async updateConfig(id: string, input: AgentConfigUpdateRequestDTO): Promise<void> {
    await request<unknown>(`${ROOT}/agents-configs/${encoded(id)}`, {
      method: 'PATCH',
      body: JSON.stringify(routedConfig(input)),
    });
  },

  updateSoul(id: string, soul: string): Promise<void> {
    return agentsApi.updateConfig(id, { soul });
  },

  getMcp(id: string): Promise<MCPConfig> {
    return request<MCPConfig>(`${ROOT}/agents-mcp/${encoded(id)}`);
  },

  updateMcp(id: string, config: MCPConfig): Promise<MCPConfig> {
    return request<MCPConfig>(`${ROOT}/agents-mcp/${encoded(id)}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    });
  },

  /** Read the global (default) agent config used as the template for new agents. */
  async getGlobalConfig(): Promise<AgentConfigDTO | null> {
    return request<AgentConfigDTO>(`${ROOT}/agents-configs/global`).catch(() => null);
  },

  /** Update the global (default) agent config, e.g. default provider + model. */
  async updateGlobalConfig(input: AgentConfigUpdateRequestDTO): Promise<AgentConfigDTO | null> {
    return request<AgentConfigDTO>(`${ROOT}/agents-configs/global`, {
      method: 'PATCH',
      body: JSON.stringify(routedConfig(input)),
    });
  },

  async update(id: string, updates: Partial<Agent>): Promise<Agent> {
    const metadata: AgentMetadataUpdateRequestDTO = {};
    if (updates.title !== undefined) metadata.display_name = updates.title;
    if (updates.description !== undefined) metadata.description = updates.description;
    const config: AgentConfigDTO = {};
    if (updates.provider !== undefined) config.provider = updates.provider;
    if (updates.model !== undefined) config.model = updates.model;
    if (updates.reasoningEffort !== undefined) config.reasoning_effort = updates.reasoningEffort;
    if (updates.approvalMode !== undefined) {
      // The UI describes the user-facing behavior while the runtime persists the
      // inverse approval gate: manual approvals are "on" and full-access
      // automatic execution is "off".
      config.approval_mode = updates.approvalMode === 'auto' ? 'off' : 'on';
    }
    if (updates.checkpointsEnabled !== undefined) config.checkpoints_enabled = updates.checkpointsEnabled;
    if (updates.goalMaxTurns !== undefined) config.goal_max_turns = updates.goalMaxTurns;
    await Promise.all([
      Object.keys(metadata).length ? agentsApi.updateMetadata(id, metadata) : Promise.resolve(),
      Object.keys(config).length ? agentsApi.updateConfig(id, config) : Promise.resolve(),
    ]);
    return agentsApi.detail(id);
  },

  async remove(id: string): Promise<void> {
    await request<unknown>(`${ROOT}/agents/${encoded(id)}/delete`, { method: 'DELETE' });
  },

  test(id: string): Promise<AgentTestResponseDTO> {
    return request<AgentTestResponseDTO>(`${ROOT}/agents/${encoded(id)}/test`, { method: 'POST' });
  },
};
