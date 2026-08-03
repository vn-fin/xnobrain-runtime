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
  LoaderCircle,
  MessageSquarePlus,
  MoreHorizontal,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Square,
  ThumbsDown,
  ThumbsUp,
  Trash2,
  UploadCloud,
  Wrench,
  X,
} from 'lucide-react';
import { TreeIcon } from './common';
import { useStreamingConversations } from '../hooks/useConversation';
import { useDismissibleLayer } from '../hooks/useDismissibleLayer';
import { AsyncState } from './AsyncState';
import { RunSteps, RunUsage } from './RunSteps';
import { Markdown } from './Markdown';
import { WORKSPACE_FILE_MIME, readDroppedEntries } from './WorkspacePanel';
import { workspaceApi } from '../api/workspace';
import type {
  Agent,
  AsyncStatus,
  ChatMessage,
  ChatRun,
  ConnectionProvider,
  Conversation,
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

export function ContextGauge({ usage, model }: { usage: ConversationUsage | null; model: string }) {
  const used = Math.max(0, usage?.contextUsed ?? 0);
  const limit = Math.max(0, usage?.contextLimit ?? 0);
  const known = used > 0 && limit > 0;
  const percent = known
    ? Math.max(0, Math.min(100, usage?.contextPercent ?? used / limit * 100))
    : 0;
  const modelLabel = model.toLowerCase() === 'auto' ? 'Auto model' : model || 'Selected model';
  const title = known
    ? `${compactTokens(used)} / ${compactTokens(limit)} context (${Number(percent.toFixed(1))}%)`
    : used > 0
      ? `${compactTokens(used)} context used; ${modelLabel} context limit is unavailable`
      : `${modelLabel} context usage is unavailable`;
  return (
    <span
      className={`context-gauge${known ? '' : ' unknown'}`}
      style={known ? { '--context-percent': `${percent}%` } as CSSProperties : undefined}
      role="img"
      aria-label={title}
      title={title}
    />
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
  onSend,
  onStop,
  onResolveRunApproval,
  onRetry,
  onSelectModel,
  onOpenSettings,
  onOpenRuntime,
  onSelectAgent,
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
  onSend: (input: string) => void | Promise<void>;
  onStop: () => void;
  onResolveRunApproval: (runId: string, choice: RunApprovalChoice) => void | Promise<void>;
  onRetry: () => void;
  onSelectModel: (providerId: string, model: string) => void | Promise<void>;
  onOpenSettings: () => void;
  onOpenRuntime: () => void;
  onSelectAgent: (agent: Agent) => void;
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
}) {
  const { t } = useTranslation();
  const [input, setInput] = useState('');
  const [modelOpen, setModelOpen] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [agentPickerSearch, setAgentPickerSearch] = useState('');
  const [mobileActionsOpen, setMobileActionsOpen] = useState(false);
  const [conversationPickerOpen, setConversationPickerOpen] = useState(false);
  const [conversationSearch, setConversationSearch] = useState('');
  const [conversationLoadingMore, setConversationLoadingMore] = useState(false);
  const [conversationPickerPosition, setConversationPickerPosition] = useState({ top: 0, left: 0 });
  const [attachments, setAttachments] = useState<string[]>([]);
  const [dragOver, setDragOver] = useState(false);
  const [fileDragOver, setFileDragOver] = useState(false);
  const [tabRenamingId, setTabRenamingId] = useState<string | null>(null);
  const [tabRenameValue, setTabRenameValue] = useState('');
  const [editingQueuedId, setEditingQueuedId] = useState<string | null>(null);
  const [editingQueuedText, setEditingQueuedText] = useState('');
  const streamingKeys = useStreamingConversations();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const conversationPickerButtonRef = useRef<HTMLButtonElement>(null);
  const activeConversationOptionRef = useRef<HTMLButtonElement>(null);
  const messageCanvasRef = useRef<HTMLDivElement>(null);
  const pendingCaret = useRef<number | null>(null);
  const mentionCache = useRef<Map<string, WorkspaceEntry[]>>(new Map());
  const [mentionOpen, setMentionOpen] = useState(false);
  const [mentionStart, setMentionStart] = useState(0);
  const [mentionQuery, setMentionQuery] = useState('');
  const [mentionListing, setMentionListing] = useState<WorkspaceEntry[]>([]);
  const [mentionLoading, setMentionLoading] = useState(false);
  const [mentionIndex, setMentionIndex] = useState(0);
  const modelPickerRef = useDismissibleLayer<HTMLDivElement>(modelOpen, () => setModelOpen(false));
  const conversationPickerRef = useDismissibleLayer<HTMLDivElement>(conversationPickerOpen, () => {
    setConversationPickerOpen(false);
    setConversationSearch('');
  });
  const mobileActionsRef = useDismissibleLayer<HTMLDivElement>(mobileActionsOpen, () => setMobileActionsOpen(false));
  // Hermes always stores `nine-router`; find the upstream account that owns
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
  }, [agent.id]);

  useEffect(() => {
    if (!conversationPickerOpen || conversationQuery) return;
    const option = activeConversationOptionRef.current;
    if (typeof option?.scrollIntoView === 'function') option.scrollIntoView({ block: 'center' });
  }, [activeConversation?.id, conversationPickerOpen, conversationQuery]);
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
  useEffect(() => {
    const canvas = messageCanvasRef.current;
    if (canvas) canvas.scrollTop = canvas.scrollHeight;
  }, [messages, queuedMessages, runs, chatError]);

  const submit = () => {
    if (streaming) return;
    const value = input.trim();
    if (!value && attachments.length === 0) return;
    const refs = attachments.map((path) => `\`${path}\``).join('\n');
    const message = [refs, value].filter(Boolean).join('\n\n');
    void onSend(message);
    setInput('');
    setAttachments([]);
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
          <button className="top-action-item" title={t('agents.runtimeConfig')} onClick={() => { setMobileActionsOpen(false); onOpenRuntime(); }}>
            <Wrench size={17} />
            <span className="top-action-label">{t('agents.runtimeConfig')}</span>
          </button>
          <button className="top-action-item" title={t('agents.settings')} onClick={() => { setMobileActionsOpen(false); onOpenSettings(); }}>
            <Settings2 size={17} />
            <span className="top-action-label">{t('agents.settings')}</span>
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
                  onChange={(event) => setConversationSearch(event.target.value)}
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
                    <button
                      key={conversation.id}
                      ref={selected ? activeConversationOptionRef : undefined}
                      role="option"
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
                        <span><strong>{conversation.title}</strong><small>{conversation.id}</small></span>
                      )}
                      {selected && <Check size={14} />}
                      <span
                        className="conversation-option-delete"
                        title={t('chat.closeConversation')}
                        onDoubleClick={(event) => event.stopPropagation()}
                        onClick={(event) => {
                          event.stopPropagation();
                          setConversationPickerOpen(false);
                          onDeleteConversation(conversation.id);
                        }}
                      >
                        <X size={13} />
                      </span>
                    </button>
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
          !activeConversation ? (
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
              <div className="user-bubble"><Markdown content={message.content} onOpenFile={onOpenFile} /></div>
            ) : (
              <article className={`assistant-message ${message.streaming ? 'streaming' : ''}`}>
                {runsForMessage(message).map((run) => (
                  <RunSteps key={run.id} run={run} onResolveApproval={onResolveRunApproval} />
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
                    <button title="Good response"><ThumbsUp size={16} /></button>
                    <button title="Bad response"><ThumbsDown size={16} /></button>
                    <button title="Retry"><RefreshCw size={16} /></button>
                    <button title="More"><MoreHorizontal size={17} /></button>
                  </div>
                )}
              </article>
            )}
          </Fragment>
        ))}
        {lastAssistantId === undefined && unpositionedRuns.map((run) => (
          <RunSteps key={run.id} run={run} onResolveApproval={onResolveRunApproval} />
        ))}
        {chatError && messages.length > 0 && <div className="chat-inline-error" role="alert">{chatError}</div>}
      </div>

      <footer className="composer-wrap">
        <div className="composer-stack">
          {queuedMessages.length > 0 && (
            <div className="queue-strip">
              {queuedMessages.map((message, index) => {
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
                      <button className="icon-button" title={t('queue.moveUp')} disabled={index === 0} onClick={() => onMoveQueued?.(message.id, 'up')}>
                        <ChevronUp size={14} />
                      </button>
                      <button className="icon-button" title={t('queue.moveDown')} disabled={index === queuedMessages.length - 1} onClick={() => onMoveQueued?.(message.id, 'down')}>
                        <ChevronDown size={14} />
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
              placeholder={t('chat.placeholder')}
              value={input}
              onChange={onInputChange}
              onKeyDown={onComposerKeyDown}
              onBlur={() => window.setTimeout(() => setMentionOpen(false), 120)}
              disabled={!activeConversation}
            />
            <div className="composer-row">
              <div className="composer-row-right">
                <div className="composer-model-control" ref={modelPickerRef}>
                  <button className="composer-model" title="Model and reasoning" onClick={() => setModelOpen((open) => !open)}>
                    <ContextGauge usage={usage} model={currentModelLabel} />
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
                <span className="composer-divider" />
                {streaming ? (
                  <button
                    className="send-round stop-round"
                    title={canStop ? 'Stop' : 'Starting…'}
                    aria-label={canStop ? 'Stop' : 'Starting'}
                    disabled={!canStop}
                    onClick={onStop}
                  >
                    {canStop ? <Square size={14} /> : <LoaderCircle className="run-step-spin" size={16} />}
                  </button>
                ) : (
                  <button
                    className="send-round"
                    title="Send"
                    aria-label="Send"
                    disabled={!activeConversation || (!input.trim() && attachments.length === 0)}
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
    </>
  );
}
