// Shared domain types for the app. These mirror the backend API models
// (agent-gateway, conversations, sandboxes, and Hermes Kanban).

export type RightView = 'workspace' | 'skills' | 'cron' | 'runtime';
export type CenterView = 'chat' | 'skills' | 'teams' | 'data' | 'kanban' | 'analytics' | 'cron';

export type ConnectionMode = 'device-code' | 'cli' | 'api-key' | 'no-auth';
export type ProviderBrand = 'openai' | 'claude' | 'anthropic' | 'gemini' | 'xai' | 'openrouter' | 'groq' | 'deepseek' | 'moonshot' | 'qwen' | 'openai-like' | 'opencode';

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
  connection_count?: number;
  base_url?: string;
  requires_base_url?: boolean;
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
  available_models?: string[];
  status: string;
};

// Sandbox (/xnobrain/api/runtime/v1/sandboxes/*)
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
  updatedAt?: number;
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

export type DelegationWorkerStatus = 'queued' | 'running' | 'completed' | 'error' | 'interrupted' | 'cancelled';

export type DelegationLogEntry = {
  id: string;
  timestamp?: number;
  kind: 'start' | 'tool' | 'activity' | 'text' | 'complete' | 'error';
  message: string;
  tool?: string;
};

export type DelegationWorker = {
  index: number;
  goal: string;
  status: DelegationWorkerStatus;
  queuePosition?: number;
  startedAt?: number;
  endedAt?: number;
  lastEventAt?: number;
  lastTool?: string;
  toolCount: number;
  steps?: number;
  inputTokens?: number;
  outputTokens?: number;
  reasoningTokens?: number;
  summary?: string;
  error?: string;
  filesRead?: string[];
  filesWritten?: string[];
  logs: DelegationLogEntry[];
};

export type ChatDelegation = {
  concurrency: number;
  workers: DelegationWorker[];
};

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
  delegation?: ChatDelegation;
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

export type ChatTodoStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled';

export type ChatTodoItem = {
  id: string;
  content: string;
  status: ChatTodoStatus;
};

/**
 * Ordered timeline of a run's activity: reasoning, tool batches, and immutable
 * plan snapshots. `tools` items reference steps by id; todo snapshots retain
 * each progress update at the point where Hermes emitted it.
 */
export type RunTimelineItem =
  | { kind: 'reasoning'; text: string }
  | { kind: 'tools'; stepIds: string[] }
  | { kind: 'todos'; todos: ChatTodoItem[] };

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
  /** Latest authoritative session plan emitted by Hermes' todo tool. */
  todos?: ChatTodoItem[];
  usage?: ChatRunUsage;
  durable?: boolean;
  runMode?: 'interactive' | 'background';
  timeoutSeconds?: number;
  deadlineAt?: number;
};

export type ConversationUsage = {
  conversationId: string;
  messages: number;
  steps?: number;
  executionSeconds?: number;
  apiCalls: number;
  model: string;
  totalTokens: number;
  contextUsed?: number;
  contextLimit?: number;
  contextPercent?: number;
  contextThreshold?: number;
  contextPressurePercent?: number;
  contextAutoCompaction?: boolean;
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
  soul: string;
  status: string;
  runtimeStatus?: 'running' | 'idle';
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
  language?: 'python' | 'notebook' | 'markdown' | 'image' | 'json' | 'text' | 'html'
    | 'javascript' | 'typescript' | 'shell' | 'css' | 'sql' | 'yaml' | 'xml'
    | 'toml' | 'ini' | 'go' | 'rust' | 'java' | 'c' | 'cpp' | 'csharp'
    | 'ruby' | 'php' | 'swift' | 'kotlin' | 'dart' | 'lua' | 'perl' | 'r'
    | 'graphql' | 'dockerfile' | 'makefile' | 'diff'
    | 'pdf' | 'document' | 'spreadsheet' | 'presentation' | 'binary';
  size: string;
  modified: string;
};

// Cron / scheduled jobs
export type CronState = 'scheduled' | 'stopped' | 'running';

export type CronJob = {
  id: string;
  agentId: string;
  name: string;
  state: CronState;
  schedule: string;
  intervalMinutes: number;
  nextRun: string;
  prompt: string;
};

export type CronRun = {
  id: string;
  state: 'running' | 'success' | 'failed';
  triggeredAt: string;
  completedAt: string;
  output: string;
  error: string;
  occurrenceId?: string;
  deliveries?: CronDeliveryRecord[];
};

export type CronDeliveryTargetType = 'channel' | 'email' | 'kanban' | 'file';
export type CronDeliveryTarget = {
  id: string;
  targetType: CronDeliveryTargetType;
  destination: string;
  available: boolean;
  degradedReason?: string | null;
};
export type CronDeliveryOption = CronDeliveryTarget & { name: string };
export type CronDeliveryRecord = {
  targetId: string;
  targetType: CronDeliveryTargetType;
  status: 'delivered' | 'failed' | 'degraded';
  at: string;
  reason?: string | null;
};
export type CronBlueprintField = {
  name: string;
  type: string;
  label: string;
  default?: unknown;
  options?: unknown[];
  optional?: boolean;
  help?: string;
};
export type CronBlueprint = {
  key: string;
  title: string;
  description: string;
  category: string;
  tags: string[];
  fields: CronBlueprintField[];
  schedule: string;
  scheduleHuman: string;
};
export type CronJobRun = CronRun & { deliveries: CronDeliveryRecord[] };

export type CronDetail = {
  job: CronJob;
  run: CronRun | null;
  targets?: CronDeliveryTarget[];
  runs?: CronJobRun[];
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

export type KanbanTeamNode = {
  stepId: string;
  taskId: string;
  title: string;
  agentId: string;
  role: string;
  needs: string[];
  status: KanbanNativeStatus | 'archived';
  kanbanStatus: KanbanColumnId;
  summary: string | null;
};

export type KanbanTeamGroup = {
  id: string;
  name: string;
  orchestratorId: string;
  status: string;
  nodes: KanbanTeamNode[];
  synthesisTaskId: string | null;
  progress: number;
  cancelled: boolean;
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
  /** API `kanban_status`: placement/grouping in the web board. */
  status: KanbanColumnId;
  /** API `status`: execution state displayed to the user. */
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
  team: KanbanTeamGroup | null;
};

export type KanbanBoard = {
  id: string;
  name: string;
  description: string;
  color: string;
  /** Ordered status vocabulary for this board (table grouping + column map). */
  statuses: KanbanStatusDef[];
  tasks: KanbanTask[];
  /** Lightweight count supplied independently from task pages. */
  taskCount?: number;
};

export type KanbanBoardStats = {
  boardSlug: string;
  total: number;
  current: number;
  completed: number;
  archived: number;
  running: number;
  blocked: number;
  byStatus: Record<KanbanColumnId, number>;
};

export type KanbanViewMode = 'board' | 'table';

export type NewKanbanBoardInput = {
  name: string;
  slug: string;
  description: string;
  color: string;
};

export type NewKanbanTaskInput = {
  title: string;
  description: string;
  status: KanbanColumnId | 'scheduled';
  priority: KanbanPriority;
  assignee: string | null;
  teamId?: string | null;
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
  title: string;
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
