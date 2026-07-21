import { request, requestRaw } from './client';
import type {
  ConversationListResponseDTO,
  ConversationMessageDTO,
  ConversationMessagesResponseDTO,
  ConversationSummaryDTO,
  ConversationUsageDTO,
} from './contracts/conversations';
import { mapConversation, mapMessage } from './mappers/conversations';
import { readSSE, type SSEEvent } from './stream';
import type { ChatMessage, Conversation, ConversationUsage, RunApprovalChoice } from '../types';

const ROOT = '/conversations/v1/conversations';
const encoded = (value: string) => encodeURIComponent(value);

export type ResolveRunApprovalResponse = {
  run_id?: string;
  choice?: RunApprovalChoice;
  resolved?: number;
};

function pathWithAgent(path: string, agentId: string, extra?: Record<string, string | number | undefined>) {
  const params = new URLSearchParams({ agent: agentId });
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (value !== undefined) params.set(key, String(value));
  }
  return `${path}?${params.toString()}`;
}

function mapUsage(dto: ConversationUsageDTO): ConversationUsage {
  return {
    conversationId: dto.conversation_id ?? '',
    messages: dto.messages ?? 0,
    apiCalls: dto.api_calls ?? 0,
    model: dto.model ?? '',
    totalTokens: dto.tokens?.total ?? 0,
    totalCostUsd: dto.cost?.total_usd ?? 0,
    provider: dto.account?.provider ?? '',
    plan: dto.account?.plan ?? '',
    quotaAvailable: dto.account?.available ?? false,
    quotaMessage: dto.account?.message ?? '',
    limits: (dto.account?.limits ?? []).map((limit) => ({
      name: limit.name ?? '',
      remainingPercent: limit.remaining_percent ?? 0,
      usedPercent: limit.used_percent ?? 0,
      resetsIn: limit.resets_in ?? '',
      resetsAt: limit.resets_at ?? '',
      unlimited: limit.unlimited ?? false,
    })),
  };
}

export const conversationsApi = {
  async list(agentId: string, page = 1, limit = 50): Promise<Conversation[]> {
    const data = await request<ConversationListResponseDTO>(pathWithAgent(ROOT, agentId, { page, limit }));
    return (data.conversations ?? []).map(mapConversation);
  },

  async create(agentId: string, title = 'New conversation'): Promise<Conversation> {
    const data = await request<ConversationSummaryDTO>(pathWithAgent(ROOT, agentId), {
      method: 'POST',
      body: JSON.stringify(title.trim() ? { title: title.trim() } : {}),
    });
    return mapConversation(data);
  },

  async detail(agentId: string, conversationId: string): Promise<Conversation> {
    const data = await request<ConversationSummaryDTO>(pathWithAgent(`${ROOT}/${encoded(conversationId)}/detail`, agentId));
    return mapConversation(data);
  },

  async messages(agentId: string, conversationId: string, signal?: AbortSignal): Promise<ChatMessage[]> {
    const path = pathWithAgent(`${ROOT}/${encoded(conversationId)}/messages`, agentId);
    const data = signal
      ? await request<ConversationMessagesResponseDTO | ConversationMessageDTO[]>(path, { signal })
      : await request<ConversationMessagesResponseDTO | ConversationMessageDTO[]>(path);
    const messages = Array.isArray(data) ? data : data.messages ?? [];
    return messages.map(mapMessage);
  },

  async usage(agentId: string, conversationId: string, signal?: AbortSignal): Promise<ConversationUsage> {
    const path = pathWithAgent(`${ROOT}/${encoded(conversationId)}/usage`, agentId);
    return mapUsage(
      signal ? await request<ConversationUsageDTO>(path, { signal }) : await request<ConversationUsageDTO>(path),
    );
  },

  async rename(agentId: string, conversationId: string, title: string): Promise<Conversation> {
    const data = await request<ConversationSummaryDTO>(pathWithAgent(`${ROOT}/${encoded(conversationId)}/name`, agentId), {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    });
    return mapConversation(data);
  },

  async remove(agentId: string, conversationId: string): Promise<void> {
    await request<unknown>(pathWithAgent(`${ROOT}/${encoded(conversationId)}/delete`, agentId), { method: 'DELETE' });
  },

  async stream(
    agentId: string,
    conversationId: string,
    input: string,
    onEvent: (event: SSEEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const response = await requestRaw(pathWithAgent(`${ROOT}/${encoded(conversationId)}/chat/stream`, agentId), {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      body: JSON.stringify({ input }),
      signal,
    });
    await readSSE(response, onEvent, signal);
  },

  async stopRun(agentId: string, conversationId: string, runId: string): Promise<void> {
    await request<unknown>(pathWithAgent(
      `${ROOT}/${encoded(conversationId)}/runs/${encoded(runId)}/stop`,
      agentId,
    ), { method: 'POST' });
  },

  async resolveRunApproval(
    agentId: string,
    conversationId: string,
    runId: string,
    choice: RunApprovalChoice,
    resolveAll = false,
    subsystem?: 'skills' | 'memory',
  ): Promise<ResolveRunApprovalResponse> {
    return request<ResolveRunApprovalResponse>(pathWithAgent(
      `${ROOT}/${encoded(conversationId)}/runs/${encoded(runId)}/approval`,
      agentId,
    ), {
      method: 'POST',
      body: JSON.stringify({ choice, resolve_all: resolveAll, ...(subsystem ? { subsystem } : {}) }),
    });
  },

  async cancelRun(agentId: string, conversationId: string, runId: string): Promise<void> {
    await conversationsApi.stopRun(agentId, conversationId, runId);
  },
};
