import { describe, expect, it, vi } from 'vitest';
import type { SSEEvent } from '../api/stream';
import { streamErrorMessage, streamStore } from './streamStore';

const mocks = vi.hoisted(() => ({
  startRun: vi.fn(async () => ({ id: 'run-test' })),
  watchRun: vi.fn(),
  run: vi.fn(async () => ({ status: 'completed' })),
  activeRun: vi.fn(async () => null),
}));

vi.mock('../api/conversations', () => ({
  conversationsApi: {
    startRun: mocks.startRun,
    watchRun: mocks.watchRun,
    run: mocks.run,
    activeRun: mocks.activeRun,
  },
}));

describe('streamErrorMessage', () => {
  it('extracts a nested provider message instead of rendering an object', () => {
    expect(streamErrorMessage({ error: { message: 'Invalid API key' } })).toContain('Invalid API key');
  });

  it('turns quota failures into an actionable message', () => {
    expect(streamErrorMessage({ error: { code: 'insufficient_quota' } })).toContain('out of quota');
  });

  it('turns expired subscriptions into an actionable message', () => {
    expect(streamErrorMessage({ detail: 'Subscription has expired' })).toContain('subscription or license has expired');
  });

  it('turns provider authentication failures into actionable guidance', () => {
    expect(streamErrorMessage({ error: { message: 'Invalid API key' } })).toContain('Reconnect the provider account');
    expect(streamErrorMessage({ status: 401, detail: 'Unauthorized' })).toContain('choose another model');
  });

  it('turns provider timeouts into actionable retry guidance', () => {
    expect(streamErrorMessage({ error: 'Gateway timeout (504)' })).toContain('Retry');
    expect(streamErrorMessage({ detail: 'Deadline exceeded' })).toContain('timed out');
  });

  it('forwards the persisted title from the existing completion stream', async () => {
    mocks.watchRun.mockImplementationOnce(async (
      _agentId: string,
      _conversationId: string,
      _runId: string,
      onEvent: (event: SSEEvent) => void,
    ) => {
      onEvent({ event: 'message', data: { event: 'run.completed', output: 'Done', conversation_title: 'Quarterly risk review' } });
    });
    const completion = new Promise((resolve) => {
      const unsubscribe = streamStore.onComplete((event) => {
        if (event.conversationId !== 'session-title') return;
        unsubscribe();
        resolve(event);
      });
    });

    streamStore.send('agent-title', 'session-title', 'Review quarterly risk');

    await expect(completion).resolves.toMatchObject({
      status: 'done',
      conversationTitle: 'Quarterly risk review',
    });
    expect(mocks.watchRun).toHaveBeenCalledTimes(1);
  });

  it('removes provisional tool-call text and streams only the final answer', async () => {
    mocks.watchRun.mockImplementationOnce(async (
      _agentId: string,
      _conversationId: string,
      _runId: string,
      onEvent: (event: SSEEvent) => void,
    ) => {
      const frame = (data: Record<string, unknown>): SSEEvent => ({ event: 'message', data });
      onEvent({ event: 'run.started', data: { run_id: 'run-phases', timestamp: 1 } });
      onEvent(frame({ event: 'reasoning.delta', delta: 'Search for a reliable source.' }));
      onEvent(frame({ event: 'message.delta', delta: 'DuckDuckGo showed a challenge page.' }));
      onEvent(frame({ event: 'tool.started', tool: 'browser', timestamp: 2 }));
      onEvent(frame({ event: 'tool.completed', tool: 'browser', timestamp: 3 }));
      onEvent(frame({ event: 'reasoning.delta', delta: 'The second source has the result.' }));
      onEvent(frame({ event: 'message.delta', delta: 'Here is the verified result.' }));
      onEvent(frame({ event: 'run.completed', output: 'Here is the verified result.', timestamp: 4 }));
    });
    const completion = new Promise<void>((resolve) => {
      const unsubscribe = streamStore.onComplete((event) => {
        if (event.conversationId !== 'session-phases') return;
        unsubscribe();
        resolve();
      });
    });

    streamStore.send('agent-phases', 'session-phases', 'Find the result');
    await completion;

    const snapshot = streamStore.getSnapshot('agent-phases::session-phases');
    const assistant = snapshot.localMessages.find((message) => message.role === 'assistant');
    expect(assistant?.content).toBe('Here is the verified result.');
    expect(assistant?.content).not.toContain('DuckDuckGo');
    expect(snapshot.runs[0].reasoning).toEqual([
      'Search for a reliable source.',
      'DuckDuckGo showed a challenge page.',
      'The second source has the result.',
    ]);
  });

  it('rehydrates an active durable run and replays its events after reload', async () => {
    mocks.activeRun.mockResolvedValueOnce({
      id: 'run-restored',
      status: 'running',
      mode: 'background',
      timeout_seconds: 3600,
      created_at: 10,
      started_at: 11,
    });
    mocks.watchRun.mockImplementationOnce(async (
      _agentId: string,
      _conversationId: string,
      _runId: string,
      onEvent: (event: SSEEvent) => void,
    ) => {
      onEvent({ id: '1', event: 'message', data: {
        event: 'run.started', run_id: 'run-restored', timestamp: 11,
        durable: true, run_mode: 'background', timeout_seconds: 3600, deadline_at: 3611,
      } });
      onEvent({ id: '2', event: 'message', data: {
        event: 'tool.started', run_id: 'run-restored', timestamp: 12, tool: 'pdf_create',
      } });
      onEvent({ id: '3', event: 'message', data: {
        event: 'run.completed', run_id: 'run-restored', timestamp: 13, output: 'PDF ready',
      } });
    });

    await streamStore.resume('agent-restored', 'session-restored');

    const snapshot = streamStore.getSnapshot('agent-restored::session-restored');
    expect(snapshot.status).toBe('done');
    expect(snapshot.runs[0]).toMatchObject({
      id: 'run-restored', status: 'completed', durable: true, runMode: 'background', timeoutSeconds: 3600,
    });
    expect(snapshot.localMessages.find((message) => message.role === 'assistant')?.content).toBe('PDF ready');
    expect(mocks.watchRun).toHaveBeenCalledWith(
      'agent-restored', 'session-restored', 'run-restored', expect.any(Function), expect.any(AbortSignal), 0,
    );
  });
});
