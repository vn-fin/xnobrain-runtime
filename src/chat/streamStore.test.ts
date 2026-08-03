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
    mocks.stream.mockImplementationOnce(async (_agentId: string, _conversationId: string, _text: string, onEvent: (event: SSEEvent) => void) => {
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
});
