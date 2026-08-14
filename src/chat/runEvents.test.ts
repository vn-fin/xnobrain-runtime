import { describe, expect, it } from 'vitest';
import type { SSEEvent } from '../api/stream';
import { formatRunDuration, formatStepDuration, historicalRuns, previewCode, reduceRunEvent, shortPreview, stepLabel, toolKind } from './runEvents';
import type { ChatRun, ChatRunStep } from '../types';

/**
 * Mirrors a real conversations/chat/stream capture. Every frame except
 * `run.started` carries its type in the JSON body (`data.event`) and comes in
 * on the default SSE `message` event line.
 */
const RUN_ID = 'run_bd58076cfd434f3bbd4e8abefa8484aa';
const OUTPUT = 'There are 3 “r” letters in “strawberry”.';

const body = (data: Record<string, unknown>): SSEEvent => ({ event: 'message', data });

const stream: SSEEvent[] = [
  { event: 'run.started', data: { run_id: RUN_ID, session_id: 'sess', status: 'started', timestamp: 1783992910.5 } },
  body({ event: 'tool.started', run_id: RUN_ID, timestamp: 1783992910.621636, tool: 'terminal', preview: "python3 - <<'PY'" }),
  body({ event: 'tool.completed', run_id: RUN_ID, timestamp: 1783992910.7272363, tool: 'terminal', duration: 0.105, error: false }),
  body({ event: 'message.delta', run_id: RUN_ID, delta: 'There are ' }),
  body({ event: 'message.delta', run_id: RUN_ID, delta: '3 “r” letters in “strawberry”.' }),
  body({ event: 'reasoning.available', run_id: RUN_ID, timestamp: 1783992914.04, text: 'Count the letter r in strawberry.' }),
  body({ event: 'run.completed', run_id: RUN_ID, timestamp: 1783992914.06, output: OUTPUT, usage: { input_tokens: 29999, output_tokens: 91, total_tokens: 30090 } }),
];

function fold(events: SSEEvent[]): ChatRun | null {
  return events.reduce<ChatRun | null>((run, event) => reduceRunEvent(run, event), null);
}

describe('reduceRunEvent (live API format)', () => {
  it('creates a run from the run.started SSE event line', () => {
    const run = reduceRunEvent(null, stream[0]);
    expect(run?.id).toBe(RUN_ID);
    expect(run?.status).toBe('running');
  });

  it('records a tool step from body-typed tool.started/completed events', () => {
    const run = fold(stream.slice(0, 3));
    expect(run?.steps).toHaveLength(1);
    const step = run!.steps[0];
    expect(step.toolName).toBe('terminal');
    expect(step.status).toBe('completed');
    expect(step.durationSec).toBeCloseTo(0.105, 3);
    expect(toolKind(step)).toBe('terminal');
    expect(formatStepDuration(step)).toBe('105ms');
  });

  it('retains delegation tasks and consolidated worker results', () => {
    const args = { tasks: [{ goal: 'Inspect the implementation.' }, { goal: 'Run focused tests.' }] };
    const output = JSON.stringify({
      results: [
        { task_index: 0, status: 'completed', summary: 'Inspected.' },
        { task_index: 1, status: 'completed', summary: 'Passed.' },
      ],
      total_duration_seconds: 1.4,
    });
    const run = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'delegate_task', args }),
      body({ event: 'tool.completed', run_id: RUN_ID, tool: 'delegate_task', output, duration: 1.4, error: false }),
    ]);

    expect(run?.steps[0]).toMatchObject({
      toolName: 'delegate_task',
      args,
      output,
      status: 'completed',
      durationSec: 1.4,
    });
  });

  it('tracks queued workers, live activity, logs, and completion usage', () => {
    const args = { tasks: Array.from({ length: 5 }, (_, index) => ({ goal: `Research conference ${index + 1}.` })) };
    const run = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'delegate_task', args, concurrency: 3 }),
      body({ event: 'delegation.worker.queued', run_id: RUN_ID, task_index: 4, task_count: 5, concurrency: 3, queue_position: 2 }),
      body({ event: 'delegation.worker.started', run_id: RUN_ID, task_index: 0, task_count: 5, concurrency: 3, timestamp: 10 }),
      body({ event: 'delegation.worker.activity', run_id: RUN_ID, task_index: 0, concurrency: 3, timestamp: 11, kind: 'tool', tool: 'web_search', message: 'KDD proceedings', tool_count: 1, api_calls: 1, input_tokens: 800, output_tokens: 120, reasoning_tokens: 5 }),
      body({ event: 'delegation.worker.text', run_id: RUN_ID, task_index: 0, concurrency: 3, timestamp: 12, delta: 'Drafting findings.' }),
      body({ event: 'delegation.worker.completed', run_id: RUN_ID, task_index: 0, concurrency: 3, timestamp: 13, status: 'completed', summary: 'Found papers.', input_tokens: 1200, output_tokens: 300, reasoning_tokens: 10, api_calls: 2, files_read: ['/workspace/a.md'], files_written: [] }),
    ]);

    const delegation = run?.steps[0].delegation;
    expect(delegation?.concurrency).toBe(3);
    expect(delegation?.workers).toHaveLength(5);
    expect(delegation?.workers[0]).toMatchObject({
      status: 'completed',
      lastTool: 'web_search',
      toolCount: 1,
      steps: 2,
      inputTokens: 1200,
      outputTokens: 300,
      summary: 'Found papers.',
      filesRead: ['/workspace/a.md'],
    });
    expect(delegation?.workers[0].logs.map((entry) => entry.kind)).toEqual(['start', 'tool', 'text', 'complete']);
    expect(delegation?.workers[4]).toMatchObject({ status: 'queued', queuePosition: 2 });
  });

  it('updates exact worker steps and tokens before delegation completes', () => {
    const run = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'delegate_task', args: { goal: 'Inspect usage.' }, concurrency: 1 }),
      body({ event: 'delegation.worker.started', run_id: RUN_ID, task_index: 0, concurrency: 1, timestamp: 10 }),
      body({ event: 'delegation.worker.activity', run_id: RUN_ID, task_index: 0, concurrency: 1, timestamp: 11, tool: 'terminal', tool_count: 2, api_calls: 3, input_tokens: 2400, output_tokens: 360, reasoning_tokens: 40 }),
    ]);

    expect(run?.steps[0].delegation?.workers[0]).toMatchObject({
      status: 'running',
      toolCount: 2,
      steps: 3,
      inputTokens: 2400,
      outputTokens: 360,
      reasoningTokens: 40,
    });
  });

  it('tracks authoritative todo snapshots without exposing generic tool output', () => {
    const run = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'todo', preview: 'planning 2 task(s)' }),
      body({ event: 'tool.completed', run_id: RUN_ID, tool: 'todo', error: false }),
      body({
        event: 'todo.updated',
        run_id: RUN_ID,
        todos: [
          { id: 'research', content: 'Research the topic', status: 'completed' },
          { id: 'summarize', content: 'Summarize the findings', status: 'in_progress' },
        ],
      }),
    ]);

    expect(run?.todos).toEqual([
      { id: 'research', content: 'Research the topic', status: 'completed' },
      { id: 'summarize', content: 'Summarize the findings', status: 'in_progress' },
    ]);
    expect(run?.timeline).toEqual([
      { kind: 'tools', stepIds: [`${RUN_ID}-0-todo`] },
      { kind: 'todos', todos: run?.todos },
    ]);
  });

  it('inserts every changed todo snapshot into the activity timeline', () => {
    const first = [
      { id: 'research', content: 'Research sources', status: 'in_progress' },
      { id: 'write', content: 'Write the answer', status: 'pending' },
    ];
    const second = [
      { id: 'research', content: 'Research sources', status: 'completed' },
      { id: 'write', content: 'Write the answer', status: 'in_progress' },
    ];
    const run = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'todo' }),
      body({ event: 'tool.completed', run_id: RUN_ID, tool: 'todo' }),
      body({ event: 'todo.updated', run_id: RUN_ID, todos: first }),
      body({ event: 'reasoning.delta', run_id: RUN_ID, delta: 'I found the sources.' }),
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'todo' }),
      body({ event: 'tool.completed', run_id: RUN_ID, tool: 'todo' }),
      body({ event: 'todo.updated', run_id: RUN_ID, todos: second }),
    ]);

    expect(run?.timeline?.map((item) => item.kind)).toEqual(['tools', 'todos', 'reasoning', 'tools', 'todos']);
    expect(run?.timeline?.filter((item) => item.kind === 'todos')).toHaveLength(2);
    expect(run?.todos).toEqual(second);
  });

  it('marks the tool step as errored when error is true', () => {
    const failing = [
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'terminal', preview: 'boom' }),
      body({ event: 'tool.completed', run_id: RUN_ID, tool: 'terminal', duration: 0.01, error: true }),
    ];
    const run = fold(failing);
    expect(run?.steps[0].status).toBe('error');
  });

  it('uses terminal run failure and cancellation events', () => {
    const failed = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'terminal' }),
      body({ event: 'run.failed', run_id: RUN_ID, timestamp: 2, message: 'failed' }),
    ]);
    expect(failed?.status).toBe('error');
    expect(failed?.steps[0].status).toBe('error');

    const cancelled = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'terminal' }),
      body({ event: 'run.cancelled', run_id: RUN_ID, timestamp: 2 }),
    ]);
    expect(cancelled?.status).toBe('cancelled');
    expect(cancelled?.steps[0].status).toBe('cancelled');
  });

  it('accumulates visible text only from message.delta frames', () => {
    const run = fold(stream);
    expect(run?.assistantContent).toBe(OUTPUT);
  });

  it('captures reasoning without leaking it into the message', () => {
    const run = fold(stream);
    expect(run?.reasoning).toEqual(['Count the letter r in strawberry.']);
    expect(run?.reasoningStreaming).toBe(false);
    expect(run?.assistantContent).not.toContain('Count the letter');
    expect(run?.timeline).toEqual([
      { kind: 'tools', stepIds: [`${RUN_ID}-0-terminal`] },
      { kind: 'reasoning', text: 'Count the letter r in strawberry.' },
    ]);
  });

  it('labels MCP and skill tools as distinct activity', () => {
    const mcp: ChatRunStep = {
      id: 'mcp',
      toolName: 'mcp__news_server__search_headlines',
      preview: '',
      status: 'completed',
    };
    const skill: ChatRunStep = {
      id: 'skill',
      toolName: 'skill_view',
      preview: 'news-summary',
      status: 'completed',
    };
    expect(toolKind(mcp)).toBe('mcp');
    expect(stepLabel(mcp)).toBe('MCP news server · search headlines');
    expect(toolKind(skill)).toBe('skill');
  });

  it('surfaces and clears pending approval requests', () => {
    const waiting = fold([
      stream[0],
      body({
        event: 'approval.request',
        run_id: RUN_ID,
        command: 'python manage.py migrate',
        description: 'Run a database migration',
        choices: ['once', 'session', 'always', 'deny'],
        allow_permanent: false,
      }),
    ]);
    expect(waiting?.status).toBe('waiting_for_approval');
    expect(waiting?.approval).toEqual({
      command: 'python manage.py migrate',
      description: 'Run a database migration',
      choices: ['once', 'deny'],
      allowPermanent: false,
    });

    const resumed = reduceRunEvent(waiting, body({ event: 'approval.responded', run_id: RUN_ID, choice: 'once', resolved: 1 }));
    expect(resumed?.status).toBe('running');
    expect(resumed?.approval).toBeUndefined();
  });

  it('shows whether an approved staged write was actually saved', () => {
    const run = fold([
      stream[0],
      body({ event: 'tool.started', run_id: RUN_ID, tool: 'skill_manage' }),
      body({
        event: 'write.applied',
        run_id: RUN_ID,
        tool: 'skill_manage',
        subsystem: 'skills',
        pending_id: 'pending-1',
      }),
      body({ event: 'tool.completed', run_id: RUN_ID, tool: 'skill_manage', error: false }),
    ]);
    expect(run?.steps[0].progress).toBe('Skill write saved');
    expect(run?.steps[0].status).toBe('completed');
  });

  it('identifies memory write approvals for persistent allow choices', () => {
    const waiting = fold([
      stream[0],
      body({
        event: 'approval.request',
        run_id: RUN_ID,
        description: 'Memory write requires approval',
        command: 'Save to memory: preferred timezone',
        choices: ['once', 'always', 'deny'],
        allow_permanent: true,
      }),
    ]);

    expect(waiting?.approval?.subsystem).toBe('memory');
  });

  it('completes the run with output and token usage', () => {
    const run = fold(stream);
    expect(run?.status).toBe('completed');
    expect(run?.assistantContent).toBe(OUTPUT);
    expect(run?.usage).toEqual({ totalTokens: 30090, inputTokens: 29999, outputTokens: 91 });
    // run duration derived from run.started/run.completed timestamps
    expect(run?.startedAt).toBeCloseTo(1783992910.5, 1);
    expect(run?.endedAt).toBeCloseTo(1783992914.06, 1);
  });
});

describe('reduceRunEvent (multi-phase run: interleaved tools/text/reasoning)', () => {
  const RID = 'run_multi';
  const b = (data: Record<string, unknown>): SSEEvent => ({ event: 'message', data: { ...data, run_id: RID } });

  // reasoning A -> provisional text A -> tool -> reasoning B -> final text B
  const multi: SSEEvent[] = [
    { event: 'run.started', data: { run_id: RID, timestamp: 1 } },
    b({ event: 'reasoning.delta', delta: 'Investigate before using the terminal.' }),
    b({ event: 'message.delta', delta: 'Phase one answer. ' }),
    b({ event: 'tool.started', tool: 'terminal', preview: 'ls -la', timestamp: 2 }),
    b({ event: 'tool.completed', tool: 'terminal', duration: 0.02, error: false, timestamp: 2.02 }),
    b({ event: 'reasoning.delta', delta: 'Use the tool result for the final answer.' }),
    b({ event: 'message.delta', delta: 'Phase two answer.' }),
    b({ event: 'run.completed', output: 'Phase two answer.', timestamp: 3, usage: { total_tokens: 10 } }),
  ];

  const fold2 = (events: SSEEvent[]) => events.reduce<ChatRun | null>((run, e) => reduceRunEvent(run, e), null);

  it('keeps intermediate tool-call text out of the final answer', () => {
    const run = fold2(multi);
    expect(run?.assistantContent).toBe('Phase two answer.');
  });

  it('moves intermediate tool-call text into the ordered work log', () => {
    const run = fold2(multi);
    expect(run?.reasoning).toEqual([
      'Investigate before using the terminal.',
      'Phase one answer.',
      'Use the tool result for the final answer.',
    ]);
    expect(run?.timeline).toEqual([
      { kind: 'reasoning', text: 'Investigate before using the terminal.' },
      { kind: 'reasoning', text: 'Phase one answer.' },
      { kind: 'tools', stepIds: [`${RID}-0-terminal`] },
      { kind: 'reasoning', text: 'Use the tool result for the final answer.' },
    ]);
    expect(run?.reasoningStreaming).toBe(false);
  });

  it('records the interleaved tool step', () => {
    const run = fold2(multi);
    expect(run?.steps).toHaveLength(1);
    expect(run?.steps[0].toolName).toBe('terminal');
    expect(run?.steps[0].status).toBe('completed');
  });
});

describe('stepLabel & previewCode (long tool previews)', () => {
  const script = "python - <<'PY'\nfrom openpyxl import load_workbook\nprint('a very long script line that keeps going and going and going and going')\nPY";
  const step = (toolName: string, preview: string): ChatRunStep => ({
    id: 's', toolName, preview, status: 'completed',
  });

  it('truncates a huge terminal preview to a short label', () => {
    const label = stepLabel(step('terminal', script));
    expect(label.startsWith('Ran ')).toBe(true);
    expect(label.length).toBeLessThanOrEqual(80);
    expect(label).not.toContain('\n');
  });

  it('exposes the full multiline command as code', () => {
    expect(previewCode(step('terminal', script))).toBe(script);
    expect(previewCode(step('read_file', 'report.md'))).toBe('');
  });

  it('labels new tool types clearly', () => {
    expect(stepLabel(step('skill_view', 'pdf-workflow'))).toBe('Viewed skill pdf-workflow');
    expect(stepLabel(step('patch', 'hpg/scripts/build.py'))).toBe('Edited build.py');
    expect(stepLabel(step('todo', 'planning 4 task(s)'))).toBe('Planning 4 task(s)');
    expect(stepLabel(step('search_files', '*.py'))).toBe('Searched *.py');
  });

  it('keeps short previews intact', () => {
    expect(shortPreview('ls -la')).toBe('ls -la');
  });
});

describe('formatRunDuration', () => {
  const timedRun = (endedAt: number): ChatRun => ({
    id: 'timed',
    status: 'completed',
    startedAt: 0,
    endedAt,
    steps: [],
    assistantContent: '',
  });

  it('adds compact non-zero units as duration crosses their boundaries', () => {
    expect(formatRunDuration(timedRun(15))).toBe('15s');
    expect(formatRunDuration(timedRun(75))).toBe('1m 15s');
    expect(formatRunDuration(timedRun(3_600))).toBe('1h');
    expect(formatRunDuration(timedRun(90_061))).toBe('1d 1h 1m 1s');
  });

  it('uses the supplied current time for an active run', () => {
    expect(formatRunDuration({ ...timedRun(0), endedAt: undefined, status: 'running' }, 125)).toBe('2m 5s');
  });
});

describe('historicalRuns', () => {
  it('restores a persisted stopped turn as cancelled', () => {
    const runs = historicalRuns([
      { id: 1, role: 'user', content: 'Run the workers', timestamp: 10 },
      {
        id: 2,
        role: 'assistant',
        content: '',
        toolCalls: JSON.stringify([{ id: 'call-1', function: { name: 'delegate_task', arguments: '{}' } }]),
        finishReason: 'tool_calls',
        timestamp: 11,
      },
      { id: 3, role: 'assistant', content: 'Response stopped by user.', finishReason: 'cancelled', timestamp: 12 },
    ]);

    expect(runs).toHaveLength(1);
    expect(runs[0].status).toBe('cancelled');
    expect(runs[0].endedAt).toBe(12);
  });

  it('restores the completed tool-step total from persisted session messages', () => {
    const runs = historicalRuns([
      { id: 1, role: 'user', content: 'Inspect the project', timestamp: 10 },
      {
        id: 2,
        role: 'assistant',
        content: '',
        toolCalls: JSON.stringify([{
          id: 'call-1',
          function: { name: 'terminal', arguments: JSON.stringify({ command: 'npm test' }) },
        }]),
        finishReason: 'tool_calls',
        timestamp: 11,
      },
      {
        id: 3,
        role: 'tool',
        content: 'Tests passed',
        toolName: 'terminal',
        toolCallId: 'call-1',
        timestamp: 12,
      },
      { id: 4, role: 'assistant', content: 'The tests pass.', finishReason: 'stop', timestamp: 13 },
    ]);

    expect(runs).toHaveLength(1);
    expect(runs[0].status).toBe('completed');
    expect(runs[0].steps).toHaveLength(1);
    expect(runs[0].steps[0]).toMatchObject({ toolName: 'terminal', status: 'completed' });
  });

  it('restores the latest todo snapshot from paired persisted tool messages', () => {
    const firstTodoResult = JSON.stringify({
      todos: [
        { id: 'plan', content: 'Plan the work', status: 'in_progress' },
        { id: 'build', content: 'Build the feature', status: 'pending' },
      ],
    });
    const todoResult = JSON.stringify({
      todos: [
        { id: 'plan', content: 'Plan the work', status: 'completed' },
        { id: 'build', content: 'Build the feature', status: 'in_progress' },
      ],
    });
    const runs = historicalRuns([
      { id: 1, role: 'user', content: 'Build it', timestamp: 10 },
      {
        id: 2,
        role: 'assistant',
        content: '',
        toolCalls: JSON.stringify([{ id: 'todo-1', function: { name: 'todo', arguments: '{}' } }]),
        finishReason: 'tool_calls',
        timestamp: 11,
      },
      { id: 3, role: 'tool', content: firstTodoResult, toolName: 'todo', toolCallId: 'todo-1', timestamp: 12 },
      {
        id: 4,
        role: 'assistant',
        content: 'Planning is complete; starting implementation.',
        toolCalls: JSON.stringify([{ id: 'todo-2', function: { name: 'todo', arguments: '{}' } }]),
        finishReason: 'tool_calls',
        timestamp: 13,
      },
      { id: 5, role: 'tool', content: todoResult, toolName: 'todo', toolCallId: 'todo-2', timestamp: 14 },
      { id: 6, role: 'assistant', content: 'Still working.', finishReason: 'stop', timestamp: 15 },
    ]);

    expect(runs[0].todos).toEqual([
      { id: 'plan', content: 'Plan the work', status: 'completed' },
      { id: 'build', content: 'Build the feature', status: 'in_progress' },
    ]);
    expect(runs[0].timeline?.map((item) => item.kind)).toEqual(['tools', 'todos', 'reasoning', 'tools', 'todos']);
    expect(runs[0].timeline?.filter((item) => item.kind === 'todos')).toHaveLength(2);
  });
});
