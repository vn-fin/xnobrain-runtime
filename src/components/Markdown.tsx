import { Children, memo, useEffect, useId, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import rehypeHighlight from 'rehype-highlight';
import { ExternalLink, FileText, Image as ImageIcon, X } from 'lucide-react';
import { agentsApi } from '../api/agents';
import { kanbanApi } from '../api/kanban';
import type { Agent, KanbanTask } from '../types';
import 'katex/dist/katex.min.css';
import 'highlight.js/styles/github-dark.css';

const IMAGE_REF = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif)$/i;
const FILE_REF = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif|pdf|md|mdx|txt|log|csv|tsv|json|ya?ml|toml|xml|html?|css|scss|py|ipynb|jsx?|tsx?|sh|sql|doc|docx|xls|xlsx|xlsm|ppt|pptx|zip|tar|gz)$/i;
const KANBAN_TASK_ID = /\bt_[0-9a-f]{8}\b/gi;
const KANBAN_TASK_LINK = '#kanban-task:';
const ASSIGNEE_COLORS = ['#4f8cff', '#34d399', '#f8d66d', '#c084fc', '#fb923c', '#7dd3fc'];

type MarkdownNode = {
  type?: string;
  value?: string;
  children?: MarkdownNode[];
  url?: string;
};

/** Turns plain Hermes task IDs into links while leaving code and existing links intact. */
function remarkKanbanTaskLinks() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode) => {
      if (!Array.isArray(node.children) || node.type === 'link') return;
      node.children = node.children.flatMap((child) => {
        if (child.type !== 'text' || !child.value) {
          visit(child);
          return [child];
        }
        const parts: MarkdownNode[] = [];
        let cursor = 0;
        for (const match of child.value.matchAll(KANBAN_TASK_ID)) {
          const index = match.index ?? 0;
          if (index > cursor) parts.push({ type: 'text', value: child.value.slice(cursor, index) });
          const taskId = match[0];
          parts.push({
            type: 'link',
            url: `${KANBAN_TASK_LINK}${taskId}`,
            children: [{ type: 'text', value: taskId }],
          });
          cursor = index + taskId.length;
        }
        if (cursor === 0) return [child];
        if (cursor < child.value.length) parts.push({ type: 'text', value: child.value.slice(cursor) });
        return parts;
      });
    };
    visit(tree);
  };
}

/** A code span is a previewable file reference when it's a whitespace-free path ending in a known extension. */
function isFileRef(text: string): boolean {
  const value = text.trim();
  return value.length > 0 && !/\s/.test(value) && FILE_REF.test(value);
}

const baseName = (path: string) => path.split('/').filter(Boolean).pop() ?? path;

function compactUrlLabel(href: string | undefined, children: ReactNode): ReactNode {
  if (!href) return children;
  const childParts = Children.toArray(children);
  if (!childParts.every((part) => typeof part === 'string' || typeof part === 'number')) return children;
  const visibleText = childParts.join('');
  if (visibleText !== href) return children;
  try {
    const url = new URL(href);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return children;
    const lastPath = url.pathname.split('/').filter(Boolean).at(-1);
    return lastPath ? decodeURIComponent(lastPath) : url.hostname;
  } catch {
    return children;
  }
}

/** Reads the `language-xxx` class off a fenced code block's <code> child. */
function languageOf(children: ReactNode): string | undefined {
  const child = Array.isArray(children) ? children[0] : children;
  if (child && typeof child === 'object' && 'props' in child) {
    const className = String((child as { props?: { className?: string } }).props?.className ?? '');
    return /language-(\w[\w+-]*)/.exec(className)?.[1];
  }
  return undefined;
}

function buildComponents(onOpenFile?: (path: string) => void, onOpenTask?: (taskId: string) => void): Components {
  return {
    code({ className, children, ...props }) {
      const text = String(children ?? '');
      const isBlock = /language-/.test(className ?? '') || text.includes('\n');
      if (!isBlock) {
        const inner = text;
        if (onOpenFile && isFileRef(inner)) {
          return (
            <button
              type="button"
              className="message-file-ref"
              title={`Quick view · ${inner}`}
              onClick={() => onOpenFile(inner)}
            >
              {IMAGE_REF.test(inner) ? <ImageIcon size={13} /> : <FileText size={13} />}
              <span>{baseName(inner)}</span>
            </button>
          );
        }
        return <code className="message-inline-code">{children}</code>;
      }
      // Block code: keep rehype-highlight's classes/spans intact.
      return <code className={className} {...props}>{children}</code>;
    },
    pre({ children }) {
      return (
        <pre className="message-code-block" data-language={languageOf(children)}>
          {children}
        </pre>
      );
    },
    a({ href, children }) {
      if (href?.startsWith(KANBAN_TASK_LINK)) {
        const taskId = href.slice(KANBAN_TASK_LINK.length);
        return (
          <a
            href={`/kanban/tasks/${encodeURIComponent(taskId)}`}
            onClick={(event) => {
              event.preventDefault();
              onOpenTask?.(taskId);
            }}
          >
            {children}
          </a>
        );
      }
      const compactChildren = compactUrlLabel(href, children);
      const compacted = compactChildren !== children;
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          title={compacted ? href : undefined}
          aria-label={compacted ? href : undefined}
        >
          {compactChildren}
        </a>
      );
    },
    table({ children }) {
      return (
        <div className="message-table-wrap">
          <table>{children}</table>
        </div>
      );
    },
  };
}

function shortText(value: string | null | undefined, max = 320): string {
  const text = (value ?? '').trim().replace(/\s+/g, ' ');
  if (text.length <= max) return text;
  return `${text.slice(0, max).trimEnd()}…`;
}

function statusLabel(value: string): string {
  return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function taskStatusLabel(value: KanbanTask['nativeStatus']): string {
  return value === 'running' ? 'In Progress' : statusLabel(value);
}

function monogram(value: string): string {
  return value.split(/[\s-]+/).map((word) => word[0]).slice(0, 2).join('').toUpperCase();
}

function assigneeColor(id: string): string {
  let hash = 0;
  for (let index = 0; index < id.length; index += 1) hash = (hash * 31 + id.charCodeAt(index)) >>> 0;
  return ASSIGNEE_COLORS[hash % ASSIGNEE_COLORS.length];
}

function taskTime(value: string | null | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}

function ExpandableTaskText({ label, value, empty }: { label: string; value: string; empty: string }) {
  const [expanded, setExpanded] = useState(false);
  const text = value.trim() || empty;
  const preview = shortText(text, 180);
  const expandable = preview !== text;
  return (
    <section className="kb-detail-section">
      <div className="kb-section-heading">
        <span className="kb-label">{label}</span>
        {expandable && (
          <button className="kb-text-action" type="button" onClick={() => setExpanded((current) => !current)}>
            {expanded ? 'Show less' : `Show full ${label.toLowerCase()}`}
          </button>
        )}
      </div>
      <p className="kb-desc">{expanded ? text : preview}</p>
    </section>
  );
}

function KanbanTaskPreview({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const titleId = useId();
  const [task, setTask] = useState<KanbanTask>();
  const [agents, setAgents] = useState<Agent[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    setTask(undefined);
    setError('');
    void Promise.all([
      kanbanApi.getBoards(),
      agentsApi.list().catch(() => [] as Agent[]),
    ])
      .then(async ([boards, roster]) => {
        const board = boards.find((item) => item.tasks.some((candidate) => candidate.id === taskId));
        if (!board) throw new Error(`Task ${taskId} was not found.`);
        const detail = await kanbanApi.getTask(board.id, taskId);
        if (active) {
          setTask(detail);
          setAgents(roster);
        }
      })
      .catch((value: unknown) => {
        if (active) setError(value instanceof Error ? value.message : `Could not load task ${taskId}.`);
      });
    return () => { active = false; };
  }, [taskId]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose(); };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose]);

  const latestRun = task?.runs?.[task.runs.length - 1];
  const result = task ? task.result || task.summary || task.block || '' : '';
  const assigneeId = task?.assignees?.[0] ?? '';
  const assignee = agents.find((agent) => agent.id === assigneeId || agent.name === assigneeId);
  const owner = task ? task.team?.name || assignee?.title || assigneeId || 'Unassigned' : '';
  const activityTimes = task ? [
    task.updated ? `Updated ${task.updated}` : '',
    latestRun?.startedAt ? `Started ${taskTime(latestRun.startedAt)}` : '',
    latestRun?.endedAt ? `Finished ${taskTime(latestRun.endedAt)}` : '',
    task.schedule?.nextRunAt ? `Next run ${taskTime(task.schedule.nextRunAt)}` : '',
  ].filter(Boolean) : [];
  return (
    <div className="modal-overlay alert-overlay" onClick={onClose}>
      <div
        className="app-modal confirm-modal kanban-task-preview"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="app-modal-head">
          <div>
            <div className="kanban-task-preview-title">
              <strong id={titleId}>{task?.title || `Kanban task ${taskId}`}</strong>
              {task && (
                <span className={`kb-substate native-${task.nativeStatus}`}>
                  {taskStatusLabel(task.nativeStatus)}
                </span>
              )}
            </div>
            <p className="app-modal-sub">{taskId}</p>
          </div>
          <button className="icon-button" type="button" aria-label="Close task summary" onClick={onClose}>
            <X size={17} />
          </button>
        </div>
        {!task && !error && <p className="confirm-message">Loading task summary…</p>}
        {error && <p className="confirm-message">{error}</p>}
        {task && (
          <div className="kanban-task-preview-body">
            <section className="kb-detail-section kanban-task-assignment">
              <div className="kanban-task-owner-row">
                <div>
                  <span className="kb-label">Assignee</span>
                  <span className="kb-person">
                    <span
                      className={`kb-avatar${assigneeId || task.team ? '' : ' unassigned'}`}
                      style={assigneeId || task.team ? { background: assigneeColor(assigneeId || task.team?.id || owner) } : undefined}
                    >
                      {assigneeId || task.team ? monogram(owner) : '—'}
                    </span>
                    <span>{owner}</span>
                  </span>
                </div>
                <div>
                  <span className="kb-label">Priority</span>
                  <span className="kb-value">
                    <span className={`kb-prio-bar ${task.priority}`} /> {statusLabel(task.priority)}
                  </span>
                </div>
              </div>
              <div className="kanban-task-progress">
                <div><span className="kb-label">Progress</span><strong>{task.progress}%</strong></div>
                <div
                  className="kb-track"
                  role="progressbar"
                  aria-label="Task progress"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={task.progress}
                >
                  <span style={{ width: `${Math.min(100, Math.max(0, task.progress))}%` }} />
                </div>
              </div>
            </section>
            {activityTimes.length > 0 && (
              <section className="kb-detail-section">
                <span className="kb-label">Activity</span>
                <div className="kanban-task-activity">
                  {activityTimes.map((time) => <span key={time}>{time}</span>)}
                </div>
              </section>
            )}
            <ExpandableTaskText label="Task brief" value={task.description} empty="No task brief provided." />
            <ExpandableTaskText label="Results" value={result} empty="No results yet." />
          </div>
        )}
        <div className="modal-actions">
          <button className="conn-btn ghost" type="button" onClick={onClose}>Close</button>
          <a className="conn-btn primary" href={`/kanban/tasks/${encodeURIComponent(taskId)}`}>
            <ExternalLink size={14} /> Open in Kanban
          </a>
        </div>
      </div>
    </div>
  );
}

function MarkdownBase({ content, onOpenFile }: { content: string; onOpenFile?: (path: string) => void }) {
  const [taskId, setTaskId] = useState('');
  return (
    <>
      <div className="markdown-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm, remarkMath, remarkKanbanTaskLinks]}
          rehypePlugins={[rehypeKatex, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
          components={buildComponents(onOpenFile, setTaskId)}
        >
          {content}
        </ReactMarkdown>
      </div>
      {taskId && createPortal(
        <KanbanTaskPreview taskId={taskId} onClose={() => setTaskId('')} />,
        document.body,
      )}
    </>
  );
}

export const Markdown = memo(MarkdownBase);
