import type { AgentDTO } from '../contracts/agentGateway';
import type { Agent } from '../../types';

export function mapAgent(dto: AgentDTO): Agent {
  const config = dto.config ?? dto.metadata?.config;
  return {
    id: dto.id ?? '',
    title: dto.display_name?.trim() || dto.title?.trim() || dto.name || '',
    name: dto.name ?? '',
    description: dto.description ?? '',
    status: dto.status ?? 'unknown',
    provider: config?.provider ?? '',
    model: config?.model ?? '',
    reasoningEffort: config?.reasoning_effort ?? config?.effort ?? '',
    approvalMode: config?.approval_mode === 'auto' ? 'auto' : 'manual',
    skillsWriteApproval: config?.skills_write_approval ?? true,
    memoryWriteApproval: config?.memory_write_approval ?? true,
    workspace: dto.name ? `/home/user/agents/${dto.name}/workspace` : '',
    skills: [],
    conversations: [],
  };
}
