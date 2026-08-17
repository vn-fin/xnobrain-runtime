import type { AgentDTO } from '../contracts/agentGateway';
import type { Agent } from '../../types';

export function mapAgent(dto: AgentDTO): Agent {
  const config = dto.config ?? dto.metadata?.config;
  return {
    id: dto.id ?? '',
    title: dto.display_name?.trim() || dto.title?.trim() || dto.name || '',
    name: dto.name ?? '',
    description: dto.description ?? '',
    soul: dto.soul ?? '',
    status: dto.status ?? 'unknown',
    // The provider runtime is not a user-selectable provider and is omitted
    // from the public agent DTO because it is not a user-selectable provider.
    provider: config?.provider ?? 'xnobrain',
    model: config?.model ?? '',
    reasoningEffort: config?.reasoning_effort ?? config?.effort ?? '',
    approvalMode: config?.approval_mode === 'on' || config?.approval_mode === 'manual' ? 'manual' : 'auto',
    skillsWriteApproval: config?.skills_write_approval ?? false,
    memoryWriteApproval: config?.memory_write_approval ?? false,
    checkpointsEnabled: config?.checkpoints_enabled ?? false,
    goalMaxTurns: config?.goal_max_turns ?? 20,
    workspace: dto.id ? `${dto.id}/workspace` : '',
    skills: [],
    conversations: [],
  };
}
