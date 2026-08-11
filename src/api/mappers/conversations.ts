import type { ConversationMessageDTO, ConversationSummaryDTO } from '../contracts/conversations';
import type { ChatMessage, Conversation } from '../../types';
import { randomId } from '../../utils/id';

function timestampValue(value?: number | string): number {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value > 10_000_000_000 ? value : value * 1000;
  }
  if (typeof value === 'string' && value.trim()) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric > 10_000_000_000 ? numeric : numeric * 1000;
    const parsed = Date.parse(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return 0;
}

function formatTimestamp(value?: number | string): string {
  const milliseconds = timestampValue(value);
  if (!milliseconds) return '';
  return new Date(milliseconds).toLocaleString();
}

export function mapConversation(dto: ConversationSummaryDTO): Conversation {
  const activity = dto.last_active_at ?? dto.updated_at ?? dto.ended_at ?? dto.started_at ?? dto.created_at;
  return {
    id: dto.id ?? '',
    title: dto.title ?? 'New Conversation',
    preview: dto.preview ?? '',
    startedAt: formatTimestamp(activity),
    updatedAt: timestampValue(activity),
    model: dto.model ?? '',
    messages: dto.message_count ?? 0,
    tools: dto.tool_call_count ?? 0,
  };
}

export function mapMessage(dto: ConversationMessageDTO): ChatMessage {
  const reasoning = dto.reasoning ?? dto.reasoning_content ?? '';
  return {
    id: dto.id ?? randomId(),
    role: dto.role ?? 'assistant',
    content: dto.content ?? '',
    ...(dto.tool_name ? { toolName: dto.tool_name } : {}),
    ...(dto.tool_call_id ? { toolCallId: dto.tool_call_id } : {}),
    ...(dto.tool_calls ? { toolCalls: dto.tool_calls } : {}),
    ...(dto.finish_reason ? { finishReason: dto.finish_reason } : {}),
    ...(reasoning ? { reasoning } : {}),
    ...(typeof dto.timestamp === 'number' ? { timestamp: dto.timestamp } : {}),
  };
}
