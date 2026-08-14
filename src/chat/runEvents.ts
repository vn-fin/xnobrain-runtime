import type { SSEEvent } from '../api/stream';
import type { ChatMessage, ChatRun, ChatRunStep, ChatTodoItem, ChatTodoStatus, DelegationLogEntry, DelegationWorker, DelegationWorkerStatus, RunApprovalChoice, RunTimelineItem } from '../types';

type Data = Record<string, unknown>;
const APPROVAL_CHOICES: RunApprovalChoice[] = ['once', 'always', 'deny'];
const TODO_STATUSES: ChatTodoStatus[] = ['pending', 'in_progress', 'completed', 'cancelled'];

function record(value: unknown): Data {
  return value && typeof value === 'object' ? value as Data : {};
}

function string(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function readable(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return '';
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function number(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
}

function parsed(value?: string): Data {
  if (!value) return {};
  try { return record(JSON.parse(value)); } catch { return {}; }
}

function todoItems(value: unknown): ChatTodoItem[] | undefined {
  const data = typeof value === 'string' ? parsed(value) : record(value);
  if (!Array.isArray(data.todos)) return undefined;
  return data.todos.slice(0, 256).flatMap((item) => {
    const todo = record(item);
    const id = string(todo.id).trim().slice(0, 256);
    const content = string(todo.content).trim().slice(0, 4000);
    const status = string(todo.status) as ChatTodoStatus;
    return id && content && TODO_STATUSES.includes(status) ? [{ id, content, status }] : [];
  });
}

function sameTodos(left: ChatTodoItem[] | undefined, right: ChatTodoItem[]): boolean {
  if (!left || left.length !== right.length) return false;
  return left.every((item, index) => {
    const other = right[index];
    return item.id === other?.id && item.content === other.content && item.status === other.status;
  });
}

function basename(path: string): string {
  return path.split('/').filter(Boolean).pop() ?? path;
}

/**
 * The real conversations stream carries the event name inside the JSON body
 * (`data.event`) for every frame except `run.started`, which uses the SSE
 * `event:` line. Normalize both shapes to a single event type.
 */
function eventType(event: SSEEvent): string {
  return string(record(event.data).event) || event.event;
}

/** Backend timestamps arrive as `timestamp` (epoch seconds); older mocks use `ts`. */
function ts(data: Data): number | undefined {
  return number(data.timestamp) ?? number(data.ts);
}

/** Tool name arrives as `tool` on the live API and `tool_name` on legacy events. */
function toolNameOf(data: Data): string {
  return string(data.tool) || string(data.tool_name);
}

function approvalChoices(value: unknown, allowPermanent: boolean): RunApprovalChoice[] {
  const values = Array.isArray(value) ? value : [];
  const choices = values.filter((choice): choice is RunApprovalChoice =>
    typeof choice === 'string' && APPROVAL_CHOICES.includes(choice as RunApprovalChoice));
  const filtered = choices.filter((choice) => allowPermanent || choice !== 'always');
  if (filtered.length > 0) return filtered;
  return allowPermanent ? APPROVAL_CHOICES : APPROVAL_CHOICES.filter((choice) => choice !== 'always');
}

function approvalSubsystem(data: Data): 'skills' | 'memory' | undefined {
  const explicit = string(data.subsystem).toLowerCase();
  if (explicit === 'skills' || explicit === 'memory') return explicit;
  const description = `${string(data.description)} ${readable(data.command)}`.toLowerCase();
  if (description.includes('skills.write_approval')) return 'skills';
  if (description.includes('memory.write_approval') || description.includes('save to memory:')) return 'memory';
  return undefined;
}

function terminal(status: ChatRun['status']): boolean {
  return status === 'completed' || status === 'error' || status === 'interrupted' || status === 'cancelled';
}

function delegationWorkers(args: unknown): DelegationWorker[] {
  const input = record(args);
  const tasks = Array.isArray(input.tasks) ? input.tasks : [];
  const goals = tasks.length > 0
    ? tasks.map((task) => string(record(task).goal))
    : [string(input.goal)].filter(Boolean);
  return goals.map((goal, index) => ({
    index,
    goal,
    status: 'queued',
    queuePosition: index + 1,
    toolCount: 0,
    logs: [],
  }));
}

function normalizedWorkerStatus(value: unknown): DelegationWorkerStatus {
  const status = string(value);
  if (status === 'completed' || status === 'interrupted' || status === 'cancelled') return status;
  if (status === 'error' || status === 'failed' || status === 'timeout') return 'error';
  return 'running';
}

function updateDelegationWorker(run: ChatRun, data: Data, type: string): ChatRun {
  const stepIndex = [...run.steps].reverse().findIndex((step) =>
    step.toolName === 'delegate_task' && step.status === 'running' && step.delegation);
  if (stepIndex < 0) return run;
  const actualStepIndex = run.steps.length - 1 - stepIndex;
  const taskIndex = number(data.task_index);
  if (taskIndex === undefined) return run;
  const timestamp = ts(data);
  return {
    ...run,
    steps: run.steps.map((step, index) => {
      if (index !== actualStepIndex || !step.delegation) return step;
      const workers = step.delegation.workers.map((worker) => {
        if (worker.index !== taskIndex) return worker;
        let next: DelegationWorker = { ...worker, lastEventAt: timestamp ?? worker.lastEventAt };
        let log: DelegationLogEntry | undefined;
        if (type === 'delegation.worker.queued') {
          next = {
            ...next,
            status: 'queued',
            queuePosition: number(data.queue_position) ?? worker.queuePosition,
          };
        } else if (type === 'delegation.worker.started') {
          next = { ...next, status: 'running', queuePosition: undefined, startedAt: timestamp ?? worker.startedAt };
          log = { id: `${step.id}-${taskIndex}-${timestamp ?? Date.now()}-start`, timestamp, kind: 'start', message: string(data.goal) || worker.goal };
        } else if (type === 'delegation.worker.activity') {
          const tool = string(data.tool);
          next = {
            ...next,
            status: 'running',
            lastTool: tool || worker.lastTool,
            toolCount: number(data.tool_count) ?? (tool ? worker.toolCount + 1 : worker.toolCount),
          };
          log = {
            id: `${step.id}-${taskIndex}-${timestamp ?? Date.now()}-${next.logs.length}`,
            timestamp,
            kind: tool ? 'tool' : 'activity',
            message: string(data.message) || (tool ? tool : 'Working'),
            ...(tool ? { tool } : {}),
          };
        } else if (type === 'delegation.worker.text') {
          const delta = string(data.delta);
          const previous = next.logs[next.logs.length - 1];
          if (delta && previous?.kind === 'text') {
            next = {
              ...next,
              logs: [...next.logs.slice(0, -1), { ...previous, message: (previous.message + delta).slice(-12_000) }],
            };
          } else if (delta) {
            log = { id: `${step.id}-${taskIndex}-${timestamp ?? Date.now()}-text`, timestamp, kind: 'text', message: delta };
          }
        } else if (type === 'delegation.worker.completed') {
          const status = normalizedWorkerStatus(data.status);
          const summary = string(data.summary);
          next = {
            ...next,
            status,
            endedAt: timestamp,
            steps: number(data.api_calls),
            inputTokens: number(data.input_tokens),
            outputTokens: number(data.output_tokens),
            reasoningTokens: number(data.reasoning_tokens),
            summary: status === 'completed' ? summary : worker.summary,
            error: status === 'error' ? summary || 'Worker failed' : worker.error,
            filesRead: Array.isArray(data.files_read) ? data.files_read.filter((item): item is string => typeof item === 'string') : worker.filesRead,
            filesWritten: Array.isArray(data.files_written) ? data.files_written.filter((item): item is string => typeof item === 'string') : worker.filesWritten,
          };
          log = {
            id: `${step.id}-${taskIndex}-${timestamp ?? Date.now()}-complete`,
            timestamp,
            kind: status === 'completed' ? 'complete' : 'error',
            message: status === 'completed' ? 'Worker completed' : next.error || 'Worker failed',
          };
        }
        if (log) next = { ...next, logs: [...next.logs, log].slice(-300) };
        return next;
      });
      return {
        ...step,
        delegation: {
          concurrency: number(data.concurrency) ?? step.delegation.concurrency,
          workers,
        },
      };
    }),
  };
}

function initialRun(data: Data): ChatRun {
  return {
    id: string(data.run_id) || `run-${string(data.session_id) || 'active'}`,
    status: 'running',
    startedAt: ts(data),
    steps: [],
    assistantContent: '',
  };
}

function enrichOutputs(run: ChatRun, messages: unknown): ChatRun {
  if (!Array.isArray(messages)) return run;
  const outputs = messages.filter((message) => record(message).role === 'tool');
  let cursor = 0;
  const steps = run.steps.map((step) => {
    const index = outputs.findIndex((message, i) => i >= cursor && string(record(message).tool_name) === step.toolName);
    if (index < 0) return step;
    cursor = index + 1;
    return { ...step, output: string(record(outputs[index]).content) || step.output };
  });
  return { ...run, steps };
}

export function reduceRunEvent(run: ChatRun | null, event: SSEEvent): ChatRun | null {
  const data = record(event.data);
  const type = eventType(event);
  if (type === 'run.started') return initialRun(data);
  if (!run) return null;

  if (type === 'message.started') {
    return { ...run, messageId: string(record(data.message).id) || string(data.message_id) || run.messageId };
  }
  if (type === 'approval.request') {
    const subsystem = approvalSubsystem(data);
    const allowPermanent = subsystem !== undefined || data.allow_permanent !== false;
    return {
      ...run,
      status: terminal(run.status) ? run.status : 'waiting_for_approval',
      approval: {
        command: readable(data.command),
        description: string(data.description) || 'Approval required',
        choices: approvalChoices(data.choices, allowPermanent),
        allowPermanent,
        ...(subsystem ? { subsystem } : {}),
      },
    };
  }
  if (type === 'approval.responded') {
    return { ...run, status: terminal(run.status) ? run.status : 'running', approval: undefined };
  }
  if (type === 'todo.updated') {
    const todos = todoItems(data);
    if (todos === undefined) return run;
    const timeline = [...(run.timeline ?? [])];
    if (todos.length > 0 && !sameTodos(run.todos, todos)) {
      timeline.push({ kind: 'todos', todos });
    }
    return { ...run, todos, timeline };
  }
  if (type.startsWith('delegation.worker.')) {
    return updateDelegationWorker(run, data, type);
  }
  if (type === 'tool.started') {
    const toolName = toolNameOf(data) || 'tool';
    const step: ChatRunStep = {
      id: `${run.id}-${run.steps.length}-${toolName}`,
      toolName,
      preview: string(data.preview),
      ...(data.args !== undefined ? { args: data.args } : {}),
      ...(toolName === 'delegate_task' ? {
        delegation: {
          concurrency: number(data.concurrency) ?? 3,
          workers: delegationWorkers(data.args),
        },
      } : {}),
      status: 'running',
      startedAt: ts(data),
    };
    const timeline = [...(run.timeline ?? [])];
    const reasoning = run.reasoning ? [...run.reasoning] : [];

    // Hermes may emit assistant text for an intermediate model iteration
    // immediately before its tool call. History identifies that message with
    // `finish_reason=tool_calls`; live SSE can only identify the boundary once
    // tool.started arrives. Move that provisional text into the work log so it
    // does not accumulate in (or duplicate) the final answer bubble.
    const interimText = run.assistantContent.trim();
    const previous = timeline[timeline.length - 1];
    if (interimText && !(previous?.kind === 'reasoning' && previous.text.trim() === interimText)) {
      reasoning.push(interimText);
      timeline.push({ kind: 'reasoning', text: interimText });
    }
    const last = timeline[timeline.length - 1];
    if (last?.kind === 'tools') {
      timeline[timeline.length - 1] = { ...last, stepIds: [...last.stepIds, step.id] };
    } else {
      timeline.push({ kind: 'tools', stepIds: [step.id] });
    }
    return {
      ...run,
      steps: [...run.steps, step],
      assistantContent: '',
      reasoning: reasoning.length > 0 ? reasoning : run.reasoning,
      reasoningStreaming: false,
      timeline,
    };
  }
  if (type === 'tool.progress') {
    const toolName = toolNameOf(data);
    const index = [...run.steps].reverse().findIndex((step) => step.status === 'running' && (!toolName || step.toolName === toolName));
    if (index < 0) return run;
    const actual = run.steps.length - 1 - index;
    return { ...run, steps: run.steps.map((step, i) => i === actual ? { ...step, progress: string(data.delta) } : step) };
  }
  if (type.startsWith('write.')) {
    const toolName = toolNameOf(data);
    const index = [...run.steps].reverse().findIndex((step) =>
      step.status === 'running' && (!toolName || step.toolName === toolName));
    if (index < 0) return run;
    const actual = run.steps.length - 1 - index;
    const subsystem = string(data.subsystem) === 'skills' ? 'Skill' : 'Memory';
    const progress = type === 'write.applied'
      ? `${subsystem} write saved`
      : type === 'write.rejected'
        ? `${subsystem} write denied`
        : type === 'write.pending'
          ? `${subsystem} write remains pending`
          : `${subsystem} write could not be saved`;
    return {
      ...run,
      steps: run.steps.map((step, i) => i === actual ? { ...step, progress } : step),
    };
  }
  if (type === 'tool.completed' || type === 'tool.failed') {
    const toolName = toolNameOf(data);
    const failed = type === 'tool.failed' || data.error === true;
    const endedAt = ts(data);
    const index = [...run.steps].reverse().findIndex((step) => step.status === 'running' && (!toolName || step.toolName === toolName));
    if (index < 0) return run;
    const actual = run.steps.length - 1 - index;
    return {
      ...run,
      steps: run.steps.map((step, i) => {
        if (i !== actual) return step;
        const durationSec = number(data.duration)
          ?? (endedAt !== undefined && step.startedAt !== undefined ? endedAt - step.startedAt : undefined);
        return {
          ...step,
          status: failed ? 'error' : 'completed',
          endedAt,
          ...(string(data.output) ? { output: string(data.output) } : {}),
          ...(durationSec !== undefined ? { durationSec } : {}),
        };
      }),
    };
  }
  if (type === 'message.delta' || type === 'assistant.delta') {
    // The first visible message token closes the current reasoning phase. A
    // later reasoning.delta therefore starts a new phase after a tool result.
    return {
      ...run,
      assistantContent: run.assistantContent + string(data.delta),
      reasoningStreaming: false,
    };
  }
  if (type === 'reasoning.delta') {
    const delta = string(data.delta) || string(data.text);
    if (!delta) return run;
    const parts = run.reasoning ? [...run.reasoning] : [];
    const timeline = [...(run.timeline ?? [])];
    const last = timeline[timeline.length - 1];
    if (run.reasoningStreaming && parts.length > 0) {
      parts[parts.length - 1] += delta;
      if (last?.kind === 'reasoning') timeline[timeline.length - 1] = { ...last, text: last.text + delta };
      else timeline.push({ kind: 'reasoning', text: delta });
    } else {
      parts.push(delta);
      timeline.push({ kind: 'reasoning', text: delta });
    }
    return { ...run, reasoning: parts, reasoningStreaming: true, timeline };
  }
  if (type === 'reasoning.available' || type === 'reasoning.completed') {
    const text = string(data.text);
    if (!text) return { ...run, reasoningStreaming: false };
    const parts = run.reasoning ? [...run.reasoning] : [];
    const timeline = [...(run.timeline ?? [])];
    const last = timeline[timeline.length - 1];
    if (run.reasoningStreaming && parts.length > 0) {
      parts[parts.length - 1] = text;
      if (last?.kind === 'reasoning') timeline[timeline.length - 1] = { ...last, text };
      else timeline.push({ kind: 'reasoning', text });
    } else if (!parts.includes(text)) {
      parts.push(text);
      timeline.push({ kind: 'reasoning', text });
    }
    return { ...run, reasoning: parts, reasoningStreaming: false, timeline };
  }
  if (type === 'assistant.completed' || type === 'message.completed') {
    return {
      ...run,
      assistantContent: string(data.content) || run.assistantContent,
      messageId: string(data.message_id) || run.messageId,
      reasoningStreaming: false,
    };
  }
  if (type === 'error' || type === 'run.failed') {
    return {
      ...run,
      status: 'error',
      endedAt: ts(data),
      approval: undefined,
      steps: run.steps.map((step) => step.status === 'running' ? { ...step, status: 'error' } : step),
    };
  }
  if (type === 'run.cancelled') {
    return {
      ...run,
      status: 'cancelled',
      endedAt: ts(data),
      approval: undefined,
      reasoningStreaming: false,
      steps: run.steps.map((step) => step.status === 'running'
        ? { ...step, status: 'cancelled', endedAt: ts(data) }
        : step),
    };
  }
  if (type === 'run.completed') {
    const usage = record(data.usage);
    const completed = enrichOutputs({
      ...run,
      id: string(data.run_id) || run.id,
      status: 'completed',
      endedAt: ts(data),
      approval: undefined,
      // `output` is the authoritative final message. Replacing the provisional
      // streamed value also repairs a dropped/partial final delta.
      assistantContent: string(data.output) || run.assistantContent,
      reasoningStreaming: false,
      usage: {
        totalTokens: number(usage.total_tokens) ?? 0,
        ...(number(usage.input_tokens) !== undefined ? { inputTokens: number(usage.input_tokens) } : {}),
        ...(number(usage.output_tokens) !== undefined ? { outputTokens: number(usage.output_tokens) } : {}),
      },
      steps: run.steps.map((step) => step.status === 'running' ? { ...step, status: 'completed', endedAt: ts(data) } : step),
    }, data.messages);
    return completed;
  }
  if (type === 'done' && (run.status === 'running' || run.status === 'waiting_for_approval')) {
    return {
      ...run,
      status: 'completed',
      endedAt: ts(data),
      approval: undefined,
      steps: run.steps.map((step) => step.status === 'running'
        ? { ...step, status: 'completed', endedAt: ts(data) }
        : step),
    };
  }
  return run;
}

/** Collapses a possibly huge/multiline preview to a short single-line label. */
export function shortPreview(value: string, max = 72): string {
  const firstLine = value.replace(/\s+/g, ' ').trim();
  if (firstLine.length <= max) return firstLine;
  return `${firstLine.slice(0, max - 1).trimEnd()}…`;
}

/**
 * Returns the full code/command body worth showing in a code block, or '' when
 * the preview is a short label rather than code.
 */
export function previewCode(step: ChatRunStep): string {
  const kind = toolKind(step);
  if (kind !== 'terminal' && kind !== 'code') return '';
  const preview = step.preview?.trim() ?? '';
  if (!preview) return '';
  // Show as code when it's multiline or clearly longer than a label.
  if (preview.includes('\n') || preview.length > 72) return step.preview;
  return '';
}

export function stepLabel(step: ChatRunStep): string {
  const output = parsed(step.output);
  const args = record(step.args);
  const preview = shortPreview(step.preview);
  if (step.toolName.startsWith('mcp__')) {
    const [, server = 'server', tool = 'tool'] = step.toolName.split('__');
    return `MCP ${server.replaceAll('_', ' ')} · ${tool.replaceAll('_', ' ')}`;
  }
  if (step.toolName === 'write_file') {
    const path = string(args.path) || string(output.resolved_path) || step.preview;
    return `Created ${basename(path) || 'file'}`;
  }
  if (step.toolName === 'patch' || step.toolName === 'edit_file') {
    const path = string(args.path) || step.preview;
    return `Edited ${basename(path) || 'file'}`;
  }
  if (step.toolName === 'terminal') return preview ? `Ran ${preview}` : 'Ran command';
  if (step.toolName === 'execute_code') return preview ? `Executed code · ${preview}` : 'Executed code';
  if (step.toolName === 'skill_view') return `Viewed skill ${string(args.name) || string(output.name) || preview}`.trim();
  if (step.toolName === 'todo') return preview ? preview.charAt(0).toUpperCase() + preview.slice(1) : 'Updated tasks';
  if (step.toolName === 'vision_analyze') return preview ? `Analyzed image · ${preview}` : 'Analyzed image';
  if (step.toolName === 'search_files' || step.toolName.includes('search') || step.toolName.includes('grep')) {
    return `Searched ${preview || string(args.query) || 'files'}`.trim();
  }
  if (step.toolName === 'read_file' || step.toolName.includes('read')) return `Read ${preview || string(args.path)}`.trim();
  if (step.toolName.includes('list') || step.toolName.includes('tree')) return `Listed ${preview || string(args.path)}`.trim();
  return `${step.toolName.replaceAll('_', ' ')}${preview ? ` · ${preview}` : ''}`;
}

export type ToolKind = 'terminal' | 'file-write' | 'code' | 'search' | 'read' | 'list' | 'skill' | 'mcp' | 'generic';

/** Maps a tool step to a visual category so the UI can pick an icon. */
export function toolKind(step: ChatRunStep): ToolKind {
  const name = step.toolName.toLowerCase();
  if (name.startsWith('mcp__')) return 'mcp';
  if (name.includes('skill')) return 'skill';
  if (name.includes('terminal') || name.includes('shell') || name.includes('bash') || name.includes('command')) return 'terminal';
  if (name.includes('write') || name.includes('create') || name.includes('edit')) return 'file-write';
  if (name.includes('execute') || name.includes('code') || name.includes('python')) return 'code';
  if (name.includes('search') || name.includes('grep') || name.includes('find')) return 'search';
  if (name.includes('read') || name.includes('view') || name.includes('cat')) return 'read';
  if (name.includes('list') || name.includes('ls') || name.includes('tree')) return 'list';
  return 'generic';
}

/** Summarizes a batch of tool steps into a grouped phrase, e.g.
 *  "Read files, ran commands, searched the web". */
export function toolGroupSummary(steps: ChatRunStep[]): string {
  const phrases: string[] = [];
  const seen = new Set<string>();
  for (const step of steps) {
    const name = step.toolName.toLowerCase();
    let phrase: string;
    if (name.includes('compact') || name === 'context') {
      phrase = 'compacted context';
    } else if (name.startsWith('mcp__')) {
      phrase = 'used MCP tools';
    } else if (name.includes('web') || name.includes('browser') || name.includes('navigate') || name.includes('fetch')) {
      phrase = 'searched the web';
    } else {
      const kind = toolKind(step);
      phrase = kind === 'terminal' ? 'ran commands'
        : kind === 'code' ? 'ran code'
        : kind === 'file-write' ? 'edited files'
        : kind === 'read' ? 'read files'
        : kind === 'search' ? 'searched files'
        : kind === 'list' ? 'listed files'
        : kind === 'skill' ? 'used skills'
        : `used ${step.toolName.replaceAll('_', ' ')}`;
    }
    if (!seen.has(phrase)) { seen.add(phrase); phrases.push(phrase); }
  }
  const joined = phrases.join(', ');
  return joined ? joined.charAt(0).toUpperCase() + joined.slice(1) : 'Used tools';
}

type HistoryToolCall = { id?: string; call_id?: string; function?: { name?: string; arguments?: string } };

/** Best-effort single-line preview from parsed tool arguments. */
function argPreview(args: Data): string {
  for (const key of ['command', 'cmd', 'query', 'pattern', 'path', 'file_path', 'name', 'url']) {
    const value = args[key];
    if (typeof value === 'string' && value.trim()) return value;
  }
  return '';
}

/**
 * Rebuild runs from a persisted message history. Groups activity per turn
 * (each user message opens a turn; the turn closes on the assistant's final
 * answer). Assistant `tool_calls` become steps, matched to their `tool` result
 * messages by id; reasoning and timing are carried through so the run renders
 * as a single collapsible "Worked for …" block before the final answer.
 */
export function historicalRuns(messages: ChatMessage[]): ChatRun[] {
  const runs: ChatRun[] = [];

  let steps: ChatRunStep[] = [];
  let stepByCallId = new Map<string, number>();
  let reasoning: string[] = [];
  let timeline: RunTimelineItem[] = [];
  let todos: ChatTodoItem[] | undefined;
  let startedAt: number | undefined;
  let turnIndex = 0;

  // Group consecutive reasoning into one text block; a new reasoning block only
  // starts after a tool batch.
  const addReasoning = (text: string) => {
    const value = text.trim();
    if (!value) return;
    reasoning.push(value);
    const last = timeline[timeline.length - 1];
    if (last && last.kind === 'reasoning') last.text += `\n\n${value}`;
    else timeline.push({ kind: 'reasoning', text: value });
  };

  // Append a tool step, grouping consecutive tool steps into one batch.
  const addStep = (step: ChatRunStep) => {
    steps.push(step);
    const last = timeline[timeline.length - 1];
    if (last && last.kind === 'tools') last.stepIds.push(step.id);
    else timeline.push({ kind: 'tools', stepIds: [step.id] });
  };

  const reset = () => {
    steps = [];
    stepByCallId = new Map();
    reasoning = [];
    timeline = [];
    todos = undefined;
    startedAt = undefined;
  };

  const flush = (finalMessageId?: string | number, endedAt?: number) => {
    if (steps.length === 0 && reasoning.length === 0) {
      reset();
      return;
    }
    runs.push({
      id: `history-run-${turnIndex}`,
      status: 'completed',
      steps,
      assistantContent: '',
      ...(reasoning.length ? { reasoning } : {}),
      ...(timeline.length ? { timeline } : {}),
      ...(todos !== undefined ? { todos } : {}),
      ...(startedAt !== undefined ? { startedAt } : {}),
      ...(endedAt !== undefined ? { endedAt } : {}),
      ...(finalMessageId !== undefined ? { insertBeforeMessageId: finalMessageId } : {}),
    });
    turnIndex += 1;
    reset();
  };

  for (const message of messages) {
    if (message.role === 'user') {
      // A new user prompt starts a fresh turn; flush anything still pending.
      flush();
      continue;
    }

    if (message.role === 'tool') {
      const index = message.toolCallId ? stepByCallId.get(message.toolCallId) : undefined;
      if (index !== undefined) {
        const step = steps[index];
        const endedAt = message.timestamp;
        const durationSec = endedAt !== undefined && step.startedAt !== undefined ? endedAt - step.startedAt : undefined;
        steps[index] = {
          ...step,
          output: message.content || step.output,
          ...(endedAt !== undefined ? { endedAt } : {}),
          ...(durationSec !== undefined && durationSec >= 0 ? { durationSec } : {}),
        };
        if (step.toolName === 'todo') {
          const snapshot = todoItems(message.content);
          if (snapshot !== undefined) {
            if (snapshot.length > 0 && !sameTodos(todos, snapshot)) {
              timeline.push({ kind: 'todos', todos: snapshot });
            }
            todos = snapshot;
          }
        }
      } else if (message.content) {
        // Orphan tool output with no matching call — keep it visible.
        addStep({
          id: `history-${message.id}`,
          toolName: message.toolName || 'tool',
          preview: '',
          output: message.content,
          status: 'completed',
          ...(message.timestamp !== undefined ? { startedAt: message.timestamp } : {}),
        });
      }
      continue;
    }

    if (message.role !== 'assistant') continue;

    if (startedAt === undefined && message.timestamp !== undefined) startedAt = message.timestamp;
    if (message.reasoning?.trim()) addReasoning(message.reasoning);

    const isFinalAnswer = message.finishReason === 'stop' || (!message.toolCalls && message.content.trim().length > 0);
    // Intermediate assistant prose (a message that still calls tools) is folded
    // into the turn's "thinking" instead of rendering as its own bubble.
    if (!isFinalAnswer && message.content.trim()) addReasoning(message.content);

    // Turn tool_calls into grouped steps.
    if (message.toolCalls) {
      let calls: HistoryToolCall[] = [];
      try {
        const parsedCalls = JSON.parse(message.toolCalls);
        if (Array.isArray(parsedCalls)) calls = parsedCalls as HistoryToolCall[];
      } catch { calls = []; }
      for (const call of calls) {
        const callId = call.call_id || call.id || '';
        const toolName = call.function?.name || 'tool';
        const args = parsed(call.function?.arguments);
        stepByCallId.set(callId, steps.length);
        addStep({
          id: `history-${message.id}-${steps.length}-${toolName}`,
          toolName,
          preview: argPreview(args),
          args,
          status: 'completed',
          ...(message.timestamp !== undefined ? { startedAt: message.timestamp } : {}),
        });
      }
    }

    // A final answer closes the turn and becomes the visible chat message.
    if (isFinalAnswer) flush(message.id, message.timestamp);
  }

  flush();
  return runs;
}

export function formatRunDuration(run: ChatRun, now?: number): string {
  const end = run.endedAt ?? now;
  if (run.startedAt === undefined || end === undefined) return '';
  let remaining = Math.max(0, Math.floor(end - run.startedAt));
  const days = Math.floor(remaining / 86_400);
  remaining %= 86_400;
  const hours = Math.floor(remaining / 3_600);
  remaining %= 3_600;
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining % 60;
  const parts: string[] = [];
  if (days > 0) parts.push(`${days}d`);
  if (hours > 0) parts.push(`${hours}h`);
  if (minutes > 0) parts.push(`${minutes}m`);
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds}s`);
  return parts.join(' ');
}

export function formatStepDuration(step: ChatRunStep): string {
  const seconds = step.durationSec
    ?? (step.startedAt !== undefined && step.endedAt !== undefined ? step.endedAt - step.startedAt : undefined);
  if (seconds === undefined || seconds < 0) return '';
  if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}
