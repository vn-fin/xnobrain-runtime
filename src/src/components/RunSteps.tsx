import { useEffect, useRef, useState } from 'react';
import {
  Brain,
  CheckCircle2,
  ChevronDown,
  CircleStop,
  Code,
  FileText,
  FolderTree,
  LoaderCircle,
  Search,
  ShieldAlert,
  Sparkles,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-react';
import { formatRunDuration, formatStepDuration, previewCode, stepLabel, toolGroupSummary, toolKind } from '../chat/runEvents';
import type { ChatRun, ChatRunStep, RunApprovalChoice } from '../types';
import { Markdown } from './Markdown';

const APPROVAL_ORDER: RunApprovalChoice[] = ['once', 'always', 'deny'];
const APPROVAL_LABELS: Record<RunApprovalChoice, string> = {
  once: 'Allow once',
  always: 'Always allow',
  deny: 'Deny',
};

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
  return <Wrench size={size} />;
}

function StatusIcon({ status }: { status: ChatRunStep['status'] }) {
  if (status === 'running') return <LoaderCircle className="run-step-spin" size={15} />;
  if (status === 'error') return <XCircle className="run-step-error" size={15} />;
  if (status === 'interrupted' || status === 'cancelled') return <CircleStop className="run-step-muted" size={15} />;
  return <CheckCircle2 className="run-step-ok" size={15} />;
}

function ReasoningBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  // Mirror assistant-ui behaviour: auto-expand the trace while the model is
  // thinking, then collapse once it finishes — unless the user toggled it.
  const [open, setOpen] = useState(!!streaming);
  const touched = useRef(false);
  useEffect(() => {
    if (!touched.current) setOpen(!!streaming);
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
      {open && <div className="run-reasoning-body"><Markdown content={text} /></div>}
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
  const approval = run.approval;
  if (!approval) return null;
  const choices = APPROVAL_ORDER.filter((choice) =>
    approval.choices.includes(choice) && (choice !== 'always' || approval.allowPermanent));

  const respond = async (choice: RunApprovalChoice) => {
    if (!onResolveApproval || submitting) return;
    if (choice === 'always' && !window.confirm('Always approve matching requests for this agent profile?')) return;
    setSubmitting(choice);
    try {
      await onResolveApproval(run.id, choice);
    } finally {
      setSubmitting(null);
    }
  };

  return (
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
            onClick={() => void respond(choice)}
          >
            {submitting === choice && <LoaderCircle className="run-step-spin" size={13} />}
            {APPROVAL_LABELS[choice]}
          </button>
        ))}
      </div>
    </div>
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

/** A grouped batch of consecutive tool steps, shown as one summary line
 *  ("Read files, ran commands, …") that expands to the individual steps. */
function ToolGroup({ steps }: { steps: ChatRunStep[] }) {
  const [open, setOpen] = useState(false);
  if (steps.length === 0) return null;
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
}: {
  run: ChatRun;
  onResolveApproval?: (runId: string, choice: RunApprovalChoice) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(run.status === 'running' || run.status === 'waiting_for_approval');
  useEffect(() => {
    if (run.status === 'waiting_for_approval') setExpanded(true);
  }, [run.status]);
  const duration = formatRunDuration(run);
  const toolCount = run.steps.length;
  const hasReasoning = (run.reasoning ?? []).some((part) => part.trim().length > 0);
  const header = run.status === 'running'
    ? 'Working…'
    : run.status === 'waiting_for_approval'
      ? 'Approval needed'
    : run.status === 'cancelled'
      ? `Cancelled${duration ? ` after ${duration}` : ''}`
      : run.status === 'interrupted'
      ? `Stopped${duration ? ` after ${duration}` : ''}`
      : run.status === 'error'
        ? `Run failed${duration ? ` after ${duration}` : ''}`
      : toolCount === 0 && hasReasoning
        ? `Thought${duration ? ` for ${duration}` : ''}`
        : `Worked${duration ? ` for ${duration}` : ''}`;
  const reasoningParts = (run.reasoning ?? [])
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  const showReasoning = reasoningParts.length > 0;

  return (
    <section className={`run-steps ${run.status}`}>
      <button className="run-steps-toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        {run.status === 'running' ? <LoaderCircle className="run-step-spin" size={16} /> : null}
        {run.status === 'waiting_for_approval' ? <ShieldAlert className="run-step-waiting" size={16} /> : null}
        {run.status !== 'running' && run.status !== 'waiting_for_approval' && toolCount === 0 && hasReasoning
          ? <Brain size={15} />
          : null}
        <span>{header}</span>
        {toolCount > 0 && <span className="run-steps-count">{toolCount} {toolCount === 1 ? 'step' : 'steps'}</span>}
        <ChevronDown className={expanded ? 'open' : ''} size={16} />
      </button>
      {expanded && (
        <div className="run-step-list">
          {run.approval && <RunApprovalPrompt run={run} onResolveApproval={onResolveApproval} />}
          {run.timeline && run.timeline.length > 0 ? (
            (() => {
              const byId = new Map(run.steps.map((step) => [step.id, step]));
              return run.timeline.map((item, index) => {
                if (item.kind === 'reasoning') {
                  return <div className="run-thinking-text" key={`think-${index}`}><Markdown content={item.text} /></div>;
                }
                const groupSteps = item.stepIds.map((id) => byId.get(id)).filter((step): step is ChatRunStep => Boolean(step));
                return <ToolGroup key={`tools-${index}`} steps={groupSteps} />;
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
              {run.steps.map((step) => <StepRow key={step.id} step={step} />)}
            </>
          )}
          {run.steps.length === 0 && !showReasoning && !run.timeline && !run.approval && <div className="run-step-empty">Preparing response…</div>}
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
