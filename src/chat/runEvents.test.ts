import { describe, expect, it } from 'vitest';
import type { SSEEvent } from '../api/stream';
import { formatStepDuration, previewCode, reduceRunEvent, shortPreview, stepLabel, toolKind } from './runEvents';
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

  // text A -> reasoning A -> tool -> text B -> reasoning B -> run.completed(output=B)
  const multi: SSEEvent[] = [
    { event: 'run.started', data: { run_id: RID, timestamp: 1 } },
    b({ event: 'message.delta', delta: 'Phase one answer. ' }),
    b({ event: 'reasoning.available', text: 'Phase one answer.' }),
    b({ event: 'tool.started', tool: 'terminal', preview: 'ls -la', timestamp: 2 }),
    b({ event: 'tool.completed', tool: 'terminal', duration: 0.02, error: false, timestamp: 2.02 }),
    b({ event: 'message.delta', delta: 'Phase two answer.' }),
    b({ event: 'reasoning.available', text: 'A private thought not shown to the user.' }),
    b({ event: 'run.completed', output: 'Phase two answer.', timestamp: 3, usage: { total_tokens: 10 } }),
  ];

  const fold2 = (events: SSEEvent[]) => events.reduce<ChatRun | null>((run, e) => reduceRunEvent(run, e), null);

  it('preserves the full streamed answer, not just the final output segment', () => {
    const run = fold2(multi);
    expect(run?.assistantContent).toBe('Phase one answer. Phase two answer.');
  });

  it('accumulates every reasoning segment in order', () => {
    const run = fold2(multi);
    expect(run?.reasoning).toEqual(['Phase one answer.', 'A private thought not shown to the user.']);
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
