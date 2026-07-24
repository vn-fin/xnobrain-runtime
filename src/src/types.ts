// Shared domain types for the app. These mirror the backend API models
// (agent-gateway, conversations, sandboxes, and Hermes Kanban).

export type RightView = 'workspace' | 'skills' | 'runtime';
export type CenterView = 'chat' | 'skills' | 'teams' | 'data' | 'kanban' | 'analytics';

export type ConnectionMode = 'device-code' | 'cli' | 'api-key';
export type ProviderBrand = 'openai' | 'claude' | 'anthropic' | 'gemini' | 'openrouter';

// Mirrors agent-gateway models.ProviderConnector (connections view)
export type ConnectionProvider = {
  id: string;
  display_name: string;
  description: string;
  provider_type: string;
  connection_mode: ConnectionMode;
  environment_variable?: string;
  brand: ProviderBrand;
  connected: boolean;
  status: string;
  last_test_status?: string;
  default_model?: string;
  available_models?: string[];
};

// Mirrors agent-gateway models.ProviderConnectInfo (returned by POST /connect)
export type ProviderConnectInfo = {
  connection_mode: ConnectionMode;
  required_client_action: string;
  login_url: string;
  verification_url?: string;
  user_code?: string;
  instructions: string;
  text_label: string;
  status: string;
};

// Simple provider option list used in agent runtime/settings selects
export type ProviderConnector = {
  id: string;
  display_name: string;
  provider_type: string;
  connected: boolean;
  connection_mode: string;
  default_model: string;
  status: string;
};

// Sandbox (/sandboxes/v1/me/sandboxes/*)
export type SandboxResources = { cpus: string; memory: string; rootSize: string };

export type SandboxInfo = {
  vmId: string;
  status: string;
  type: string;
  image: string;
  ipv4: string;
  ipv6: string;
  createdAt: string;
  gateway: { healthy: boolean; port: number };
  resources: SandboxResources;
};

export type SandboxMetrics = {
  cpuPercent: number;
  vcpuTimeNs: number;
  uptimeSeconds: number;
  memoryBytes: number;
  memoryLimitBytes: number;
  memoryAvailableBytes: number;
  diskUsageBytes: number;
  diskTotalBytes: number;
  diskReadBytes: number;
  diskWriteBytes: number;
  netRxBytes: number;
  netTxBytes: number;
};

export type SandboxSystem = {
  cpuPercent: number;
  os: { hostname: string; os: string; osVersion: string; kernelVersion: string; fqdn: string };
};

export type SandboxHealth = { healthy: boolean; statusCode: number; endpoint: string };

export type SandboxData = {
  info: SandboxInfo;
  metrics: SandboxMetrics;
  system: SandboxSystem;
  health: SandboxHealth;
  updatedAt: string;
};

// Skills (agent-gateway agents-skills)
export type AgentSkill = {
  skill_id: string;
  name: string;
  category: string;
  description: string;
  enabled: boolean;
  installed: boolean;
  path: string;
  /** Installed version, when the backend reports one (SKILL.md frontmatter). */
  version?: string;
};

// Conversations (conversations service)
export type Conversation = {
  id: string;
  title: string;
  preview: string;
  startedAt: string;
  model: string;
  messages: number;
  tools: number;
};

export type ChatMessage = {
  id: string | number;
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  content: string;
  toolName?: string;
  streaming?: boolean;
  /** Raw `tool_calls` JSON from history (assistant messages that invoked tools). */
  toolCalls?: string;
  /** Links a `tool` result message back to its originating tool call. */
  toolCallId?: string;
  /** `stop` for a final answer, `tool_calls` for an intermediate tool step. */
  finishReason?: string;
  /** Model reasoning/thinking summary carried on the message. */
  reasoning?: string;
  /** Epoch seconds when the message was produced. */
  timestamp?: number;
};

export type ChatRunStepStatus = 'running' | 'completed' | 'error' | 'interrupted' | 'cancelled';
export type RunApprovalChoice = 'once' | 'always' | 'deny';

export type ChatRunApproval = {
  command: string;
  description: string;
  choices: RunApprovalChoice[];
  allowPermanent: boolean;
  subsystem?: 'skills' | 'memory';
};

export type ChatRunStep = {
  id: string;
  toolName: string;
  preview: string;
  args?: unknown;
  output?: string;
  progress?: string;
  status: ChatRunStepStatus;
  startedAt?: number;
  endedAt?: number;
  /** Duration in seconds as reported by the backend (tool.completed). */
  durationSec?: number;
};

export type ChatRunUsage = {
  totalTokens: number;
  inputTokens?: number;
  outputTokens?: number;
};

/**
 * Ordered timeline of a run's activity: grouped reasoning text blocks
 * interleaved with grouped tool-step batches. `tools` items reference steps by
 * id (the actual step data lives in `ChatRun.steps`, so status updates apply
 * without duplicating state).
 */
export type RunTimelineItem =
  | { kind: 'reasoning'; text: string }
  | { kind: 'tools'; stepIds: string[] };

export type ChatRun = {
  id: string;
  messageId?: string;
  insertBeforeMessageId?: string | number;
  status: 'running' | 'waiting_for_approval' | 'completed' | 'error' | 'interrupted' | 'cancelled';
  startedAt?: number;
  endedAt?: number;
  steps: ChatRunStep[];
  assistantContent: string;
  approval?: ChatRunApproval;
  /** Model reasoning / thinking segments, in order. A run may emit several. */
  reasoning?: string[];
  reasoningStreaming?: boolean;
  /** Ordered, grouped view of reasoning + tool batches for rendering. */
  timeline?: RunTimelineItem[];
  usage?: ChatRunUsage;
};

export type ConversationUsage = {
  conversationId: string;
  messages: number;
  apiCalls: number;
  model: string;
  totalTokens: number;
  totalCostUsd: number;
  provider: string;
  plan: string;
  quotaAvailable: boolean;
  quotaMessage: string;
  limits: Array<{
    name: string;
    remainingPercent: number;
    usedPercent: number;
    resetsIn: string;
    resetsAt: string;
    unlimited: boolean;
  }>;
};

export type AsyncStatus = 'idle' | 'loading' | 'ready' | 'error';

export type SkillAgentState = { installed: boolean; enabled: boolean };
export type SkillStateMap = Record<string, Record<string, SkillAgentState>>;

// Root runtime profile config, used as defaults for new agents.
export type GlobalRuntimeConfig = {
  provider: string;
  model: string;
  skillsWriteApproval: boolean;
  memoryWriteApproval: boolean;
};

// Assistants / agents (agent-gateway agents)
export type Agent = {
  id: string;
  title: string;
  name: string;
  description: string;
  status: string;
  provider: string;
  model: string;
  reasoningEffort: string;
  approvalMode: 'auto' | 'manual';
  skillsWriteApproval: boolean;
  memoryWriteApproval: boolean;
  workspace: string;
  skills: AgentSkill[];
  conversations: Conversation[];
};

// Workspace tree entries (agents-workspaces)
export type WorkspaceEntry = {
  name: string;
  path: string;
  type: 'file' | 'directory';
  level: number;
  open?: boolean;
  selected?: boolean;
  language?: 'python' | 'notebook' | 'markdown' | 'image' | 'json' | 'text' | 'pdf' | 'binary';
  size: string;
  modified: string;
};

// Cron / scheduled jobs
export type CronState = 'scheduled' | 'stopped' | 'running';

export type CronJob = {
  id: string;
  name: string;
  state: CronState;
  intervalMinutes: number;
  forever: boolean;
  repeatCount?: number;
  nextRun: string; // ISO timestamp
  prompt: string;
};

// Per-agent skill enablement map: agentId -> { skillId -> enabled }
export type AgentSkillMap = Record<string, Record<string, boolean>>;

// ---------------------------------------------------------------------------
// Kanban (multi-agent task board)
//
// Hermes has execution substates (triage, todo, ready, running, blocked, done,
// archived). The product combines ready/running and blocked/done into five
// fixed columns.
// ---------------------------------------------------------------------------

export type KanbanColumnId = 'backlog' | 'todo' | 'running' | 'done' | 'archived';
export type KanbanNativeStatus = 'triage' | 'todo' | 'ready' | 'scheduled' | 'running' | 'blocked' | 'review' | 'done' | 'archived';
export type KanbanPriority = 'high' | 'medium' | 'low';
export type KanbanDepState = 'done' | 'pending' | 'blocked';

export type KanbanDependency = { id: string; title: string; state: KanbanDepState };
export type KanbanComment = {
  id: number;
  author: string;
  body: string;
  createdAt: string;
};
export type KanbanTaskEvent = {
  id: number;
  kind: string;
  payload: Record<string, unknown> | null;
  createdAt: string;
};
export type KanbanRun = {
  id: number;
  profile: string | null;
  status: string;
  outcome: string | null;
  summary: string | null;
  startedAt: string;
  endedAt: string | null;
};
export type KanbanWorkerActivity = {
  exists: boolean;
  sizeBytes: number;
  entries: Array<{ kind: string; name: string; durationSeconds: number }>;
};
export type KanbanConversationLink = {
  id: string;
  agentId: string;
  url: string;
};

export type KanbanTaskSchedule = {
  recurrence: 'once' | 'interval';
  nextRunAt: string | null;
  intervalMinutes: number | null;
  timezone: string;
  enabled: boolean;
  occurrenceCount: number;
  lastRunAt: string | null;
};

/** The fixed product status vocabulary. */
export type KanbanStatusDef = {
  id: KanbanColumnId;
  label: string;
  column: KanbanColumnId;
};

export type KanbanTask = {
  id: string;
  title: string;
  description: string;
  /** Five-column presentation status. */
  status: KanbanColumnId;
  /** Original execution state returned by the task runtime. */
  nativeStatus: KanbanNativeStatus;
  allowedStatuses: KanbanColumnId[];
  priority: KanbanPriority;
  /** Native execution assignee; currently contains zero or one agent id. */
  assignees: string[];
  tags: string[];
  skills: string[];
  deps: KanbanDependency[];
  comments: KanbanComment[];
  events: KanbanTaskEvent[];
  runs: KanbanRun[];
  workerActivity: KanbanWorkerActivity | null;
  conversation: KanbanConversationLink | null;
  /** 0–100 completion, surfaced for in-progress work. */
  progress: number;
  /** Human-readable "updated" label (e.g. "4m ago"). */
  updated: string;
  /** Populated when the task is blocked. */
  block?: string | null;
  /** Populated when the task is complete. */
  summary?: string | null;
  result?: string | null;
  schedule: KanbanTaskSchedule | null;
};

export type KanbanBoard = {
  id: string;
  name: string;
  description: string;
  color: string;
  /** Ordered status vocabulary for this board (table grouping + column map). */
  statuses: KanbanStatusDef[];
  tasks: KanbanTask[];
};

export type KanbanViewMode = 'board' | 'table';

export type NewKanbanTaskInput = {
  title: string;
  description: string;
  status: KanbanColumnId | 'scheduled';
  priority: KanbanPriority;
  assignee: string | null;
  skills: string[];
  schedule?: {
    recurrence: 'once' | 'interval';
    scheduled_at: string;
    timezone: string;
    interval_minutes?: number;
  };
};

export type KanbanTaskPatchInput = {
  title: string;
  description: string;
  priority: KanbanPriority;
  skills: string[];
};

export type KanbanEvent = {
  id: number;
  taskId: string;
  kind: string;
  createdAt: string;
  assignee: string | null;
  nativeStatus: KanbanNativeStatus;
  status: KanbanColumnId;
};

export type KanbanNotice = {
  id: number;
  kind: 'success' | 'error';
  message: string;
};
