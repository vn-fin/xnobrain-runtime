export type ConversationSummaryDTO = {
  id?: string;
  title?: string | null;
  model?: string;
  source?: string;
  preview?: string | null;
  message_count?: number;
  tool_call_count?: number;
  started_at?: number | string;
  last_active_at?: number | string;
  ended_at?: number | string;
  created_at?: number | string;
  updated_at?: number | string;
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

export type ConversationRunDTO = {
  id: string;
  agent_id: string;
  conversation_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'timed_out' | 'cancelled';
  mode: 'interactive' | 'background';
  timeout_seconds: number;
  created_at: number;
  started_at?: number | null;
  ended_at?: number | null;
  last_activity_at?: number | null;
  deadline_at?: number | null;
  revision: number;
  error?: string | null;
};

export type ActiveConversationRunDTO = { run?: ConversationRunDTO | null };

export type GoalDTO = {
  objective: string;
  status: 'active' | 'paused' | 'done';
  turns_used: number;
  max_turns: number;
  created_at?: number;
  last_turn_at?: number;
  last_verdict?: string | null;
  last_reason?: string | null;
  paused_reason?: string | null;
  waiting?: boolean;
  waiting_reason?: string | null;
  subgoals?: string[];
  contract?: {
    outcome?: string;
    verification?: string;
    constraints?: string;
    boundaries?: string;
    stop_when?: string;
  };
};

export type GoalResponseDTO = { goal?: GoalDTO | null; run?: ConversationRunDTO };

export type ConversationCompactResponseDTO = {
  conversation_id?: string;
  before_tokens?: number;
  after_tokens?: number;
  messages_before?: number;
  messages_after?: number;
  focus?: string;
  in_place?: boolean;
};

export type ConversationUsageDTO = {
  conversation_id?: string;
  api_calls?: number;
  compressions?: number;
  duration?: string;
  execution_seconds?: number;
  messages?: number;
  steps?: number;
  tool_calls?: number;
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
  context?: {
    auto_compaction?: boolean;
    limit?: number;
    percent?: number;
    pressure_percent?: number;
    threshold?: number;
    used?: number;
  };
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
