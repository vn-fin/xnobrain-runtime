import { describe, expect, it, vi } from 'vitest';
import type { SSEEvent } from '../api/stream';
import { streamErrorMessage, streamStore } from './streamStore';

const mocks = vi.hoisted(() => ({ stream: vi.fn() }));

vi.mock('../api/conversations', () => ({
  conversationsApi: {
    stream: mocks.stream,
  },
}));

describe('streamErrorMessage', () => {
  it('extracts a nested provider message instead of rendering an object', () => {
    expect(streamErrorMessage({ error: { message: 'Invalid API key' } })).toBe('Invalid API key');
  });

  it('turns quota failures into an actionable message', () => {
    expect(streamErrorMessage({ error: { code: 'insufficient_quota' } })).toContain('out of quota');
  });

  it('turns expired subscriptions into an actionable message', () => {
    expect(streamErrorMessage({ detail: 'Subscription has expired' })).toContain('subscription or license has expired');
  });

  it('forwards the persisted title from the existing completion stream', async () => {
    mocks.stream.mockImplementationOnce(async (
      _agentId: string,
      _conversationId: string,
      _text: string,
      _model: string,
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
    expect(mocks.stream).toHaveBeenCalledTimes(1);
  });

  it('removes provisional tool-call text and streams only the final answer', async () => {
    mocks.stream.mockImplementationOnce(async (
      _agentId: string,
      _conversationId: string,
      _text: string,
      _model: string,
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
});
