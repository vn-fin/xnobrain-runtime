import { Fragment, useEffect, useMemo, useRef, useState, type ChangeEvent, type CSSProperties, type DragEvent, type KeyboardEvent } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ArrowUp,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Clock,
  Copy,
  FileText,
  Gauge,
  GraduationCap,
  ListTodo,
  LoaderCircle,
  Menu,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Square,
  Target,
  Users,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react';
import { TreeIcon } from './common';
import { useStreamingConversations } from '../hooks/useConversation';
import { useDismissibleLayer } from '../hooks/useDismissibleLayer';
import { AsyncState } from './AsyncState';
import { RunActivityBar, RunSteps, RunUsage } from './RunSteps';
import { Markdown } from './Markdown';
import { UserMessage } from './UserMessage';
import { ConfirmDialog } from './modals';
import { WORKSPACE_FILE_MIME, readDroppedEntries } from './WorkspacePanel';
import { workspaceApi } from '../api/workspace';
import type { ConversationCompactResult } from '../api/conversations';
import type {
  Agent,
  AsyncStatus,
  ChatMessage,
  ChatRun,
  ComposerFeature,
  ConnectionProvider,
  Conversation,
  ConversationGoal,
  ConversationUsage,
  RunApprovalChoice,
  WorkspaceEntry,
} from '../types';

/** Animated three-dot "assistant is responding" indicator shown before the first token. */
function TypingIndicator() {
  return (
    <span className="typing-indicator" role="status" aria-label="Agent is responding">
      <span /><span /><span />
    </span>
  );
}

function compactTokens(value: number): string {
  if (value >= 1_000_000) return `${Number((value / 1_000_000).toFixed(1))}M`;
  if (value >= 1_000) return `${Number((value / 1_000).toFixed(1))}K`;
  return String(value);
}

function conversationTime(conversation: Conversation): string {
  const raw = conversation.updatedAt ?? conversation.startedAt;
  if (!raw) return '';
  const date = new Date(typeof raw === 'number' && raw < 10_000_000_000 ? raw * 1000 : raw);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export function ContextGauge({ usage, model }: { usage: ConversationUsage | null; model: string }) {
  const used = Math.max(0, usage?.contextUsed ?? 0);
  const limit = Math.max(0, usage?.contextLimit ?? 0);
  const threshold = Math.max(0, usage?.contextThreshold ?? 0);
  const denominator = threshold || limit;
  const known = used > 0 && denominator > 0;
  const percent = known
    ? Math.max(0, Math.min(100, threshold
      ? usage?.contextPressurePercent ?? used / threshold * 100
      : usage?.contextPercent ?? used / limit * 100))
    : 0;
  const modelLabel = model.toLowerCase() === 'auto' ? 'Auto model' : model || 'Selected model';
  const roundedPercent = Math.round(percent);
  const level = threshold && percent >= 85 ? 'critical' : threshold && percent >= 60 ? 'elevated' : 'normal';
  const label = used <= 0
    ? '—'
    : threshold && percent >= 100
      ? 'Due'
      : threshold && percent >= 85
        ? `${roundedPercent}% · Soon`
        : threshold && percent >= 60
          ? `${roundedPercent}%`
          : compactTokens(used);
  const title = threshold && known
    ? `${compactTokens(used)} / ${compactTokens(threshold)} context (${Number(percent.toFixed(1))}% to compaction)${limit ? `; ${compactTokens(limit)} model window` : ''}; auto-compaction ${usage?.contextAutoCompaction ? 'enabled' : 'disabled'}`
    : known
      ? `${compactTokens(used)} / ${compactTokens(limit)} model context (${Number(percent.toFixed(1))}%)`
    : used > 0
      ? `${compactTokens(used)} context used; ${modelLabel} context limit is unavailable`
      : `${modelLabel} context usage is unavailable`;
  return (
    <span
      className={`context-meter ${level}${known ? '' : ' unknown'}`}
      style={known ? { '--context-percent': `${percent}%` } as CSSProperties : undefined}
      role="img"
      aria-label={title}
    >
      <span className="context-meter-label">{label}</span>
      <span className="context-meter-track"><i /></span>
      <span className="context-meter-tooltip" aria-hidden="true">
        <strong>Context usage</strong>
        {threshold && known ? <>
          <span>{compactTokens(used)} / {compactTokens(threshold)} to compaction</span>
          <span>{roundedPercent}% · Auto-compaction {usage?.contextAutoCompaction ? 'on' : 'off'}</span>
          {limit > 0 && <span>Model window {compactTokens(limit)}</span>}
        </> : known ? <>
          <span>{compactTokens(used)} / {compactTokens(limit)} model context</span>
          <span>{roundedPercent}% of model window</span>
        </> : <span>{used > 0 ? `${compactTokens(used)} used · limit unavailable` : 'Available after the first response'}</span>}
      </span>
    </span>
  );
}

export function ChatArea({
  agent,
  agents,
  activeConversation,
  providers,
  blends = [],
  runs,
  messages,
  usage,
  queuedMessages = [],
  onEditQueued,
  onDeleteQueued,
  onMoveQueued,
  chatStatus,
  chatError,
  streaming,
  canStop,
  compacting = false,
  compactError = '',
  compactResult = null,
  goal = null,
  goalPending = false,
  goalError = '',
  goalMaxTurns = 20,
  onCompactContext,
  onUpdateGoal = async () => undefined,
  onPauseGoal = async () => undefined,
  onResumeGoal = async () => undefined,
  onDeleteGoal = async () => undefined,
  onAddSubgoal = async () => undefined,
  onDeleteSubgoal = async () => undefined,
  onGoalMaxTurnsChange = async () => undefined,
  onSend,
  onStop,
  onResolveRunApproval,
  onRetry,
  onSelectModel,
  onSelectAgent,
  onNewAgent,
  onOpenManage,
  onTestAgent,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onRenameConversation,
  conversationHasMore = false,
  onLoadMoreConversations,
  onOpenFile,
  onUploadFiles,
  onUploadTree,
  workspaceCwd,
  missingConversationId = '',
}: {
  agent: Agent;
  agents: Agent[];
  activeConversation: Conversation | undefined;
  providers: ConnectionProvider[];
  blends?: string[];
  runs: ChatRun[];
  messages: ChatMessage[];
  usage: ConversationUsage | null;
  queuedMessages?: Array<{ id: string; content: string }>;
  onEditQueued?: (id: string, content: string) => void;
  onDeleteQueued?: (id: string) => void;
  onMoveQueued?: (id: string, direction: 'up' | 'down') => void;
  chatStatus: AsyncStatus;
  chatError: string;
  streaming: boolean;
  canStop: boolean;
  compacting?: boolean;
  compactError?: string;
  compactResult?: ConversationCompactResult | null;
  goal?: ConversationGoal | null;
  goalPending?: boolean;
  goalError?: string;
  goalMaxTurns?: number;
  onCompactContext?: (focus?: string) => void | Promise<ConversationCompactResult | void>;
  onUpdateGoal?: (objective: string, maxTurns?: number, contract?: ConversationGoal['contract']) => void | Promise<unknown>;
  onPauseGoal?: () => void | Promise<unknown>;
  onResumeGoal?: () => void | Promise<unknown>;
  onDeleteGoal?: () => void | Promise<unknown>;
  onAddSubgoal?: (text: string) => void | Promise<unknown>;
  onDeleteSubgoal?: (index: number) => void | Promise<unknown>;
  onGoalMaxTurnsChange?: (turns: number) => void | Promise<unknown>;
  onSend: (input: string, feature?: ComposerFeature) => void | Promise<void>;
  onStop: () => void;
  onResolveRunApproval: (runId: string, choice: RunApprovalChoice) => void | Promise<void>;
  onRetry: () => void;
  onSelectModel: (providerId: string, model: string) => void | Promise<void>;
  onSelectAgent: (agent: Agent) => void;
  onNewAgent?: () => void;
  onOpenManage?: () => void;
  onTestAgent: () => void;
  onSelectConversation: (id: string) => void;
  onCreateConversation: () => void;
  onDeleteConversation: (id: string) => void;
  onRenameConversation: (id: string, title: string) => void | Promise<void>;
  conversationHasMore?: boolean;
  onLoadMoreConversations?: () => void | Promise<unknown>;
  onOpenFile: (path: string) => void;
  onUploadFiles?: (files: File[]) => void | Promise<void>;
  onUploadTree?: (items: { file: File; relativeDir: string }[]) => void | Promise<void>;
  workspaceCwd?: string;
  missingConversationId?: string;
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const [modelOpen, setModelOpen] = useState(false);
  const [contextOpen, setContextOpen] = useState(false);
  const [contextConfirming, setContextConfirming] = useState(false);
  const [contextFocus, setContextFocus] = useState('');
  const [featureMenuOpen, setFeatureMenuOpen] = useState(false);
  const [selectedFeature, setSelectedFeature] = useState<ComposerFeature>();
  const [goalOpen, setGoalOpen] = useState(false);
  const [goalEditing, setGoalEditing] = useState(false);
  const [goalDraft, setGoalDraft] = useState('');
  const [goalConfigPending, setGoalConfigPending] = useState(false);
  const [goalConfigError, setGoalConfigError] = useState('');
  const [subgoalDraft, setSubgoalDraft] = useState('');
  const [subgoalAdding, setSubgoalAdding] = useState(false);
  const [goalDeleteConfirm, setGoalDeleteConfirm] = useState(false);
  const [goalBarExpanded, setGoalBarExpanded] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [agentPickerSearch, setAgentPickerSearch] = useState('');
  const [mobileActionsOpen, setMobileActionsOpen] = useState(false);
  const [conversationPickerOpen, setConversationPickerOpen] = useState(false);
  const [stopConfirmOpen, setStopConfirmOpen] = useState(false);
  const [conversationSearch, setConversationSearch] = useState('');
  const [conversationLoadingMore, setConversationLoadingMore] = useState(false);
  const [conversationPickerPosition, setConversationPickerPosition] = useState({ top: 0, left: 0 });
  const [attachments, setAttachments] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [fileDragOver, setFileDragOver] = useState(false);
  const [tabRenamingId, setTabRenamingId] = useState<string | null>(null);
  const [tabRenameValue, setTabRenameValue] = useState('');
  const [conversationMenuId, setConversationMenuId] = useState<string | null>(null);
  const [conversationMenuPosition, setConversationMenuPosition] = useState({ top: 0, left: 0 });
  const [editingQueuedId, setEditingQueuedId] = useState<string | null>(null);
  const [editingQueuedText, setEditingQueuedText] = useState('');
  const streamingKeys = useStreamingConversations();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const conversationPickerButtonRef = useRef<HTMLButtonElement>(null);
  const activeConversationOptionRef = useRef<HTMLDivElement>(null);
  const conversationMenuButtonRef = useRef<HTMLButtonElement>(null);
  const conversationMenuRef = useRef<HTMLDivElement>(null);
  const messageCanvasRef = useRef<HTMLDivElement>(null);
  const followLatestRef = useRef(true);
  const [activityExpandSignal, setActivityExpandSignal] = useState(0);
  const pendingCaret = useRef<number | null>(null);
  const mentionCache = useRef<Map<string, WorkspaceEntry[]>>(new Map());
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionStart, setMentionStart] = useState(0);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionListing, setMentionListing] = useState<WorkspaceEntry[]>([]);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(0);
  const modelPickerRef = useDismissibleLayer<HTMLDivElement>(modelOpen, () => setModelOpen(false));
  const contextPopoverRef = useDismissibleLayer<HTMLDivElement>(contextOpen, () => {
    if (compacting) return;
    setContextOpen(false);
    setContextConfirming(false);
    setContextFocus('');
  });
  const featureMenuRef = useDismissibleLayer<HTMLDivElement>(featureMenuOpen, () => setFeatureMenuOpen(false));
  const goalControlRef = useDismissibleLayer<HTMLDivElement>(goalOpen, () => {
    setGoalOpen(false);
    setGoalEditing(false);
    setSubgoalAdding(false);
  });
  const conversationPickerRef = useDismissibleLayer<HTMLDivElement>(conversationPickerOpen, () => {
    setConversationPickerOpen(false);
    setConversationSearch('');
    setConversationMenuId(null);
  });
  const mobileActionsRef = useDismissibleLayer<HTMLDivElement>(mobileActionsOpen, () => setMobileActionsOpen(false));
  // The provider runtime stores one internal transport; find the upstream account that owns
  // the selected routed model so the picker can still highlight it.
  const currentProvider = providers.find((provider) =>
    provider.id === agent.provider
    || provider.available_models?.includes(agent.model)
    || provider.default_model === agent.model,
  );
  const currentModelLabel = agent.model || currentProvider?.default_model || 'Select model';
  const agentPickerQuery = agentPickerSearch.trim().toLowerCase();
  const filteredAgents = agents.filter((item) => !agentPickerQuery
    || item.title.toLowerCase().includes(agentPickerQuery)
    || item.model.toLowerCase().includes(agentPickerQuery));
  const conversationQuery = conversationSearch.trim().toLowerCase();
  const filteredConversations = agent.conversations.filter((item) => !conversationQuery
    || item.title.toLowerCase().includes(conversationQuery)
    || item.id.toLowerCase().includes(conversationQuery));

  useEffect(() => {
    setAgentPickerOpen(false);
    setAgentPickerSearch('');
    setMobileActionsOpen(false);
    setConversationPickerOpen(false);
    setConversationSearch('');
    setConversationMenuId(null);
    setContextOpen(false);
    setContextConfirming(false);
    setContextFocus('');
    setFeatureMenuOpen(false);
    setSelectedFeature(undefined);
    setGoalOpen(false);
    setGoalEditing(false);
    setSubgoalAdding(false);
    setGoalBarExpanded(false);
  }, [agent.id]);

  useEffect(() => {
    setContextOpen(false);
    setContextConfirming(false);
    setContextFocus('');
    setFeatureMenuOpen(false);
    setSelectedFeature(undefined);
    setGoalOpen(false);
    setGoalEditing(false);
    setSubgoalAdding(false);
    setGoalBarExpanded(false);
  }, [activeConversation?.id]);

  useEffect(() => {
    if (!conversationPickerOpen || conversationQuery) return;
    const option = activeConversationOptionRef.current;
    if (typeof option?.scrollIntoView === 'function') option.scrollIntoView({ block: 'center' });
  }, [activeConversation?.id, conversationPickerOpen, conversationQuery]);

  useEffect(() => {
    if (!conversationMenuId) return;
    conversationMenuRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus();
  }, [conversationMenuId]);

  useEffect(() => {
    if (!conversationPickerOpen) setConversationMenuId(null);
  }, [conversationPickerOpen]);
  // Only the final answer of a turn is shown as a chat bubble. Intermediate
  // tool-call messages (finish_reason "tool_calls") are hidden here and instead
  // folded into the turn's collapsible "thinking" run. Live streaming messages
  // (no finish reason yet) always show.
  const isFinalAnswer = (message: ChatMessage) =>
    message.streaming
    || message.finishReason === 'stop'
    || (!message.finishReason && !message.toolCalls && message.content.trim().length > 0);
  const visibleMessages = messages.filter((message) =>
    message.role === 'user' || (message.role === 'assistant' && isFinalAnswer(message)));
  const assistantMessages = visibleMessages.filter((message) => message.role === 'assistant');
  const lastAssistantId = assistantMessages.at(-1)?.id;
  const unpositionedRuns = runs.filter((run) => run.insertBeforeMessageId === undefined);
  const fallbackRuns = new Map<string | number, ChatRun[]>();
  const fallbackOffset = Math.max(0, assistantMessages.length - unpositionedRuns.length);
  unpositionedRuns.forEach((run, index) => {
    const message = assistantMessages[Math.min(fallbackOffset + index, assistantMessages.length - 1)];
    if (!message) return;
    fallbackRuns.set(message.id, [...(fallbackRuns.get(message.id) ?? []), run]);
  });
  const runsForMessage = (message: ChatMessage) => message.role === 'assistant' ? [
    ...runs.filter((run) => run.insertBeforeMessageId === message.id),
    ...(fallbackRuns.get(message.id) ?? []),
  ] : [];
  const liveRun = [...runs].reverse().find((run) =>
    run.status === 'running' || run.status === 'waiting_for_approval');
  const showLiveActivity = Boolean(liveRun && (streaming || liveRun.status === 'waiting_for_approval'));
  const contextMessageCount = usage?.messages ?? messages.length;
  const canCompactContext = Boolean(
    activeConversation
    && contextMessageCount >= 4
    && !streaming
    && !compacting
    && onCompactContext,
  );
  const activeTaskCount = runs.filter((run) => run.status === 'running' || run.status === 'waiting_for_approval').length;
  const delegationWorkers = runs.flatMap((run) => run.steps.flatMap((step) => step.delegation?.workers ?? []));
  const activeWorkerCount = delegationWorkers.filter((worker) => worker.status === 'running').length;
  const queuedWorkerCount = delegationWorkers.filter((worker) => worker.status === 'queued').length;
  const requestStop = () => setStopConfirmOpen(true);

  const confirmContextCompaction = async () => {
    if (!canCompactContext || !onCompactContext) return;
    try {
      await onCompactContext(contextFocus.trim() || undefined);
      setContextOpen(false);
      setContextConfirming(false);
      setContextFocus('');
    } catch {
      // The hook exposes a sanitized inline error inside the context popover.
    }
  };

  useEffect(() => {
    followLatestRef.current = true;
    const canvas = messageCanvasRef.current;
    if (canvas) canvas.scrollTop = canvas.scrollHeight;
  }, [agent.id, activeConversation?.id]);

  useEffect(() => {
    const canvas = messageCanvasRef.current;
    if (canvas && followLatestRef.current) canvas.scrollTop = canvas.scrollHeight;
  }, [messages, queuedMessages, runs, chatError]);

  const onTranscriptScroll = () => {
    const canvas = messageCanvasRef.current;
    if (!canvas) return;
    followLatestRef.current = canvas.scrollHeight - canvas.scrollTop - canvas.clientHeight <= 80;
  };

  const revealLiveActivity = () => {
    if (!liveRun) return;
    setActivityExpandSignal((value) => value + 1);
    followLatestRef.current = true;
    window.setTimeout(() => {
      const canvas = messageCanvasRef.current;
      const target = canvas
        ? Array.from(canvas.querySelectorAll<HTMLElement>('.run-steps'))
          .find((element) => element.dataset.runId === liveRun.id)
        : undefined;
      if (target && typeof target.scrollIntoView === 'function') {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
      } else if (canvas) {
        canvas.scrollTop = canvas.scrollHeight;
      }
    }, 0);
  };

  const submit = () => {
    if (compacting) return;
    const value = input.trim();
    if (!value && attachments.length === 0) return;
    const refs = attachments.map((path) => `\`${path}\``).join('\n');
    const message = [refs, value].filter(Boolean).join('\n\n');
    void onSend(message, selectedFeature);
    setInput('');
    setAttachments([]);
    setSelectedFeature(undefined);
  };

  const openGoalEditor = () => {
    setGoalDraft(goal?.objective ?? '');
    setGoalEditing(true);
    setSubgoalAdding(false);
    setGoalOpen(true);
  };

  const saveGoal = async () => {
    const objective = goalDraft.trim();
    if (!objective || goalPending) return;
    await onUpdateGoal(objective, goal?.maxTurns, goal?.contract);
    setGoalOpen(false);
    setGoalEditing(false);
  };

  const updateGoalMaxTurns = async (turns: number) => {
    if (goalConfigPending || turns === goalMaxTurns) return;
    setGoalConfigPending(true);
    setGoalConfigError('');
    try {
      await onGoalMaxTurnsChange(turns);
    } catch (value) {
      setGoalConfigError(value instanceof Error ? value.message : 'Could not save goal configuration.');
    } finally {
      setGoalConfigPending(false);
    }
  };

  const saveSubgoal = async () => {
    const text = subgoalDraft.trim();
    if (!text || goalPending) return;
    await onAddSubgoal(text);
    setSubgoalDraft('');
    setSubgoalAdding(false);
  };

  const basename = (path: string) => path.split('/').filter(Boolean).pop() ?? path;

  const startTabRename = (conversation: Conversation) => {
    setTabRenamingId(conversation.id);
    setTabRenameValue(conversation.title);
  };

  const commitTabRename = (conversation: Conversation) => {
    const value = tabRenameValue.trim();
    if (value && value !== conversation.title) void onRenameConversation(conversation.id, value);
    setTabRenamingId(null);
  };

  const copyConversationLink = async (conversation: Conversation) => {
    const path = `/agents/${encodeURIComponent(agent.id)}/sessions/${encodeURIComponent(conversation.id)}`;
    await navigator.clipboard?.writeText(new URL(path, window.location.origin).toString());
    setConversationMenuId(null);
  };

  const openConversationMenu = (conversationId: string, button: HTMLElement) => {
    const rect = button.getBoundingClientRect();
    const menuHeight = 112;
    setConversationMenuPosition({
      top: rect.bottom + menuHeight > window.innerHeight ? Math.max(8, rect.top - menuHeight) : rect.bottom + 4,
      left: Math.max(8, Math.min(rect.right - 190, window.innerWidth - 198)),
    });
    setConversationMenuId((current) => current === conversationId ? null : conversationId);
  };

  const deleteConversationFromPicker = (conversationId: string) => {
    setConversationMenuId(null);
    setConversationPickerOpen(false);
    setConversationSearch('');
    onDeleteConversation(conversationId);
  };

  const onConversationMenuKeyDown = (event: KeyboardEvent<HTMLDivElement>, conversation: Conversation) => {
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="menuitem"]'));
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      items[event.key === 'Home' ? 0 : items.length - 1]?.focus();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      setConversationMenuId(null);
      conversationMenuButtonRef.current?.focus();
    } else if (event.key === 'F2') {
      event.preventDefault();
      setConversationMenuId(null);
      startTabRename(conversation);
    } else if (event.key === 'Delete') {
      event.preventDefault();
      deleteConversationFromPicker(conversation.id);
    } else if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === 'c') {
      event.preventDefault();
      void copyConversationLink(conversation);
    }
  };

  const addAttachment = (path: string) => {
    const clean = path.trim();
    if (clean) setAttachments((prev) => (prev.includes(clean) ? prev : [...prev, clean]));
  };

  const carriesFile = (event: DragEvent) => Array.from(event.dataTransfer.types).includes(WORKSPACE_FILE_MIME);

  const onComposerDragOver = (event: DragEvent) => {
    if (!carriesFile(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    setDragOver(true);
  };

  const onComposerDragLeave = (event: DragEvent) => {
    if (event.currentTarget.contains(event.relatedTarget as Node)) return;
    setDragOver(false);
  };

  const onComposerDrop = (event: DragEvent) => {
    const path = event.dataTransfer.getData(WORKSPACE_FILE_MIME);
    if (!path) return;
    event.preventDefault();
    setDragOver(false);
    addAttachment(path);
  };

  // --- OS file drop → upload into the currently open workspace directory ---
  const canUpload = Boolean(onUploadFiles);
  const fileDragDepth = useRef(0);

  const dropCarriesOsFiles = (event: DragEvent) => {
    const dt = event.dataTransfer;
    if (!dt) return false;
    // Ignore internal workspace-file drags (those become composer attachments).
    if (Array.from(dt.types).includes(WORKSPACE_FILE_MIME)) return false;
    return Array.from(dt.types).some((type) => type === 'Files' || type === 'application/x-moz-file');
  };

  const onCanvasDragEnter = (event: DragEvent) => {
    if (!canUpload || !dropCarriesOsFiles(event)) return;
    event.preventDefault();
    fileDragDepth.current += 1;
    setFileDragOver(true);
  };

  const onCanvasDragOver = (event: DragEvent) => {
    if (!canUpload || !dropCarriesOsFiles(event)) return;
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  };

  const onCanvasDragLeave = (event: DragEvent) => {
    if (!canUpload || !dropCarriesOsFiles(event)) return;
    fileDragDepth.current = Math.max(0, fileDragDepth.current - 1);
    if (fileDragDepth.current === 0) setFileDragOver(false);
  };

  const onCanvasDrop = (event: DragEvent) => {
    if (!canUpload || !dropCarriesOsFiles(event)) return;
    event.preventDefault();
    fileDragDepth.current = 0;
    setFileDragOver(false);

    // Capture entries synchronously — DataTransfer becomes invalid after await.
    const fsEntries: FileSystemEntry[] = [];
    const items = event.dataTransfer?.items;
    if (items && items.length && typeof items[0].webkitGetAsEntry === 'function') {
      for (const item of Array.from(items)) {
        const entry = item.webkitGetAsEntry();
        if (entry) fsEntries.push(entry);
      }
    }

    if (fsEntries.length && onUploadTree) {
      void readDroppedEntries(fsEntries).then((collected) => {
        if (collected.length) void onUploadTree(collected);
      });
      return;
    }

    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length) void onUploadFiles?.(files);
  };

  // --- @mention file autocomplete ---
  const dirOf = (path: string) => path.split('/').filter(Boolean).slice(0, -1).join('/');

  const mentionDir = useMemo(() => {
    const slash = mentionQuery.lastIndexOf('/');
    return slash >= 0 ? mentionQuery.slice(0, slash) : '';
  }, [mentionQuery]);

  const mentionTerm = useMemo(() => {
    const slash = mentionQuery.lastIndexOf('/');
    return (slash >= 0 ? mentionQuery.slice(slash + 1) : mentionQuery).toLowerCase();
  }, [mentionQuery]);

  const suggestions = useMemo(() => {
    const matches = mentionListing.filter((entry) => entry.name.toLowerCase().includes(mentionTerm));
    return matches
      .sort((a, b) => (a.type !== b.type ? (a.type === 'directory' ? -1 : 1) : a.name.localeCompare(b.name)))
      .slice(0, 8);
  }, [mentionListing, mentionTerm]);

  // Reset per-agent state.
  useEffect(() => { mentionCache.current.clear(); setMentionOpen(false); }, [agent.id]);

  // Only calls the list API when the directory segment changes (i.e. after a "/").
  useEffect(() => {
    if (!mentionOpen) return;
    const dir = mentionDir;
    const cached = mentionCache.current.get(dir);
    if (cached) { setMentionListing(cached); return; }
    const controller = new AbortController();
    setMentionLoading(true);
    workspaceApi.list(agent.id, dir, controller.signal)
      .then((entries) => { mentionCache.current.set(dir, entries); setMentionListing(entries); })
      .catch(() => { if (!controller.signal.aborted) setMentionListing([]); })
      .finally(() => { if (!controller.signal.aborted) setMentionLoading(false); });
    return () => controller.abort();
  }, [mentionOpen, mentionDir, agent.id]);

  useEffect(() => { setMentionIndex(0); }, [mentionQuery, mentionListing]);

  // Restore caret after programmatic edits.
  useEffect(() => {
    if (pendingCaret.current != null && textareaRef.current) {
      const pos = pendingCaret.current;
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(pos, pos);
      pendingCaret.current = null;
    }
  });

  // Auto-grow the composer to fit its content, scrolling once it hits the
  // CSS max-height. Runs on every input change, including programmatic edits
  // (mention insertion) and clearing after submit.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  const detectMention = (text: string, caret: number) => {
    let i = caret - 1;
    while (i >= 0 && !/\s/.test(text[i])) {
      if (text[i] === '@') {
        if (i === 0 || /\s/.test(text[i - 1])) {
          setMentionStart(i);
          setMentionQuery(text.slice(i + 1, caret));
          setMentionOpen(true);
          return;
        }
        break;
      }
      i -= 1;
    }
    setMentionOpen(false);
  };

  const onInputChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const text = event.target.value;
    setInput(text);
    detectMention(text, event.target.selectionStart ?? text.length);
  };

  const applyMention = (entry: WorkspaceEntry) => {
    const isDir = entry.type === 'directory';
    const insert = `@${entry.path}${isDir ? '/' : ' '}`;
    const caret = textareaRef.current?.selectionStart ?? (mentionStart + 1 + mentionQuery.length);
    const before = input.slice(0, mentionStart);
    const after = input.slice(caret);
    setInput(before + insert + after);
    pendingCaret.current = before.length + insert.length;
    if (isDir) {
      setMentionQuery(`${entry.path}/`);
      setMentionOpen(true);
      setMentionIndex(0);
    } else {
      setMentionOpen(false);
    }
  };

  const openMentionManually = () => {
    const el = textareaRef.current;
    if (!el) return;
    const caret = el.selectionStart ?? input.length;
    let i = caret - 1;
    while (i >= 0 && !/\s/.test(input[i]) && input[i] !== '@') i -= 1;
    if (i >= 0 && input[i] === '@' && (i === 0 || /\s/.test(input[i - 1]))) {
      setMentionStart(i);
      setMentionQuery(input.slice(i + 1, caret));
      setMentionOpen(true);
      return;
    }
    const before = input.slice(0, caret);
    const after = input.slice(caret);
    const prefix = before.length && !/\s$/.test(before) ? ' @' : '@';
    setInput(before + prefix + after);
    const at = before.length + prefix.length - 1;
    setMentionStart(at);
    setMentionQuery('');
    setMentionOpen(true);
    pendingCaret.current = at + 1;
  };

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (mentionOpen && suggestions.length > 0) {
      if (event.key === 'ArrowDown') { event.preventDefault(); setMentionIndex((i) => (i + 1) % suggestions.length); return; }
      if (event.key === 'ArrowUp') { event.preventDefault(); setMentionIndex((i) => (i - 1 + suggestions.length) % suggestions.length); return; }
      if (event.key === 'Enter' || event.key === 'Tab') { event.preventDefault(); applyMention(suggestions[mentionIndex]); return; }
      if (event.key === 'Escape') { event.preventDefault(); setMentionOpen(false); return; }
    }
    if (event.ctrlKey && (event.key === ' ' || event.code === 'Space')) {
      event.preventDefault();
      openMentionManually();
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };
  return (
    <>
      <header className="top-bar">
        <button
          className="mobile-manage-trigger"
          aria-label={t('nav.manage', { defaultValue: 'Manage' })}
          onClick={() => {
            setAgentPickerOpen(false);
            setConversationPickerOpen(false);
            setMobileActionsOpen(false);
            onOpenManage?.();
          }}
        >
          <Menu size={21} />
        </button>
        <div className="agent-switch">
          <button
            aria-haspopup="listbox"
            aria-expanded={agentPickerOpen}
            onClick={() => {
              setConversationPickerOpen(false);
              setMobileActionsOpen(false);
              setAgentPickerOpen((open) => !open);
            }}
          >
            <Bot size={18} />
            <span className="agent-switch-name">{agent.title}</span>
            <ChevronDown size={16} />
          </button>
          {agentPickerOpen && (
            <>
              <div className="agent-switch-catcher" onClick={() => setAgentPickerOpen(false)} />
              <div className="agent-switch-popover">
                <div className="agent-switch-popover-header">
                  <strong>{t('agents.title')}</strong>
                  <button
                    className="agent-switch-new"
                    onClick={() => {
                      setAgentPickerOpen(false);
                      onNewAgent?.();
                    }}
                  >
                    <Plus size={15} /> {t('agents.new')}
                  </button>
                </div>
                <div className="agent-switch-search">
                  <Search size={14} />
                  <input
                    value={agentPickerSearch}
                    onChange={(event) => setAgentPickerSearch(event.target.value)}
                    placeholder={t('agents.searchPlaceholder')}
                    autoFocus
                  />
                </div>
                <div className="agent-switch-list" role="listbox" aria-label={t('agents.switch')}>
                  {filteredAgents.map((item) => (
                    <button
                      key={item.id}
                      className={item.id === agent.id ? 'active' : ''}
                      role="option"
                      aria-selected={item.id === agent.id}
                      onClick={() => {
                        setAgentPickerOpen(false);
                        onSelectAgent(item);
                      }}
                    >
                      <span className="status-dot" />
                      <span><strong>{item.title}</strong><small>{item.model}</small></span>
                      {item.id === agent.id && <Check size={14} />}
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}
        </div>

        <div className={mobileActionsOpen ? 'top-actions open' : 'top-actions'} ref={mobileActionsRef}>
          <button
            className="mobile-actions-toggle"
            title={t('agents.menu', { defaultValue: 'Agent actions' })}
            aria-label={t('agents.menu', { defaultValue: 'Agent actions' })}
            aria-expanded={mobileActionsOpen}
            onClick={() => {
              setAgentPickerOpen(false);
              setConversationPickerOpen(false);
              setMobileActionsOpen((open) => !open);
            }}
          >
            <MoreHorizontal size={18} />
          </button>
          <button className="top-action-item" title={t('agents.test')} onClick={() => { setMobileActionsOpen(false); onTestAgent(); }}>
            <Gauge size={17} />
            <span className="top-action-label">{t('agents.test')}</span>
          </button>
        </div>
      </header>

      <div className="conversation-navigation">
        <div className="conversation-picker" ref={conversationPickerRef}>
          <button
            className="conversation-trigger"
            title={t('chat.showAllConversations')}
            aria-label={`${t('chat.showAllConversations')}: ${activeConversation?.title ?? t('chat.newConversation')}`}
            aria-haspopup="listbox"
            aria-expanded={conversationPickerOpen}
            ref={conversationPickerButtonRef}
            onClick={() => {
              setAgentPickerOpen(false);
              setMobileActionsOpen(false);
              const rect = conversationPickerButtonRef.current?.getBoundingClientRect();
              if (rect) setConversationPickerPosition({
                top: rect.bottom + 7,
                left: Math.max(8, Math.min(rect.left, window.innerWidth - 392)),
              });
              setConversationSearch('');
              setConversationPickerOpen((open) => !open);
            }}
          >
            {activeConversation && streamingKeys.includes(`${agent.id}::${activeConversation.id}`)
              ? <span className="tab-stream-dot" title={t('queue.streaming')} />
              : <MessageSquarePlus size={15} />}
            <span className="conversation-trigger-copy">
              <strong>{activeConversation?.title ?? t('chat.newConversation')}</strong>
              <small>{t('chat.sessionCount', { count: agent.conversations.length, defaultValue: '{{count}} sessions' })}</small>
            </span>
            <ChevronDown size={16} />
          </button>
          {conversationPickerOpen && (
            <div className="conversation-picker-popover" style={conversationPickerPosition}>
              <div className="conversation-picker-search">
                <Search size={14} />
                <input
                  value={conversationSearch}
                  onChange={(event) => {
                    setConversationSearch(event.target.value);
                    setConversationMenuId(null);
                  }}
                  placeholder={t('chat.searchConversations', { defaultValue: 'Search sessions…' })}
                  autoFocus
                />
              </div>
              <button
                className="conversation-create-option"
                onClick={() => {
                  setConversationPickerOpen(false);
                  setConversationSearch('');
                  onCreateConversation();
                }}
              >
                <Plus size={15} />
                <span>{t('chat.createConversation')}</span>
              </button>
              <div
                className="conversation-picker-list"
                role="listbox"
                aria-label={t('chat.showAllConversations')}
                onScroll={(event) => {
                  const list = event.currentTarget;
                  if (
                    conversationHasMore
                    && !conversationLoadingMore
                    && list.scrollHeight - list.scrollTop - list.clientHeight < 80
                  ) {
                    setConversationLoadingMore(true);
                    void Promise.resolve(onLoadMoreConversations?.()).finally(() => setConversationLoadingMore(false));
                  }
                }}
              >
                {filteredConversations.map((conversation) => {
                  const selected = conversation.id === activeConversation?.id;
                  const tabStreaming = streamingKeys.includes(`${agent.id}::${conversation.id}`);
                  return (
                    <div
                      key={conversation.id}
                      ref={selected ? activeConversationOptionRef : undefined}
                      role="option"
                      tabIndex={0}
                      aria-selected={selected}
                      onClick={() => {
                        onSelectConversation(conversation.id);
                        setConversationPickerOpen(false);
                        setConversationSearch('');
                      }}
                      onDoubleClick={(event) => {
                        event.stopPropagation();
                        startTabRename(conversation);
                      }}
                      onKeyDown={(event) => {
                        if (event.target !== event.currentTarget) return;
                        if (event.key === 'Enter' || event.key === ' ') {
                          event.preventDefault();
                          onSelectConversation(conversation.id);
                          setConversationPickerOpen(false);
                          setConversationSearch('');
                        } else if (event.key === 'F2') {
                          event.preventDefault();
                          startTabRename(conversation);
                        } else if (event.key === 'Delete') {
                          event.preventDefault();
                          deleteConversationFromPicker(conversation.id);
                        } else if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === 'c') {
                          event.preventDefault();
                          void copyConversationLink(conversation);
                        } else if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                          event.preventDefault();
                          const button = event.currentTarget.querySelector<HTMLButtonElement>('.conversation-option-menu-trigger');
                          if (button) openConversationMenu(conversation.id, button);
                        }
                      }}
                    >
                      {tabStreaming ? <span className="tab-stream-dot" /> : <MessageSquarePlus size={14} />}
                      {tabRenamingId === conversation.id ? (
                        <input
                          className="chat-tab-rename"
                          value={tabRenameValue}
                          autoFocus
                          aria-label={t('conversation.renameLabel')}
                          onClick={(event) => event.stopPropagation()}
                          onDoubleClick={(event) => event.stopPropagation()}
                          onFocus={(event) => event.currentTarget.select()}
                          onChange={(event) => setTabRenameValue(event.target.value)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter') { event.preventDefault(); commitTabRename(conversation); }
                            if (event.key === 'Escape') { event.preventDefault(); setTabRenamingId(null); }
                          }}
                          onBlur={() => commitTabRename(conversation)}
                        />
                      ) : (
                        <span><strong>{conversation.title}</strong><small>{conversationTime(conversation)}</small></span>
                      )}
                      {selected && <Check size={14} />}
                      {tabRenamingId !== conversation.id && (
                        <button
                          ref={conversationMenuId === conversation.id ? conversationMenuButtonRef : undefined}
                          className="conversation-option-menu-trigger"
                          aria-label={t('conversation.menuFor', { name: conversation.title, defaultValue: 'Options for {{name}}' })}
                          title={t('conversation.menu', { defaultValue: 'Session options' })}
                          aria-haspopup="menu"
                          aria-expanded={conversationMenuId === conversation.id}
                          onDoubleClick={(event) => event.stopPropagation()}
                          onClick={(event) => {
                            event.stopPropagation();
                            openConversationMenu(conversation.id, event.currentTarget);
                          }}
                        >
                          <MoreHorizontal size={16} />
                        </button>
                      )}
                      {conversationMenuId === conversation.id && (
                        <div
                          ref={conversationMenuRef}
                          className="conversation-option-menu"
                          role="menu"
                          aria-label={t('conversation.menuFor', { name: conversation.title, defaultValue: 'Options for {{name}}' })}
                          style={conversationMenuPosition}
                          onClick={(event) => event.stopPropagation()}
                          onDoubleClick={(event) => event.stopPropagation()}
                          onKeyDown={(event) => onConversationMenuKeyDown(event, conversation)}
                        >
                          <button role="menuitem" onClick={() => { setConversationMenuId(null); startTabRename(conversation); }}>
                            <Pencil size={14} /><span>{t('conversation.rename')}</span><kbd>F2</kbd>
                          </button>
                          <button className="danger" role="menuitem" onClick={() => deleteConversationFromPicker(conversation.id)}>
                            <Trash2 size={14} /><span>{t('conversation.delete')}</span><kbd>Del</kbd>
                          </button>
                          <button role="menuitem" onClick={() => void copyConversationLink(conversation)}>
                            <Copy size={14} /><span>{t('conversation.copyLink', { defaultValue: 'Copy link' })}</span><kbd>{navigator.platform?.includes('Mac') ? '⇧⌘C' : 'Ctrl+Shift+C'}</kbd>
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
                {filteredConversations.length === 0 && (
                  <p>{t('chat.noMatchingConversations', { defaultValue: 'No matching sessions.' })}</p>
                )}
                {(conversationHasMore || conversationLoadingMore) && (
                  <button
                    className="conversation-load-more"
                    disabled={conversationLoadingMore}
                    onClick={() => {
                      if (conversationLoadingMore) return;
                      setConversationLoadingMore(true);
                      void Promise.resolve(onLoadMoreConversations?.()).finally(() => setConversationLoadingMore(false));
                    }}
                  >
                    {conversationLoadingMore
                      ? t('chat.loadingOlderSessions', { defaultValue: 'Loading older sessions…' })
                      : t('chat.loadOlderSessions', { defaultValue: 'Load older sessions' })}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      <div
        className={fileDragOver ? 'message-canvas file-drag-over' : 'message-canvas'}
        ref={messageCanvasRef}
        onDragEnter={onCanvasDragEnter}
        onDragOver={onCanvasDragOver}
        onDragLeave={onCanvasDragLeave}
        onDrop={onCanvasDrop}
        onScroll={onTranscriptScroll}
      >
        {fileDragOver && (
          <div className="chat-dropzone">
            <UploadCloud size={34} />
            <p>{t('chat.dropToUpload')}</p>
            <span>{t('chat.dropTarget', { dir: workspaceCwd ? `/${workspaceCwd}` : 'Workspace' })}</span>
          </div>
        )}
        {chatStatus === 'loading' ? <AsyncState status="loading" /> : chatStatus === 'error' && messages.length === 0 ? (
          <AsyncState status="error" error={chatError} onRetry={onRetry} />
        ) : visibleMessages.length === 0 && queuedMessages.length === 0 && runs.length === 0 ? (
          !activeConversation && missingConversationId ? (
            <div className="chat-empty" role="status">
              <span className="chat-empty-icon subtle"><MessageSquarePlus size={26} /></span>
              <strong>Session not found</strong>
              <p className="chat-empty-desc">This session may have been removed or is not available to this agent.</p>
              <button type="button" className="conn-btn primary" onClick={onCreateConversation}>Start a new session</button>
            </div>
          ) : !activeConversation ? (
            <div className="chat-empty">
              <button type="button" className="chat-empty-card" onClick={onCreateConversation}>
                <span className="chat-empty-icon"><MessageSquarePlus size={28} /></span>
                <strong>{t('chat.emptyTitle')}</strong>
                <span className="chat-empty-desc">{t('chat.emptyDesc', { name: agent.title })}</span>
                <span className="chat-empty-btn"><Plus size={16} /> {t('chat.newConversation')}</span>
              </button>
            </div>
          ) : (
            <div className="chat-empty">
              <span className="chat-empty-icon subtle"><MessageSquarePlus size={26} /></span>
              <p className="chat-empty-desc">{t('chat.emptyReady', { name: agent.title })}</p>
            </div>
          )
        ) : visibleMessages.map((message) => (
          <Fragment key={message.id}>
            {message.role === 'user' ? (
              <UserMessage content={message.content} onOpenFile={onOpenFile} />
            ) : (
              <article className={`assistant-message ${message.streaming ? 'streaming' : ''}`}>
                {runsForMessage(message).map((run) => (
                  <RunSteps
                    key={run.id}
                    run={run}
                    answerContent={message.content}
                    onResolveApproval={onResolveRunApproval}
                    onStop={requestStop}
                    expandSignal={liveRun?.id === run.id ? activityExpandSignal : 0}
                  />
                ))}
                <div className="message-content">
                  {message.content
                    ? <Markdown content={message.content} onOpenFile={onOpenFile} />
                    : message.streaming ? <TypingIndicator /> : null}
                </div>
                {runsForMessage(message).map((run) => <RunUsage key={`usage-${run.id}`} run={run} />)}
                {!message.streaming && (
                  <div className="message-actions">
                    <button title="Copy" onClick={() => void navigator.clipboard?.writeText(message.content)}><Copy size={16} /></button>
                    <button title="Retry" onClick={onRetry}><RefreshCw size={16} /></button>
                  </div>
                )}
              </article>
            )}
          </Fragment>
        ))}
        {lastAssistantId === undefined && unpositionedRuns.map((run) => (
          <RunSteps
            key={run.id}
            run={run}
            onResolveApproval={onResolveRunApproval}
            onStop={requestStop}
            expandSignal={liveRun?.id === run.id ? activityExpandSignal : 0}
          />
        ))}
        {chatError && messages.length > 0 && <div className="chat-inline-error" role="alert">{chatError}</div>}
      </div>

      <footer className="composer-wrap">
        <div className="composer-stack">
          {goal && (
            <section className={`goal-status-bar ${goal.waiting ? 'waiting' : goal.status}${goalBarExpanded ? ' expanded' : ''}`} aria-label={`Goal ${goal.waiting ? 'waiting' : goal.status}`}>
              <div className="goal-status-row">
                <button
                  type="button"
                  className="goal-status-summary"
                  aria-expanded={goalBarExpanded}
                  onClick={() => setGoalBarExpanded((expanded) => !expanded)}
                >
                  <span className="goal-status-state" aria-hidden="true">
                    {goal.status === 'active'
                      ? <LoaderCircle className={goal.waiting ? '' : 'run-step-spin'} size={15} />
                      : goal.status === 'paused' ? <Pause size={14} /> : <Check size={15} />}
                  </span>
                  <strong>{goal.waiting ? 'Goal waiting' : goal.status === 'active' ? 'Pursuing goal' : goal.status === 'paused' ? 'Goal paused' : 'Goal achieved'}</strong>
                  <span className="goal-status-separator" aria-hidden="true">·</span>
                  <span className="goal-status-objective">{goal.objective}</span>
                  <span className="goal-status-separator goal-status-turn-separator" aria-hidden="true">·</span>
                  <span className="goal-status-turns">{goal.status === 'done' ? `Completed in ${goal.turnsUsed} turns` : `Turn ${goal.turnsUsed}/${goal.maxTurns}`}</span>
                </button>
                <div className="goal-status-actions">
                  <button type="button" title="Edit goal" aria-label="Edit goal" disabled={goalPending} onClick={openGoalEditor}>
                    <Pencil size={13} />
                  </button>
                  {goal.status === 'active' && !goal.waiting ? (
                    <button type="button" title="Pause goal" aria-label="Pause goal" disabled={goalPending} onClick={() => void onPauseGoal()}>
                      <Pause size={13} />
                    </button>
                  ) : (
                    <button type="button" title={goal.status === 'done' ? 'Restart goal' : 'Resume goal'} aria-label={goal.status === 'done' ? 'Restart goal' : 'Resume goal'} disabled={goalPending} onClick={() => void onResumeGoal()}>
                      {goal.status === 'done' ? <RefreshCw size={13} /> : <Play size={13} />}
                    </button>
                  )}
                  <button type="button" className="danger" title="Delete goal" aria-label="Delete goal" disabled={goalPending} onClick={() => setGoalDeleteConfirm(true)}>
                    <Trash2 size={13} />
                  </button>
                  <button
                    type="button"
                    title={goalBarExpanded ? 'Hide goal details' : 'Show goal details'}
                    aria-label={goalBarExpanded ? 'Hide goal details' : 'Show goal details'}
                    aria-expanded={goalBarExpanded}
                    onClick={() => setGoalBarExpanded((expanded) => !expanded)}
                  >
                    {goalBarExpanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
                  </button>
                </div>
              </div>
              {goalBarExpanded && (
                <div className="goal-status-details">
                  <p><strong>Objective</strong><span>{goal.objective}</span></p>
                  {(goal.waitingReason || goal.pausedReason || goal.lastReason) && (
                    <p><strong>Latest check</strong><span>{goal.waitingReason || goal.pausedReason || goal.lastReason}</span></p>
                  )}
                  {goal.contract.verification && (
                    <p><strong>Verification</strong><span>{goal.contract.verification}</span></p>
                  )}
                  {goal.subgoals.length > 0 && (
                    <div className="goal-status-subgoals">
                      <strong>Subgoals</strong>
                      <ol>{goal.subgoals.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ol>
                    </div>
                  )}
                </div>
              )}
            </section>
          )}
          {showLiveActivity && liveRun && (
            <RunActivityBar run={liveRun} onViewActivity={revealLiveActivity} />
          )}
          {(compacting || compactError || compactResult) && (
            <div
              className={`context-compact-status${compactError ? ' error' : compactResult ? ' success' : ''}`}
              role={compactError ? 'alert' : 'status'}
              aria-live="polite"
            >
              {compacting ? <>
                <LoaderCircle className="run-step-spin" size={14} />
                <span>Compacting session context…</span>
              </> : compactError ? <>
                <X size={14} />
                <span>{compactError}</span>
              </> : compactResult ? <>
                <Check size={14} />
                <span>Context compacted · {compactTokens(compactResult.beforeTokens)} → {compactTokens(compactResult.afterTokens)}</span>
              </> : null}
            </div>
          )}
          {queuedMessages.length > 0 && (
            <div className="queue-strip" aria-label={`${queuedMessages.length} queued message${queuedMessages.length === 1 ? '' : 's'}`}>
              {queuedMessages.map((message) => {
                const editing = editingQueuedId === message.id;
                const commitEdit = () => {
                  onEditQueued?.(message.id, editingQueuedText);
                  setEditingQueuedId(null);
                  setEditingQueuedText('');
                };
                if (editing) {
                  return (
                    <div className="queue-item editing" key={message.id}>
                      <textarea
                        className="queue-edit-input"
                        value={editingQueuedText}
                        autoFocus
                        rows={Math.min(6, Math.max(1, editingQueuedText.split('\n').length))}
                        onChange={(e) => setEditingQueuedText(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); commitEdit(); }
                          if (e.key === 'Escape') { setEditingQueuedId(null); setEditingQueuedText(''); }
                        }}
                      />
                      <button className="icon-button" title={t('common.cancel')} onClick={() => { setEditingQueuedId(null); setEditingQueuedText(''); }}>
                        <X size={14} />
                      </button>
                      <button className="icon-button" title={t('common.save')} disabled={!editingQueuedText.trim()} onClick={commitEdit}>
                        <Check size={14} />
                      </button>
                    </div>
                  );
                }
                return (
                  <div className="queue-item" key={message.id}>
                    <Clock size={13} className="queue-item-icon" />
                    <span className="queue-item-text" title={message.content}>{message.content}</span>
                    <div className="queue-item-actions">
                      <button
                        className="icon-button"
                        title="Move queued message up"
                        aria-label="Move queued message up"
                        disabled={!onMoveQueued || queuedMessages[0]?.id === message.id}
                        onClick={() => onMoveQueued?.(message.id, 'up')}
                      >
                        <ChevronUp size={13} />
                      </button>
                      <button
                        className="icon-button"
                        title="Move queued message down"
                        aria-label="Move queued message down"
                        disabled={!onMoveQueued || queuedMessages[queuedMessages.length - 1]?.id === message.id}
                        onClick={() => onMoveQueued?.(message.id, 'down')}
                      >
                        <ChevronDown size={13} />
                      </button>
                      <button className="icon-button" title={t('common.edit')} onClick={() => { setEditingQueuedId(message.id); setEditingQueuedText(message.content); }}>
                        <Pencil size={13} />
                      </button>
                      <button className="icon-button" title={t('common.delete')} onClick={() => onDeleteQueued?.(message.id)}>
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          <div
            className={dragOver ? 'composer drag-over' : 'composer'}
            onDragOver={onComposerDragOver}
            onDragLeave={onComposerDragLeave}
            onDrop={onComposerDrop}
          >
            {mentionOpen && (mentionLoading || suggestions.length > 0) && (
              <div className="mention-popup" role="listbox">
                {mentionLoading && suggestions.length === 0 ? (
                  <div className="mention-loading"><LoaderCircle className="run-step-spin" size={14} /> Searching…</div>
                ) : (
                  suggestions.map((entry, index) => (
                    <button
                      key={entry.path}
                      role="option"
                      aria-selected={index === mentionIndex}
                      className={index === mentionIndex ? 'mention-item active' : 'mention-item'}
                      onMouseEnter={() => setMentionIndex(index)}
                      onMouseDown={(event) => { event.preventDefault(); applyMention(entry); }}
                    >
                      <TreeIcon entry={entry} size={16} />
                      <span className="mention-name">{entry.name}</span>
                      <span className="mention-path">{dirOf(entry.path)}</span>
                    </button>
                  ))
                )}
              </div>
            )}
            {attachments.length > 0 && (
              <div className="composer-attachments">
                {attachments.map((path) => (
                  <span key={path} className="composer-chip" title={path}>
                    <button className="composer-chip-open" onClick={() => onOpenFile(path)}>
                      <FileText size={13} />
                      {basename(path)}
                    </button>
                    <button
                      className="composer-chip-remove"
                      title={t('common.remove', 'Remove')}
                      onClick={() => setAttachments((prev) => prev.filter((p) => p !== path))}
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              ref={textareaRef}
              className="composer-input"
              rows={1}
              placeholder={selectedFeature === 'goal' ? 'Describe the goal, then send' : t('chat.placeholder')}
              value={input}
              onChange={onInputChange}
              onKeyDown={onComposerKeyDown}
              onBlur={() => window.setTimeout(() => setMentionOpen(false), 120)}
              disabled={!activeConversation || compacting}
            />
            <div className="composer-row">
              <div className="composer-shortcuts">
                <div className="composer-add-control" ref={featureMenuRef}>
                  <button
                    className="composer-icon"
                    title="Agent features"
                    aria-label="Agent features"
                    aria-haspopup="menu"
                    aria-expanded={featureMenuOpen}
                    disabled={!activeConversation}
                    onClick={() => { setGoalOpen(false); setFeatureMenuOpen((open) => !open); }}
                  >
                    <Plus size={17} />
                  </button>
                  {featureMenuOpen && (
                    <div className="composer-add-menu" role="menu" aria-label="Agent features">
                      <button role="menuitem" onClick={() => { setSelectedFeature('todo'); setFeatureMenuOpen(false); textareaRef.current?.focus(); }}>
                        <ListTodo size={15} /><span><strong>Todo list</strong><small>Plan and check each step</small></span>
                      </button>
                      <button role="menuitem" onClick={() => { setSelectedFeature('delegate'); setFeatureMenuOpen(false); textareaRef.current?.focus(); }}>
                        <Users size={15} /><span><strong>Sub-agents</strong><small>Delegate independent work</small></span>
                      </button>
                      <button role="menuitem" onClick={() => {
                        setFeatureMenuOpen(false);
                        if (goal) setGoalOpen(true);
                        else {
                          setSelectedFeature('goal');
                          setGoalOpen(false);
                          textareaRef.current?.focus();
                        }
                      }}>
                        <Target size={15} /><span><strong>Goal</strong><small>{goal ? 'View or edit persistent goal' : 'Run until the outcome is met'}</small></span>
                      </button>
                      <button
                        role="menuitem"
                        disabled={!goal}
                        onClick={() => { setFeatureMenuOpen(false); setGoalOpen(true); setSubgoalAdding(true); }}
                      >
                        <Sparkles size={15} /><span><strong>Subgoal</strong><small>Add a completion criterion</small></span>
                      </button>
                      <button role="menuitem" onClick={() => { setSelectedFeature('learn'); setFeatureMenuOpen(false); textareaRef.current?.focus(); }}>
                        <GraduationCap size={15} /><span><strong>Learn</strong><small>Capture reusable knowledge</small></span>
                      </button>
                    </div>
                  )}
                </div>
                {selectedFeature && selectedFeature !== 'goal' && (
                  <button className="composer-feature-chip" onClick={() => setSelectedFeature(undefined)} title="Remove mode">
                    {selectedFeature === 'todo' ? <ListTodo size={14} /> : selectedFeature === 'delegate' ? <Users size={14} /> : <GraduationCap size={14} />}
                    {selectedFeature === 'todo' ? 'Todo list' : selectedFeature === 'delegate' ? 'Sub-agents' : 'Learn'}
                    <X size={12} />
                  </button>
                )}
                {(goal || selectedFeature === 'goal') && <div className={`goal-control${goalOpen ? ' open' : ''}${selectedFeature === 'goal' ? ' selected' : ''}`} ref={goalControlRef}>
                  <button
                    className={`goal-chip ${goal?.status ?? 'idle'}${selectedFeature === 'goal' ? ' selected' : ''}`}
                    title={goal?.objective ?? 'Create a persistent goal'}
                    aria-haspopup="dialog"
                    aria-expanded={goalOpen}
                    disabled={!activeConversation}
                    onClick={() => {
                      setFeatureMenuOpen(false);
                      if (!goal) {
                        setSelectedFeature((feature) => feature === 'goal' ? undefined : 'goal');
                        setGoalOpen(false);
                        textareaRef.current?.focus();
                      } else setGoalOpen((open) => !open);
                    }}
                  >
                    {goal?.status === 'active' ? <LoaderCircle className={goal.waiting ? '' : 'run-step-spin'} size={14} />
                      : goal?.status === 'paused' ? <Pause size={13} />
                        : goal?.status === 'done' ? <Check size={14} /> : <Target size={14} />}
                    <span>{goal?.status === 'active'
                      ? goal.waiting ? `Goal waiting · ${goal.turnsUsed}/${goal.maxTurns}` : `Goal ${goal.turnsUsed}/${goal.maxTurns}`
                      : goal?.status === 'paused' ? `Goal paused · ${goal.turnsUsed}/${goal.maxTurns}`
                        : goal?.status === 'done' ? 'Goal achieved' : 'Goal'}</span>
                  </button>
                  {(goalOpen || goal || selectedFeature === 'goal') && (
                    <div className="goal-popover" role="dialog" aria-label={goal ? 'Goal details' : 'Goal configuration'}>
                      {goalEditing && goal ? (
                        <div className="goal-editor">
                          <div className="goal-popover-title"><Target size={15} /><strong>Edit goal</strong></div>
                          <label>Objective</label>
                          <textarea autoFocus rows={3} value={goalDraft} onChange={(event) => setGoalDraft(event.target.value)} placeholder="What outcome should the agent keep working toward?" />
                          {goalError && <div className="goal-error" role="alert">{goalError}</div>}
                          <div className="goal-actions">
                            <button onClick={() => { setGoalOpen(false); setGoalEditing(false); }}>Cancel</button>
                            <button className="primary" disabled={!goalDraft.trim() || goalPending} onClick={() => void saveGoal()}>
                              {goalPending && <LoaderCircle className="run-step-spin" size={13} />}Save
                            </button>
                          </div>
                        </div>
                      ) : !goal ? (
                        <div className="goal-config">
                          <div className="goal-popover-title"><Target size={15} /><span><strong>Goal mode</strong><small>Send the objective like a normal message</small></span></div>
                          <p>Type only the goal in the chat box. The agent will keep working until it succeeds or reaches its turn budget.</p>
                          <div className="goal-config-heading"><strong>Maximum turns</strong><span>Saved for {agent.title}</span></div>
                          <div className="goal-turn-presets" role="radiogroup" aria-label="Maximum goal turns">
                            {[10, 15, 20, 25, 30].map((turns) => (
                              <button
                                key={turns}
                                type="button"
                                role="radio"
                                aria-checked={goalMaxTurns === turns}
                                className={goalMaxTurns === turns ? 'active' : ''}
                                disabled={goalConfigPending}
                                onClick={() => void updateGoalMaxTurns(turns)}
                              >
                                {turns}
                              </button>
                            ))}
                          </div>
                          {goalConfigError && <div className="goal-error" role="alert">{goalConfigError}</div>}
                        </div>
                      ) : (
                        <div className="goal-details">
                          <div className="goal-popover-title">
                            {goal.status === 'active' ? <LoaderCircle className={goal.waiting ? '' : 'run-step-spin'} size={15} /> : goal.status === 'paused' ? <Pause size={14} /> : <Check size={15} />}
                            <span><strong>Goal · {goal.waiting ? 'Waiting' : goal.status === 'active' ? 'Running' : goal.status === 'paused' ? 'Paused' : 'Achieved'}</strong><small>Turn {goal.turnsUsed}/{goal.maxTurns}</small></span>
                          </div>
                          <p className="goal-objective">{goal.objective}</p>
                          {(goal.lastReason || goal.pausedReason || goal.waitingReason) && <p className="goal-reason">{goal.waitingReason || goal.pausedReason || goal.lastReason}</p>}
                          {goal.subgoals.length > 0 && (
                            <ol className="goal-subgoals">
                              {goal.subgoals.map((item, index) => <li key={`${index}-${item}`}><span>{item}</span><button title="Delete subgoal" onClick={() => void onDeleteSubgoal(index + 1)}><X size={12} /></button></li>)}
                            </ol>
                          )}
                          {subgoalAdding ? (
                            <div className="subgoal-editor"><input autoFocus value={subgoalDraft} onChange={(event) => setSubgoalDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void saveSubgoal(); if (event.key === 'Escape') setSubgoalAdding(false); }} placeholder="Add a completion criterion" /><button disabled={!subgoalDraft.trim() || goalPending} onClick={() => void saveSubgoal()}><Check size={14} /></button></div>
                          ) : <button className="goal-add-subgoal" onClick={() => setSubgoalAdding(true)}><Plus size={13} /> Add subgoal</button>}
                          {goalError && <div className="goal-error" role="alert">{goalError}</div>}
                          <div className="goal-actions">
                            {goal.status === 'active' && !goal.waiting
                              ? <button disabled={goalPending} onClick={() => void onPauseGoal()}><Pause size={13} /> Pause</button>
                              : <button disabled={goalPending} onClick={() => void onResumeGoal()}><Play size={13} /> Resume</button>}
                            <button disabled={goalPending} onClick={openGoalEditor}><Pencil size={13} /> Edit</button>
                            <button className="danger" disabled={goalPending} onClick={() => setGoalDeleteConfirm(true)}><Trash2 size={13} /> Delete</button>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>}
              </div>
              <div className="composer-row-right">
                <div className="composer-model-context">
                  <div className="composer-model-control" ref={modelPickerRef}>
                  <button className="composer-model" title="Select model" onClick={() => setModelOpen((open) => !open)}>
                    {currentModelLabel}
                    <ChevronDown size={14} />
                  </button>
                  {modelOpen && (
                    <div className="model-picker" role="menu">
                      {blends.length > 0 && (
                        <div className="model-provider">
                          <div className="model-provider-name">Blends</div>
                          {blends.map((blendName) => (
                            <button
                              key={`blend:${blendName}`}
                              className={blendName === agent.model ? 'active' : ''}
                              onClick={() => {
                                setModelOpen(false);
                                void onSelectModel('blend', blendName);
                              }}
                            >
                              <span>{blendName === 'auto' ? 'Auto' : blendName}</span>
                              {blendName === agent.model && <Check size={14} />}
                            </button>
                          ))}
                        </div>
                      )}
                      {providers.map((provider) => {
                        const models = provider.available_models?.length
                          ? provider.available_models
                          : provider.default_model ? [provider.default_model] : [];
                        return models.length > 0 && (
                          <div className="model-provider" key={provider.id}>
                            <div className="model-provider-name">{provider.display_name}</div>
                            {models.map((model) => (
                              <button
                                key={model}
                                className={provider.id === currentProvider?.id && model === agent.model ? 'active' : ''}
                                onClick={() => {
                                  setModelOpen(false);
                                  void onSelectModel(provider.id, model);
                                }}
                              >
                                <span>{model}</span>
                                {provider.id === currentProvider?.id && model === agent.model && <Check size={14} />}
                              </button>
                            ))}
                          </div>
                        );
                      })}
                      {providers.every((provider) => !provider.available_models?.length && !provider.default_model) && (
                        <div className="model-picker-empty">No models available</div>
                      )}
                    </div>
                  )}
                  </div>
                  <div className="composer-context-control" ref={contextPopoverRef}>
                    <button
                      className="composer-context-button"
                      title="Session context"
                      aria-label={`Session context. ${usage?.contextUsed ? `${compactTokens(usage.contextUsed)} tokens used.` : 'Usage unavailable.'}`}
                      aria-haspopup="dialog"
                      aria-expanded={contextOpen}
                      onClick={() => {
                        setModelOpen(false);
                        setContextOpen((open) => !open);
                        if (contextOpen) {
                          setContextConfirming(false);
                          setContextFocus('');
                        }
                      }}
                    >
                      <span className="composer-context-divider" aria-hidden="true">·</span>
                      <ContextGauge usage={usage} model={currentModelLabel} />
                    </button>
                    {contextOpen && (
                      <div className="context-popover" role="dialog" aria-label="Session context">
                        <div className="context-popover-head">
                          <span className="context-popover-icon"><Gauge size={15} /></span>
                          <div>
                            <strong>Session context</strong>
                            <span>{usage?.contextUsed ? `${compactTokens(usage.contextUsed)} tokens currently used` : 'Available after the first response'}</span>
                          </div>
                        </div>
                        <div className="context-popover-meter" aria-hidden="true">
                          <i style={{ width: `${Math.max(0, Math.min(100, usage?.contextPressurePercent ?? usage?.contextPercent ?? 0))}%` }} />
                        </div>
                        {usage?.contextThreshold ? (
                          <div className="context-popover-detail">
                            <span>{compactTokens(usage.contextUsed ?? 0)} / {compactTokens(usage.contextThreshold)} to compaction</span>
                            <span>{Math.round(usage.contextPressurePercent ?? 0)}%</span>
                          </div>
                        ) : usage?.contextLimit ? (
                          <div className="context-popover-detail">
                            <span>{compactTokens(usage.contextUsed ?? 0)} / {compactTokens(usage.contextLimit)} model context</span>
                            <span>{Math.round(usage.contextPercent ?? 0)}%</span>
                          </div>
                        ) : null}
                        {!contextConfirming ? <>
                          <p>Summarize older turns while preserving recent messages and this session.</p>
                          <button
                            className="context-compact-primary"
                            disabled={!canCompactContext}
                            onClick={() => setContextConfirming(true)}
                          >
                            {compacting ? <LoaderCircle className="run-step-spin" size={14} /> : null}
                            Compact context
                          </button>
                          {!activeConversation ? <small>Select a session first.</small>
                            : streaming ? <small>Wait for the active response to finish.</small>
                              : contextMessageCount < 4 ? <small>More conversation history is needed.</small>
                                : null}
                        </> : <div className="context-confirm">
                          <strong>Compact session context?</strong>
                          <p>Older turns will be summarized. Recent messages and recoverable history stay available.</p>
                          <label htmlFor="context-compact-focus">Preserve specific details <span>Optional</span></label>
                          <textarea
                            id="context-compact-focus"
                            rows={2}
                            maxLength={500}
                            placeholder="e.g. API decisions and unresolved bugs"
                            value={contextFocus}
                            onChange={(event) => setContextFocus(event.target.value)}
                            disabled={compacting}
                          />
                          {compactError && <div className="context-confirm-error" role="alert">{compactError}</div>}
                          <div className="context-confirm-actions">
                            <button disabled={compacting} onClick={() => setContextConfirming(false)}>Cancel</button>
                            <button className="context-compact-primary" disabled={!canCompactContext} onClick={() => void confirmContextCompaction()}>
                              {compacting && <LoaderCircle className="run-step-spin" size={14} />}
                              Compact context
                            </button>
                          </div>
                        </div>}
                      </div>
                    )}
                  </div>
                </div>
                <span className="composer-divider" />
                {streaming ? (
                  <button
                    className="send-round stop-round"
                    title={canStop ? 'Stop' : 'Starting…'}
                    aria-label={canStop ? 'Stop' : 'Starting'}
                    disabled={!canStop}
                    onClick={requestStop}
                  >
                    {canStop ? <Square size={14} /> : <LoaderCircle className="run-step-spin" size={16} />}
                  </button>
                ) : (
                  <button
                    className="send-round"
                    title="Send"
                    aria-label="Send"
                    disabled={compacting || !activeConversation || (!input.trim() && attachments.length === 0)}
                    onClick={submit}
                  >
                    <ArrowUp size={16} />
                  </button>
                )}
              </div>
            </div>
          </div>

        </div>
      </footer>
      {stopConfirmOpen && (
        <ConfirmDialog
          title="Stop this response?"
          message={activeWorkerCount || queuedWorkerCount
            ? `Stopping will cancel ${activeWorkerCount} active worker${activeWorkerCount === 1 ? '' : 's'} and ${queuedWorkerCount} queued worker${queuedWorkerCount === 1 ? '' : 's'}. Completed and partial output will be kept.`
            : activeTaskCount
            ? `Stopping will cancel ${activeTaskCount} active task${activeTaskCount === 1 ? '' : 's'} and keep all output received so far.`
            : 'Stopping will cancel the active response and keep all output received so far.'}
          confirmLabel="Stop response"
          danger
          onConfirm={() => { setStopConfirmOpen(false); onStop(); }}
          onCancel={() => setStopConfirmOpen(false)}
        />
      )}
      {goalDeleteConfirm && (
        <ConfirmDialog
          title="Delete this goal?"
          message="The persistent goal and its subgoals will be cleared. The current agent turn may finish, but it will not continue toward this goal."
          confirmLabel="Delete goal"
          danger
          onConfirm={() => { setGoalDeleteConfirm(false); setGoalOpen(false); void onDeleteGoal(); }}
          onCancel={() => setGoalDeleteConfirm(false)}
        />
      )}
    </>
  );
}
