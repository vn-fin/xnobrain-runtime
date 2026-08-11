import { Fragment, useEffect, useRef, useState, type DragEvent as ReactDragEvent } from 'react';
import {
  Brain,
  Check,
  Clock,
  Coins,
  FileText,
  Loader2,
  MessageSquare,
  Send,
  Square,
  Upload,
  X,
} from 'lucide-react';
import { workspaceApi } from '../api/workspace';
import { useConversation } from '../hooks/useConversation';
import type { Agent, ChatMessage, ChatRun, ConversationUsage } from '../types';
import { Markdown } from './Markdown';
import { RunSteps } from './RunSteps';
import { UserMessage } from './UserMessage';
import { readDroppedEntries, WORKSPACE_FILE_MIME } from './WorkspacePanel';

export type SessionConversationFallback = {
  status: 'loading' | 'ready' | 'error';
  messages: ChatMessage[];
  runs: ChatRun[];
  usage?: ConversationUsage;
  reasoningSteps?: number;
  error?: string;
};

export type SessionStatusTone = 'running' | 'completed' | 'failed' | 'idle';

type SessionConversationModalProps = {
  agent?: Agent;
  conversationId: string;
  title: string;
  subtitle: string;
  statusTone: SessionStatusTone;
  executionTime?: string;
  fallback?: SessionConversationFallback;
  emptyTitle?: string;
  emptyDescription?: string;
  closeLabel?: string;
  onOpenInConversation?: () => void;
  onClose: () => void;
};

function SessionConversationTranscript({ messages, runs }: { messages: ChatMessage[]; runs: ChatRun[] }) {
  const isFinalAnswer = (message: ChatMessage) =>
    message.finishReason === 'stop'
    || (!message.finishReason && !message.toolCalls && message.content.trim().length > 0);
  const visibleMessages = messages.filter((message) =>
    message.role === 'user' || (message.role === 'assistant' && isFinalAnswer(message)));
  const assistantMessages = visibleMessages.filter((message) => message.role === 'assistant');
  const unpositionedRuns = runs.filter((run) => run.insertBeforeMessageId === undefined);
  const fallbackRuns = new Map<string | number, ChatRun[]>();
  const fallbackOffset = Math.max(0, assistantMessages.length - unpositionedRuns.length);
  unpositionedRuns.forEach((run, index) => {
    const message = assistantMessages[Math.min(fallbackOffset + index, assistantMessages.length - 1)];
    if (message) fallbackRuns.set(message.id, [...(fallbackRuns.get(message.id) ?? []), run]);
  });
  const runsForMessage = (message: ChatMessage) => message.role === 'assistant' ? [
    ...runs.filter((run) => run.insertBeforeMessageId === message.id),
    ...(fallbackRuns.get(message.id) ?? []),
  ] : [];

  return (
    <>
      {visibleMessages.map((message) => (
        <Fragment key={message.id}>
          {message.role === 'user' ? (
            <UserMessage content={message.content} />
          ) : (
            <article className="assistant-message">
              {runsForMessage(message).map((run) => (
                <RunSteps key={run.id} run={run} answerContent={message.content} />
              ))}
              <div className="message-content"><Markdown content={message.content} /></div>
            </article>
          )}
        </Fragment>
      ))}
      {assistantMessages.length === 0 && unpositionedRuns.map((run) => <RunSteps key={run.id} run={run} />)}
    </>
  );
}

export function SessionConversationModal({
  agent,
  conversationId,
  title,
  subtitle,
  statusTone,
  executionTime,
  fallback,
  emptyTitle = 'No session yet',
  emptyDescription = 'This session becomes available after the agent starts the work.',
  closeLabel = 'Close session',
  onOpenInConversation,
  onClose,
}: SessionConversationModalProps) {
  const agentId = agent?.id ?? '';
  const displayName = agent?.title ?? title;
  const chat = useConversation(agentId, conversationId, agent?.model ?? '', false);
  const [input, setInput] = useState('');
  const [attachments, setAttachments] = useState<string[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [uploadProgress, setUploadProgress] = useState<{ name: string; percent: number; current: number; total: number }>();
  const [fileDragOver, setFileDragOver] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const canvas = useRef<HTMLDivElement>(null);
  const dragDepth = useRef(0);
  const useLiveSession = chat.status === 'ready' || chat.messages.length > 0 || chat.streaming || Boolean(chat.error);
  const messages = useLiveSession ? chat.messages : fallback?.messages ?? [];
  const runs = useLiveSession ? chat.runs : fallback?.runs ?? [];
  const usage = chat.usage ?? fallback?.usage;
  const reasoningSteps = useLiveSession
    ? chat.runs.reduce((total, run) => total + run.steps.length, 0)
    : fallback?.reasoningSteps;
  const executionLabel = executionTime
    ?? (usage?.executionSeconds !== undefined ? `${usage.executionSeconds.toFixed(1)}s` : '—');

  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [onClose]);

  useEffect(() => {
    const target = canvas.current;
    if (target) target.scrollTop = target.scrollHeight;
  }, [messages.length, messages[messages.length - 1]?.content, chat.streaming]);

  const rememberAttachments = (paths: string[]) => {
    setAttachments((current) => [...new Set([...current, ...paths])]);
  };

  const uploadItems = async (items: { file: File; relativeDir: string }[]) => {
    if (!agent || !items.length || uploading) return;
    setUploading(true);
    setUploadError('');
    const uploaded: string[] = [];
    try {
      const directories = [...new Set(items.map((item) => item.relativeDir).filter(Boolean))]
        .sort((left, right) => left.split('/').length - right.split('/').length);
      for (const directory of directories) {
        try {
          await workspaceApi.create(agent.id, { path: directory, type: 'directory' });
        } catch {
          // Upload remains the source of truth if a directory already exists.
        }
      }
      for (let index = 0; index < items.length; index += 1) {
        const item = items[index];
        setUploadProgress({ name: item.file.name, percent: 0, current: index + 1, total: items.length });
        await workspaceApi.upload(agent.id, item.relativeDir, item.file, (progress) => {
          setUploadProgress({
            name: item.file.name,
            percent: progress.percent ?? (progress.total ? Math.round(progress.loaded / progress.total * 100) : 0),
            current: index + 1,
            total: items.length,
          });
        });
        uploaded.push([item.relativeDir, item.file.name].filter(Boolean).join('/'));
      }
      rememberAttachments(uploaded);
    } catch (cause) {
      setUploadError(cause instanceof Error ? cause.message : 'Unable to upload files.');
    } finally {
      setUploading(false);
      setUploadProgress(undefined);
      if (fileInput.current) fileInput.current.value = '';
    }
  };

  const carriesOsFiles = (event: ReactDragEvent) => {
    const types = Array.from(event.dataTransfer?.types ?? []);
    return !types.includes(WORKSPACE_FILE_MIME)
      && types.some((type) => type === 'Files' || type === 'application/x-moz-file');
  };

  const onDrop = (event: ReactDragEvent) => {
    if (!carriesOsFiles(event) || uploading) return;
    event.preventDefault();
    dragDepth.current = 0;
    setFileDragOver(false);
    const entries: FileSystemEntry[] = [];
    for (const item of Array.from(event.dataTransfer.items ?? [])) {
      const entry = item.webkitGetAsEntry?.();
      if (entry) entries.push(entry);
    }
    if (entries.length) {
      void readDroppedEntries(entries).then(uploadItems);
      return;
    }
    void uploadItems(Array.from(event.dataTransfer.files ?? []).map((file) => ({ file, relativeDir: '' })));
  };

  const submit = () => {
    const text = input.trim();
    if ((!text && attachments.length === 0) || !agent || !conversationId) return;
    const refs = attachments.map((path) => `\`${path}\``).join('\n');
    void chat.sendMessage([refs, text].filter(Boolean).join('\n\n'));
    setInput('');
    setAttachments([]);
  };

  return (
    <div className="modal-overlay team-conversation-overlay" onClick={onClose}>
      <div
        className="app-modal team-conversation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="session-conversation-title"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="team-conversation-head">
          <span className={`run-node-state ${statusTone}`}>
            {statusTone === 'running' ? <Loader2 size={16} />
              : statusTone === 'completed' ? <Check size={15} />
              : statusTone === 'failed' ? <X size={15} />
              : <i />}
          </span>
          <span>
            <strong id="session-conversation-title">{title}</strong>
            <small>{subtitle}</small>
          </span>
          <div className="team-conversation-head-actions">
            {onOpenInConversation && agent && conversationId && (
              <button className="kb-text-action" type="button" onClick={onOpenInConversation}>
                <MessageSquare size={14} /> View session
              </button>
            )}
            <button className="icon-button" onClick={onClose} aria-label={closeLabel}><X size={17} /></button>
          </div>
        </header>

        <section className="team-conversation-metrics" aria-label="Session metrics">
          <span><Coins size={14} /><small>Tokens</small><strong>{usage ? usage.totalTokens.toLocaleString() : '—'}</strong></span>
          <span><Brain size={14} /><small>Reasoning steps</small><strong>{reasoningSteps !== undefined ? reasoningSteps.toLocaleString() : '—'}</strong></span>
          <span><Clock size={14} /><small>Execution time</small><strong>{executionLabel}</strong></span>
          <span><MessageSquare size={14} /><small>Messages</small><strong>{usage?.messages !== undefined ? usage.messages.toLocaleString() : messages.length ? messages.length.toLocaleString() : '—'}</strong></span>
        </section>

        <div
          ref={canvas}
          className={`message-canvas team-conversation-canvas ${fileDragOver ? 'file-drag-over' : ''}`}
          style={{ gridRow: 'auto' }}
          onDragEnter={(event) => {
            if (!carriesOsFiles(event) || uploading) return;
            event.preventDefault();
            dragDepth.current += 1;
            setFileDragOver(true);
          }}
          onDragOver={(event) => {
            if (!carriesOsFiles(event) || uploading) return;
            event.preventDefault();
            event.dataTransfer.dropEffect = 'copy';
          }}
          onDragLeave={(event) => {
            if (!carriesOsFiles(event)) return;
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (dragDepth.current === 0) setFileDragOver(false);
          }}
          onDrop={onDrop}
        >
          {fileDragOver && (
            <div className="chat-dropzone">
              <Upload size={34} />
              <p>Drop files or folders to upload</p>
              <span>Uploads go to {displayName}&apos;s persistent workspace</span>
            </div>
          )}
          {!conversationId ? (
            <div className="team-conversation-empty">
              <MessageSquare size={22} />
              <strong>{emptyTitle}</strong>
              <p>{emptyDescription}</p>
            </div>
          ) : !useLiveSession && (fallback?.status === 'loading' || !fallback) ? (
            <div className="team-conversation-empty"><Loader2 className="run-step-spin" size={22} /><strong>Loading session…</strong></div>
          ) : chat.status === 'error' && messages.length === 0 ? (
            <div className="team-conversation-empty error"><X size={22} /><strong>Session unavailable</strong><p>{chat.error}</p></div>
          ) : !useLiveSession && fallback?.status === 'error' ? (
            <div className="team-conversation-empty error"><X size={22} /><strong>Session unavailable</strong><p>{fallback.error}</p></div>
          ) : messages.length === 0 ? (
            <div className="team-conversation-empty"><MessageSquare size={22} /><strong>No stored messages</strong></div>
          ) : <SessionConversationTranscript messages={messages} runs={runs} />}
          {chat.error && messages.length > 0 && <div className="chat-inline-error" role="alert">{chat.error}</div>}
        </div>

        <footer className="team-conversation-composer">
          {uploadError && <div className="team-conversation-upload-error" role="alert">{uploadError}</div>}
          {uploadProgress && (
            <div className="team-conversation-upload-progress" role="status">
              <span>{uploadProgress.name} · {uploadProgress.current}/{uploadProgress.total}</span>
              <strong>{uploadProgress.percent}%</strong>
              <i><b style={{ width: `${uploadProgress.percent}%` }} /></i>
            </div>
          )}
          <div className="composer team-node-composer">
            {attachments.length > 0 && (
              <div className="composer-attachments">
                {attachments.map((path) => (
                  <span key={path} className="composer-chip" title={path}>
                    <span className="team-node-attachment"><FileText size={13} />{path.split('/').pop()}</span>
                    <button className="composer-chip-remove" title={`Remove ${path}`} onClick={() => setAttachments((current) => current.filter((item) => item !== path))}><X size={12} /></button>
                  </span>
                ))}
              </div>
            )}
            <textarea
              className="composer-input"
              rows={1}
              value={input}
              disabled={!agent || !conversationId}
              placeholder={`Message ${displayName}…`}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  submit();
                }
              }}
            />
            <div className="composer-row">
              <input
                ref={fileInput}
                type="file"
                multiple
                hidden
                aria-label={`Upload files to ${displayName} workspace`}
                onChange={(event) => void uploadItems(Array.from(event.target.files ?? []).map((file) => ({ file, relativeDir: '' })))}
              />
              <button
                type="button"
                className="composer-icon"
                disabled={!agent || uploading}
                title="Upload files"
                aria-label="Upload files"
                onClick={() => fileInput.current?.click()}
              >
                {uploading ? <Loader2 className="run-step-spin" size={17} /> : <Upload size={17} />}
              </button>
              <span className="team-node-session-label">{agent?.model || 'Agent model'} · stored session</span>
              <div className="composer-row-right">
                {chat.streaming ? (
                  <button className="send-round stop-round" disabled={!chat.canStop} aria-label={chat.canStop ? `Stop ${displayName}` : 'Starting'} onClick={() => void chat.stopStream()}>
                    {chat.canStop ? <Square size={13} /> : <Loader2 className="run-step-spin" size={16} />}
                  </button>
                ) : (
                  <button className="send-round" disabled={!agent || !conversationId || (!input.trim() && attachments.length === 0) || uploading} aria-label={`Send message to ${displayName}`} onClick={submit}>
                    <Send size={15} />
                  </button>
                )}
              </div>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}
