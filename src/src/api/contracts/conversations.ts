export type ConversationSummaryDTO = {
  id?: string;
  title?: string | null;
  model?: string;
  source?: string;
  preview?: string | null;
  message_count?: number;
  tool_call_count?: number;
  started_at?: number;
  last_active_at?: number;
  ended_at?: number;
  end_reason?: string;
  system_prompt?: string;
  model_config?: Record<string, unknown>;
};

export type ConversationListResponseDTO = {
  conversations?: ConversationSummaryDTO[];
  pagination?: { page?: number; limit?: number; has_more?: boolean };
};

export type ConversationMessageDTO = {
  id?: string | number;
  role?: string;
  content?: string | null;
  tool_name?: string | null;
  tool_call_id?: string | null;
  tool_calls?: string | null;
  finish_reason?: string | null;
  reasoning?: string | null;
  reasoning_content?: string | null;
  timestamp?: number;
};

export type ConversationMessagesResponseDTO = { messages?: ConversationMessageDTO[] };

export type ConversationUsageDTO = {
  conversation_id?: string;
  api_calls?: number;
  compressions?: number;
  duration?: string;
  messages?: number;
  model?: string;
  source?: string;
  tokens?: {
    cache_read?: number;
    cache_write?: number;
    input?: number;
    output?: number;
    reasoning?: number;
    total?: number;
  };
  cost?: { source?: string; status?: string; total_usd?: number };
  context?: { limit?: number; percent?: number; used?: number };
  account?: {
    provider?: string;
    plan?: string;
    available?: boolean;
    message?: string;
    limits?: Array<{
      name?: string;
      remaining_percent?: number;
      used_percent?: number;
      resets_in?: string;
      resets_at?: string;
      unlimited?: boolean;
    }>;
  };
};
