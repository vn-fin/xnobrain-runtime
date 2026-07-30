import type { ConversationMessageDTO, ConversationSummaryDTO } from '../contracts/conversations';
import type { ChatMessage, Conversation } from '../../types';
import { randomId } from '../../utils/id';

function formatTimestamp(epoch?: number): string {
  if (!epoch) return '';
  const milliseconds = epoch > 10_000_000_000 ? epoch : epoch * 1000;
  return new Date(milliseconds).toLocaleString();
}

export function mapConversation(dto: ConversationSummaryDTO): Conversation {
  return {
    id: dto.id ?? '',
    title: dto.title ?? 'New Conversation',
    preview: dto.preview ?? '',
    startedAt: formatTimestamp(dto.last_active_at ?? dto.started_at),
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
