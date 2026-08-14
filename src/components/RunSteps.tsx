import { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Brain,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleStop,
  Code,
  FileText,
  FolderTree,
  LoaderCircle,
  ListChecks,
  Minus,
  Circle,
  Plug,
  Search,
  ShieldAlert,
  Sparkles,
  Terminal,
  Wrench,
  X,
  XCircle,
} from 'lucide-react';
import { formatRunDuration, formatStepDuration, previewCode, stepLabel, toolGroupSummary, toolKind } from '../chat/runEvents';
import type { ChatRun, ChatRunStep, ChatTodoItem, DelegationWorker, RunApprovalChoice } from '../types';
import { ConfirmDialog } from './modals';
import { Markdown } from './Markdown';

const APPROVAL_ORDER: RunApprovalChoice[] = ['once', 'always', 'deny'];
const APPROVAL_LABELS: Record<RunApprovalChoice, string> = {
  once: 'Allow once',
  always: 'Always allow',
  deny: 'Deny',
};

function useRunClock(run: ChatRun): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (run.status !== 'running') return;
    const update = () => setNow(Date.now() / 1000);
    update();
    const timer = window.setInterval(update, 1_000);
    return () => window.clearInterval(timer);
  }, [run.id, run.status]);
  return now;
}

export function RunActivityBar({ run, onViewActivity }: { run: ChatRun; onViewActivity: () => void }) {
  const now = useRunClock(run);
  const waiting = run.status === 'waiting_for_approval';
  const duration = formatRunDuration(run, run.status === 'running' ? now : undefined);
  const stepCount = run.steps.length;
  const stepLabel = `${stepCount} ${stepCount === 1 ? 'step' : 'steps'}`;
  const todos = run.todos ?? [];
  const todoDone = todos.filter((todo) => todo.status === 'completed' || todo.status === 'cancelled').length;
  const todoLabel = todos.length > 0 ? `${todoDone}/${todos.length} tasks` : '';
  const activityLabel = waiting
    ? `Approval needed${todoLabel ? `, ${todoLabel}` : ''}, ${stepLabel}`
    : `Agent is working${duration ? ` for ${duration}` : ''}${todoLabel ? `, ${todoLabel}` : ''}, ${stepLabel}`;
  const deadlineRemaining = run.deadlineAt ? Math.max(0, run.deadlineAt - now) : undefined;
  const deadlineLabel = deadlineRemaining !== undefined
    ? formatRunDuration({ ...run, startedAt: 0, endedAt: deadlineRemaining })
    : '';
  const showDeadlineWarning = run.status === 'running'
    && deadlineRemaining !== undefined
    && deadlineRemaining < 5 * 60;

  return (
    <div className={`live-run-activity ${waiting ? 'waiting' : 'running'}`} aria-label={activityLabel}>
      <span className="live-run-activity-state">
        {waiting
          ? <ShieldAlert className="run-step-waiting" size={15} />
          : <LoaderCircle className="run-step-spin" size={15} />}
        <strong>{waiting ? 'Approval needed' : `Working${duration ? ` for ${duration}` : ''}`}</strong>
      </span>
      {todoLabel && <><span className="live-run-activity-separator" aria-hidden="true">·</span><span className="live-run-activity-count">{todoLabel}</span></>}
      <span className="live-run-activity-separator" aria-hidden="true">·</span>
      <span className="live-run-activity-count">{stepLabel}</span>
      {showDeadlineWarning && deadlineLabel && <><span className="live-run-activity-separator" aria-hidden="true">·</span><span className="live-run-deadline">{deadlineLabel} limit remaining</span></>}
      <button
        type="button"
        aria-label={waiting ? 'Review agent activity' : 'View agent activity'}
        title={waiting ? 'Review activity' : 'View activity'}
        onClick={onViewActivity}
      >
        <span>{waiting ? 'Review activity' : 'View activity'}</span>
        <ArrowUp size={13} />
      </button>
      <span className="sr-only" role="status">{waiting ? 'Agent approval is required.' : 'Agent is working.'}</span>
    </div>
  );
}

function TodoStatusIcon({ todo }: { todo: ChatTodoItem }) {
  if (todo.status === 'completed') return <Check size={14} />;
  if (todo.status === 'in_progress') return <LoaderCircle className="run-step-spin" size={14} />;
  if (todo.status === 'cancelled') return <Minus size={14} />;
  return <Circle size={12} />;
}

function RunPlan({ todos, runStatus }: { todos: ChatTodoItem[]; runStatus: ChatRun['status'] }) {
  const complete = todos.filter((todo) => todo.status === 'completed' || todo.status === 'cancelled').length;
  const allDone = todos.length > 0 && complete === todos.length;
  const [open, setOpen] = useState(() => !allDone);
  const [showAll, setShowAll] = useState(false);
  const touched = useRef(false);
  const activeId = todos.find((todo) => todo.status === 'in_progress')?.id;
  const visibleTodos = showAll ? todos : todos.slice(0, 5);

  useEffect(() => {
    if (!touched.current && runStatus === 'running' && activeId) setOpen(true);
    if (!touched.current && runStatus !== 'running' && allDone) setOpen(false);
  }, [activeId, allDone, runStatus]);

  if (todos.length === 0) return null;
  return (
    <section className={`run-plan ${allDone ? 'complete' : 'active'}`} aria-label="Agent plan">
      <button
        type="button"
        className="run-plan-toggle"
        aria-expanded={open}
        title="Session plan — not Kanban tasks"
        onClick={() => { touched.current = true; setOpen((value) => !value); }}
      >
        <ListChecks size={15} />
        <span>Plan</span>
        <span className="run-plan-count">{complete} / {todos.length}</span>
        <ChevronDown className={open ? 'open' : ''} size={14} />
      </button>
      {open && (
        <ol className="run-plan-items">
          {visibleTodos.map((todo) => (
            <li key={todo.id} className={`run-plan-item ${todo.status}`}>
              <span className="run-plan-status" aria-hidden="true"><TodoStatusIcon todo={todo} /></span>
              <span>{todo.content}</span>
              {todo.status === 'in_progress' && <small>Current</small>}
            </li>
          ))}
          {todos.length > 5 && (
            <li className="run-plan-more-row">
              <button type="button" onClick={() => setShowAll((value) => !value)}>
                {showAll ? 'Show less' : `Show ${todos.length - 5} more`}
              </button>
            </li>
          )}
        </ol>
      )}
      <span className="sr-only" role="status">Agent plan: {complete} of {todos.length} tasks finished.</span>
    </section>
  );
}

function formatted(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'string') {
    try { return JSON.stringify(JSON.parse(value), null, 2); } catch { return value; }
  }
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function object(value: unknown): Record<string, unknown> {
  if (value && typeof value === 'object') return value as Record<string, unknown>;
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === 'object' ? parsed as Record<string, unknown> : {};
    } catch { return {}; }
  }
  return {};
}

function codePreview(step: ChatRunStep): string {
  const args = object(step.args);
  const output = object(step.output);
  const candidates = step.toolName === 'write_file'
    ? [args.content]
    : step.toolName === 'execute_code'
      ? [args.code, args.content, args.input, output.output]
      : [];
  const fromArgs = candidates.find((value): value is string => typeof value === 'string' && value.trim().length > 0);
  // When the event carried no args/output (live API), fall back to the raw
  // command/script preview for terminal and code tools.
  return fromArgs ?? previewCode(step);
}

/** Picks a glyph for the tool based on its visual category. */
function ToolGlyph({ step }: { step: ChatRunStep }) {
  const kind = toolKind(step);
  const size = 14;
  if (kind === 'terminal') return <Terminal size={size} />;
  if (kind === 'file-write' || kind === 'read') return <FileText size={size} />;
  if (kind === 'code') return <Code size={size} />;
  if (kind === 'search') return <Search size={size} />;
  if (kind === 'list') return <FolderTree size={size} />;
  if (kind === 'skill') return <Sparkles size={size} />;
  if (kind === 'mcp') return <Plug size={size} />;
  return <Wrench size={size} />;
}

function StatusIcon({ status }: { status: ChatRunStep['status'] }) {
  if (status === 'running') return <LoaderCircle className="run-step-spin" size={15} />;
  if (status === 'error') return <XCircle className="run-step-error" size={15} />;
  if (status === 'interrupted' || status === 'cancelled') return <CircleStop className="run-step-muted" size={15} />;
  return <CheckCircle2 className="run-step-ok" size={15} />;
}

function ReasoningBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  // Reasoning is visible by default for both live and restored runs. Keep a
  // user's explicit toggle stable as streaming state changes.
  const [open, setOpen] = useState(true);
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current && streaming) setOpen(true);
  }, [streaming]);
  return (
    <div className={`run-reasoning ${streaming ? 'streaming' : ''}`}>
      <button
        className="run-reasoning-toggle"
        onClick={() => { touched.current = true; setOpen((value) => !value); }}
        aria-expanded={open}
      >
        <Brain size={15} className={streaming ? 'run-step-spin-soft' : ''} />
        <span>{streaming ? 'Thinking…' : 'Reasoning'}</span>
        <ChevronDown className={open ? 'open' : ''} size={15} />
      </button>
      {open && (
        <div
          className="run-reasoning-body"
          aria-live={streaming ? 'polite' : undefined}
          aria-atomic={streaming ? 'false' : undefined}
        >
          {streaming ? text : <Markdown content={text} />}
        </div>
      )}
    </div>
  );
}

function usageSummary(run: ChatRun): string {
  if (!run.usage) return '';
  const parts: string[] = [];
  if (run.usage.inputTokens !== undefined) parts.push(`${run.usage.inputTokens.toLocaleString()} in`);
  if (run.usage.outputTokens !== undefined) parts.push(`${run.usage.outputTokens.toLocaleString()} out`);
  const detail = parts.length ? ` (${parts.join(' · ')})` : '';
  return `${run.usage.totalTokens.toLocaleString()} tokens${detail}`;
}

function RunApprovalPrompt({
  run,
  onResolveApproval,
}: {
  run: ChatRun;
  onResolveApproval?: (runId: string, choice: RunApprovalChoice) => void | Promise<void>;
}) {
  const [submitting, setSubmitting] = useState<RunApprovalChoice | null>(null);
  const [alwaysConfirmOpen, setAlwaysConfirmOpen] = useState(false);
  const approval = run.approval;
  if (!approval) return null;
  const choices = APPROVAL_ORDER.filter((choice) =>
    approval.choices.includes(choice) && (choice !== 'always' || approval.allowPermanent));

  const submit = async (choice: RunApprovalChoice) => {
    if (!onResolveApproval || submitting) return;
    setSubmitting(choice);
    try {
      await onResolveApproval(run.id, choice);
    } finally {
      setSubmitting(null);
    }
  };

  const respond = (choice: RunApprovalChoice) => {
    if (choice === 'always') {
      setAlwaysConfirmOpen(true);
      return;
    }
    void submit(choice);
  };

  const target = approval.subsystem ? `${approval.subsystem} writes` : 'matching requests';

  return (
    <>
      <div className="run-approval" role="group" aria-label="Run approval request">
        <div className="run-approval-main">
          <span className="run-approval-icon"><ShieldAlert size={16} /></span>
          <div className="run-approval-copy">
            <strong>{approval.description || 'Approval required'}</strong>
            {approval.command && <code className="run-approval-command">{approval.command}</code>}
          </div>
        </div>
        <div className="run-approval-actions">
          {choices.map((choice) => (
            <button
              type="button"
              key={choice}
              className={choice === 'deny' ? 'run-approval-button danger' : 'run-approval-button'}
              disabled={!onResolveApproval || submitting !== null}
              onClick={() => respond(choice)}
            >
              {submitting === choice && <LoaderCircle className="run-step-spin" size={13} />}
              {APPROVAL_LABELS[choice]}
            </button>
          ))}
        </div>
      </div>
      {alwaysConfirmOpen && (
        <ConfirmDialog
          title="Always allow this action?"
          message={`Always allow ${target} for this agent profile? Future matching requests will not ask again.`}
          confirmLabel="Always allow"
          onConfirm={() => {
            setAlwaysConfirmOpen(false);
            void submit('always');
          }}
          onCancel={() => setAlwaysConfirmOpen(false)}
        />
      )}
    </>
  );
}

function StepRow({ step }: { step: ChatRunStep }) {
  const args = formatted(step.args);
  const output = formatted(step.output);
  const preview = codePreview(step);
  const stepDuration = formatStepDuration(step);
  return (
    <div className={`run-step ${step.status}`}>
      <span className="run-step-icon"><StatusIcon status={step.status} /></span>
      <div className="run-step-body">
        <div className="run-step-head">
          <span className="run-step-tool"><ToolGlyph step={step} /></span>
          <span className="run-step-label">{stepLabel(step)}</span>
          {stepDuration && <span className="run-step-time">{stepDuration}</span>}
        </div>
        {step.progress && <div className="run-step-progress">{step.progress}</div>}
        {preview && <pre className="run-step-code-preview"><code>{preview}</code></pre>}
        {(args || output) && (
          <details className="run-step-details">
            <summary>Details</summary>
            {args && <><small>Arguments</small><pre>{args}</pre></>}
            {output && <><small>Output</small><pre>{output}</pre></>}
          </details>
        )}
      </div>
    </div>
  );
}

type DelegationResult = {
  task_index?: number;
  status?: string;
  summary?: string;
  duration_seconds?: number;
  error?: string;
  api_calls?: number;
  tokens?: { input?: number; output?: number };
  files_read?: string[];
  files_written?: string[];
};

function delegationDuration(seconds: unknown): string {
  if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0) return '';
  if (seconds < 1) return `${Math.round(seconds * 1_000)}ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${Math.round(seconds % 60)}s`;
}

function compactTokens(value: number): string {
  if (value < 1_000) return String(value);
  if (value < 1_000_000) return `${(value / 1_000).toFixed(value < 10_000 ? 1 : 0)}k`;
  return `${(value / 1_000_000).toFixed(1)}m`;
}

function workerDuration(worker: DelegationWorker, now: number): string {
  const start = worker.startedAt;
  const end = worker.endedAt ?? now;
  return start !== undefined ? delegationDuration(Math.max(0, end - start)) : '';
}

function workerStatusGlyph(worker: DelegationWorker): string {
  if (worker.status === 'completed') return '[✓]';
  if (worker.status === 'error') return '[!]';
  if (worker.status === 'cancelled' || worker.status === 'interrupted') return '[×]';
  if (worker.status === 'running') return '[>]';
  return '[ ]';
}

function WorkerLogModal({ worker, now, onClose }: { worker: DelegationWorker; now: number; onClose: () => void }) {
  const [tab, setTab] = useState<'log' | 'result' | 'files'>('log');
  const [autoScroll, setAutoScroll] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const running = worker.status === 'running';
  const tokens = (worker.inputTokens ?? 0) + (worker.outputTokens ?? 0) + (worker.reasoningTokens ?? 0);
  useEffect(() => {
    if (autoScroll && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [autoScroll, worker.logs]);
  const files = [
    ...(worker.filesRead ?? []).map((path) => ({ kind: 'read', path })),
    ...(worker.filesWritten ?? []).map((path) => ({ kind: 'written', path })),
  ];
  return (
    <div className="delegation-log-overlay" role="presentation" onMouseDown={onClose}>
      <section className="delegation-log-modal" role="dialog" aria-modal="true" aria-label={`Worker ${worker.index + 1} activity`} onMouseDown={(event) => event.stopPropagation()}>
        <header className="delegation-log-head">
          <span className="delegation-prompt" aria-hidden="true">$</span>
          <strong>WORKER {String(worker.index + 1).padStart(2, '0')}</strong>
          <span className={`delegation-log-state ${worker.status}`}>{worker.status.toUpperCase()}</span>
          <button type="button" onClick={onClose} aria-label="Close worker activity"><X size={16} /></button>
        </header>
        <div className="delegation-log-goal" title={worker.goal}>{worker.goal}</div>
        <div className="delegation-log-meta">
          <span>{workerDuration(worker, now) || '0s'}</span>
          <span>{worker.toolCount} {worker.toolCount === 1 ? 'tool' : 'tools'}</span>
          <span>{worker.steps === undefined ? 'steps —' : `${worker.steps} ${worker.steps === 1 ? 'step' : 'steps'}`}</span>
          <span>{tokens > 0 ? `${compactTokens(tokens)} tokens` : 'tokens pending'}</span>
        </div>
        <nav className="delegation-log-tabs" aria-label="Worker activity views">
          <button type="button" className={tab === 'log' ? 'active' : ''} onClick={() => setTab('log')}>Live log</button>
          <button type="button" className={tab === 'result' ? 'active' : ''} onClick={() => setTab('result')}>Result</button>
          <button type="button" className={tab === 'files' ? 'active' : ''} onClick={() => setTab('files')}>Files</button>
        </nav>
        {tab === 'log' && (
          <div className="delegation-log-lines" ref={logRef} aria-live="polite">
            {worker.logs.length > 0 ? worker.logs.map((entry) => (
              <div className={`delegation-log-line ${entry.kind}`} key={entry.id}>
                <time>{entry.timestamp ? new Date(entry.timestamp * 1_000).toLocaleTimeString([], { hour12: false }) : '--:--:--'}</time>
                <span>{entry.kind}</span>
                <code>{entry.tool ? `${entry.tool}${entry.message ? ` · ${entry.message}` : ''}` : entry.message}</code>
              </div>
            )) : <div className="delegation-log-empty">Waiting for the worker to start…</div>}
          </div>
        )}
        {tab === 'result' && (
          <div className="delegation-log-result">
            {worker.error ? <pre>{worker.error}</pre> : worker.summary ? <Markdown content={worker.summary} /> : <span>No result yet.</span>}
          </div>
        )}
        {tab === 'files' && (
          <div className="delegation-log-files">
            {files.length > 0 ? files.map((file, index) => <div key={`${file.kind}-${file.path}-${index}`}><span>{file.kind}</span><code>{file.path}</code></div>) : <span>No file activity reported.</span>}
          </div>
        )}
        <footer className="delegation-log-footer">
          <span className={running ? 'streaming' : ''}>{running ? '● streaming updates' : '● stream complete'}</span>
          <button type="button" onClick={() => setAutoScroll((value) => !value)}>Auto-scroll {autoScroll ? 'on' : 'off'}</button>
        </footer>
      </section>
    </div>
  );
}

function DelegationCard({ step, onStop }: { step: ChatRunStep; onStop?: () => void }) {
  const [selectedWorker, setSelectedWorker] = useState<number>();
  const [collapseCompleted, setCollapseCompleted] = useState(false);
  const [confirmCancelAll, setConfirmCancelAll] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1_000);
  useEffect(() => {
    if (step.status !== 'running') return;
    const timer = window.setInterval(() => setNow(Date.now() / 1_000), 1_000);
    return () => window.clearInterval(timer);
  }, [step.status]);
  const args = object(step.args);
  const output = object(step.output);
  const rawTasks = Array.isArray(args.tasks) ? args.tasks : [];
  const goals = rawTasks
    .map((task) => object(task).goal)
    .filter((goal): goal is string => typeof goal === 'string' && goal.trim().length > 0);
  if (goals.length === 0 && typeof args.goal === 'string' && args.goal.trim()) goals.push(args.goal);
  const results = (Array.isArray(output.results) ? output.results : [])
    .map((result) => object(result) as DelegationResult);
  const workers: DelegationWorker[] = step.delegation?.workers ?? Array.from(
    { length: Math.max(goals.length, results.length, 1) },
    (_, index) => {
      const result = results.find((item) => item.task_index === index) ?? results[index];
      const status = result?.status === 'completed' && !result.error ? 'completed' : result ? 'error' : step.status === 'running' ? 'running' : 'error';
      return {
        index,
        goal: goals[index] || `Worker ${String(index + 1).padStart(2, '0')}`,
        status,
        toolCount: Array.isArray(object(result).tool_trace) ? (object(result).tool_trace as unknown[]).length : 0,
        steps: result?.api_calls,
        inputTokens: result?.tokens?.input,
        outputTokens: result?.tokens?.output,
        summary: result?.summary,
        error: result?.error,
        filesRead: result?.files_read,
        filesWritten: result?.files_written,
        startedAt: step.startedAt,
        endedAt: step.endedAt,
        logs: result ? [{
          id: `${step.id}-${index}-restored`,
          timestamp: step.endedAt,
          kind: status === 'completed' ? 'complete' : 'error',
          message: status === 'completed' ? 'Worker completed' : result.error || 'Worker failed',
        }] : [],
      };
    },
  );
  const workerCount = workers.length;
  const failedCount = workers.filter((worker) => worker.status === 'error').length;
  const completedCount = workers.filter((worker) => worker.status === 'completed').length;
  const runningCount = workers.filter((worker) => worker.status === 'running').length;
  const queuedCount = workers.filter((worker) => worker.status === 'queued').length;
  const totalTools = workers.reduce((sum, worker) => sum + worker.toolCount, 0);
  const totalSteps = workers.reduce((sum, worker) => sum + (worker.steps ?? 0), 0);
  const totalTokens = workers.reduce(
    (sum, worker) => sum + (worker.inputTokens ?? 0) + (worker.outputTokens ?? 0) + (worker.reasoningTokens ?? 0),
    0,
  );
  const isRunning = step.status === 'running';
  const state = isRunning
    ? 'RUNNING'
    : step.status === 'cancelled' || step.status === 'interrupted'
      ? 'STOPPED'
      : step.status === 'error' || (workers.length > 0 && failedCount === workers.length)
        ? 'FAILED'
        : failedCount > 0
          ? 'PARTIAL FAILURE'
          : 'COMPLETED';
  const transcripts = Array.isArray(output.live_transcripts) ? output.live_transcripts : [];
  const delegationId = transcripts
    .map((item) => typeof item === 'string' ? item.match(/deleg_[^/\\]+/)?.[0] : undefined)
    .find(Boolean);
  const elapsed = isRunning
    ? step.startedAt ? delegationDuration(now - step.startedAt) : formatStepDuration(step)
    : delegationDuration(output.total_duration_seconds) || formatStepDuration(step);
  const visibleWorkers = collapseCompleted ? workers.filter((worker) => worker.status !== 'completed') : workers;
  const selected = selectedWorker === undefined ? undefined : workers.find((worker) => worker.index === selectedWorker);
  const concurrency = step.delegation?.concurrency ?? (typeof output.concurrency === 'number' ? output.concurrency : Math.min(workerCount, 3));
  const percent = workerCount > 0 ? Math.round((completedCount + failedCount) / workerCount * 100) : 0;

  return (
    <section className={`delegation-card ${state.toLowerCase().replace(/\s+/g, '-')}`} aria-label="Delegation activity">
      <header className="delegation-head">
        <span className="delegation-prompt" aria-hidden="true">$</span>
        <strong>DELEGATION{delegationId ? ` ${delegationId}` : ''}</strong>
        <span className="delegation-state">{state}</span>
      </header>
      <div className="delegation-meta">
        <span>{completedCount + failedCount} / {workerCount} complete</span>
        {elapsed && <span>{elapsed}</span>}
      </div>
      <div className="delegation-capacity">
        {runningCount} running · {queuedCount} queued · {concurrency} {concurrency === 1 ? 'slot' : 'slots'}
        {` · ${totalTools} ${totalTools === 1 ? 'tool' : 'tools'} · ${totalSteps} ${totalSteps === 1 ? 'step' : 'steps'}`}
        {totalTokens > 0 ? ` · ${compactTokens(totalTokens)} tok` : ''}
      </div>
      <div className="delegation-progress" role="progressbar" aria-label="Delegation progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
        <span style={{ width: `${percent}%` }} />
      </div>
      <ol className="delegation-workers">
        {visibleWorkers.map((worker) => {
          const tokens = (worker.inputTokens ?? 0) + (worker.outputTokens ?? 0) + (worker.reasoningTokens ?? 0);
          const metrics = worker.status === 'queued'
            ? `queue #${worker.queuePosition ?? 1}`
            : [
                worker.lastTool,
                `${worker.toolCount} ${worker.toolCount === 1 ? 'tool' : 'tools'}`,
                worker.steps === undefined ? 'steps —' : `${worker.steps} ${worker.steps === 1 ? 'step' : 'steps'}`,
                tokens > 0 ? `${compactTokens(tokens)} tok` : 'tokens —',
                workerDuration(worker, now),
              ].filter(Boolean).join(' · ');
          return (
            <li key={worker.index} className={worker.status}>
              <button type="button" title={worker.goal} onClick={() => setSelectedWorker(worker.index)}>
                <span className="delegation-worker-status" aria-hidden="true">{workerStatusGlyph(worker)}</span>
                <span className="delegation-worker-main">
                  <span className="delegation-worker-goal"><strong>{String(worker.index + 1).padStart(2, '0')}</strong> {worker.goal}</span>
                  <span className="delegation-worker-metrics">{metrics}</span>
                </span>
                <ChevronRight size={14} />
              </button>
            </li>
          );
        })}
      </ol>
      <div className="delegation-command">
        <span aria-hidden="true">$</span>
        <span>{isRunning
          ? `${completedCount} completed · ${runningCount} running · ${queuedCount} queued`
          : state === 'COMPLETED'
            ? 'results returned to agent for synthesis'
            : state === 'PARTIAL FAILURE'
              ? `${completedCount}/${workerCount} results returned; agent received available results`
              : state === 'STOPPED'
                ? 'delegation stopped'
                : 'delegation failed'}</span>
      </div>
      <div className="delegation-actions">
        {completedCount > 0 && <button type="button" onClick={() => setCollapseCompleted((value) => !value)}>{collapseCompleted ? 'Show completed' : 'Collapse completed'}</button>}
        {isRunning && onStop && <button type="button" className="danger" onClick={() => setConfirmCancelAll(true)}>Cancel all</button>}
      </div>
      {selected && <WorkerLogModal worker={selected} now={now} onClose={() => setSelectedWorker(undefined)} />}
      {confirmCancelAll && onStop && (
        <ConfirmDialog
          title="Cancel all delegated tasks?"
          message={`This stops the current response, cancels ${runningCount} running ${runningCount === 1 ? 'worker' : 'workers'}, and removes ${queuedCount} queued ${queuedCount === 1 ? 'worker' : 'workers'}. Completed results remain available.`}
          confirmLabel="Cancel all"
          danger
          onConfirm={() => {
            setConfirmCancelAll(false);
            onStop();
          }}
          onCancel={() => setConfirmCancelAll(false)}
        />
      )}
    </section>
  );
}

/** A grouped batch of consecutive tool steps, shown as one summary line
 *  ("Read files, ran commands, …") that expands to the individual steps. */
function ToolGroup({ steps, onStop }: { steps: ChatRunStep[]; onStop?: () => void }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
  if (steps.some((step) => step.toolName === 'delegate_task')) {
    return (
      <div className="delegation-card-list">
        {steps.map((step) => step.toolName === 'delegate_task'
          ? <DelegationCard key={step.id} step={step} onStop={onStop} />
          : <StepRow key={step.id} step={step} />)}
      </div>
    );
  }
  return (
    <div className={`run-tool-group ${open ? 'open' : ''}`}>
      <button className="run-tool-group-head" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <span className="run-tool-group-icon"><ToolGlyph step={steps[0]} /></span>
        <span className="run-tool-group-label">{toolGroupSummary(steps)}</span>
        <ChevronDown className={open ? 'open' : ''} size={14} />
      </button>
      {open && <div className="run-tool-group-steps">{steps.map((step) => <StepRow key={step.id} step={step} />)}</div>}
    </div>
  );
}

export function RunSteps({
  run,
  onResolveApproval,
  onStop,
  answerContent,
  expandSignal = 0,
}: {
  run: ChatRun;
  onResolveApproval?: (runId: string, choice: RunApprovalChoice) => void | Promise<void>;
  onStop?: () => void;
  answerContent?: string;
  expandSignal?: number;
}) {
  const normalizedAnswer = answerContent?.trim().replace(/\s+/g, ' ') ?? '';
  const repeatsAnswer = (part: string) => {
    const normalizedPart = part.trim().replace(/\s+/g, ' ');
    if (!normalizedPart || !normalizedAnswer) return false;
    if (normalizedPart === normalizedAnswer) return true;
    return Math.min(normalizedPart.length, normalizedAnswer.length) >= 40
      && (normalizedAnswer.startsWith(normalizedPart) || normalizedPart.startsWith(normalizedAnswer));
  };
  const reasoningParts = (run.reasoning ?? [])
    .map((part) => part.trim())
    .filter((part) => part.length > 0 && !repeatsAnswer(part));
  const timeline = run.timeline?.filter((item) => item.kind !== 'reasoning' || !repeatsAnswer(item.text));
  const timelineHasPlans = Boolean(timeline?.some((item) => item.kind === 'todos'));
  const toolCount = run.steps.length;
  const hasReasoning = reasoningParts.length > 0;
  const hasAnswer = run.assistantContent.trim().length > 0;
  const hasIncompletePlan = Boolean(run.todos?.some((todo) => todo.status === 'pending' || todo.status === 'in_progress'));
  const autoExpanded = run.status === 'waiting_for_approval'
    || (run.status === 'running' && !hasAnswer)
    || ((run.status === 'error' || run.status === 'cancelled' || run.status === 'interrupted') && hasIncompletePlan);
  const [expanded, setExpanded] = useState(autoExpanded);
  const now = useRunClock(run);
  useEffect(() => {
    // Keep live activity visible while the model is working, then collapse the
    // work log when answer text starts. Restored/completed runs start collapsed.
    setExpanded(autoExpanded);
  }, [autoExpanded]);
  useEffect(() => {
    if (expandSignal > 0) setExpanded(true);
  }, [expandSignal]);
  const duration = formatRunDuration(run, run.status === 'running' ? now : undefined);
  const header = run.status === 'running'
    ? `Worked${duration ? ` for ${duration}` : ''}`
    : run.status === 'waiting_for_approval'
      ? 'Approval needed'
    : run.status === 'cancelled'
      ? `Cancelled${duration ? ` after ${duration}` : ''}`
      : run.status === 'interrupted'
      ? `Stopped${duration ? ` after ${duration}` : ''}`
      : run.status === 'error'
        ? `Run failed${duration ? ` after ${duration}` : ''}`
      : `Worked${duration ? ` for ${duration}` : ''}`;
  const showReasoning = reasoningParts.length > 0;

  return (
    <section className={`run-steps ${run.status}`} data-run-id={run.id}>
      <button className="run-steps-toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        {run.status === 'running' ? <LoaderCircle className="run-step-spin" size={16} /> : null}
        {run.status === 'waiting_for_approval' ? <ShieldAlert className="run-step-waiting" size={16} /> : null}
        {run.status !== 'running' && run.status !== 'waiting_for_approval' && toolCount === 0 && hasReasoning
          ? <Brain size={15} />
          : null}
        <span className="run-steps-label">{header}</span>
        <span className="run-steps-count">{toolCount} {toolCount === 1 ? 'step' : 'steps'}</span>
        <ChevronDown className={expanded ? 'open' : ''} size={16} />
      </button>
      {expanded && (
        <div className="run-step-list">
          {!timelineHasPlans && run.todos && <RunPlan todos={run.todos} runStatus={run.status} />}
          {run.approval && <RunApprovalPrompt run={run} onResolveApproval={onResolveApproval} />}
          {timeline && timeline.length > 0 ? (
            (() => {
              const byId = new Map(run.steps.map((step) => [step.id, step]));
              const lastReasoningIndex = timeline.reduce(
                (last, item, index) => item.kind === 'reasoning' ? index : last,
                -1,
              );
              return timeline.map((item, index) => {
                if (item.kind === 'reasoning') {
                  return (
                    <ReasoningBlock
                      key={`think-${index}`}
                      text={item.text}
                      streaming={Boolean(run.reasoningStreaming && index === lastReasoningIndex)}
                    />
                  );
                }
                if (item.kind === 'todos') {
                  return <RunPlan key={`plan-${index}`} todos={item.todos} runStatus={run.status} />;
                }
                const groupSteps = item.stepIds.map((id) => byId.get(id)).filter((step): step is ChatRunStep => Boolean(step));
                return <ToolGroup key={`tools-${index}`} steps={groupSteps} onStop={onStop} />;
              });
            })()
          ) : (
            <>
              {reasoningParts.map((part, index) => (
                <ReasoningBlock
                  key={index}
                  text={part}
                  streaming={run.reasoningStreaming && index === reasoningParts.length - 1}
                />
              ))}
              {run.steps.map((step) => step.toolName === 'delegate_task'
                ? <DelegationCard key={step.id} step={step} onStop={onStop} />
                : <StepRow key={step.id} step={step} />)}
            </>
          )}
          {run.steps.length === 0 && !showReasoning && !timeline?.length && !run.approval && <div className="run-step-empty">Preparing response…</div>}
        </div>
      )}
    </section>
  );
}

/** Total token usage for a run, rendered as its own line below the answer. */
export function RunUsage({ run }: { run: ChatRun }) {
  if (!run.usage || run.usage.totalTokens <= 0) return null;
  return <div className="run-usage">{usageSummary(run)}</div>;
}
