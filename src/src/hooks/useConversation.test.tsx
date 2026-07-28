import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ConversationUsage } from '../types';
import { useConversation } from './useConversation';

const mocks = vi.hoisted(() => ({
  messages: vi.fn(),
  usage: vi.fn(),
  complete: undefined as undefined | ((event: {
    agentId: string;
    conversationId: string;
    status: 'done';
    active: boolean;
    key: string;
  }) => void),
  snapshot: {
    streaming: false,
    runActive: false,
    runId: null,
    runs: [],
    localMessages: [],
    events: [],
    queue: [],
    error: '',
    status: 'idle',
  },
}));

vi.mock('../api/conversations', () => ({
  conversationsApi: {
    messages: mocks.messages,
    usage: mocks.usage,
  },
}));

vi.mock('../chat/runEvents', () => ({ historicalRuns: () => [] }));

vi.mock('../chat/streamStore', () => ({
  streamStore: {
    subscribe: () => () => undefined,
    getSnapshot: () => mocks.snapshot,
    setActive: vi.fn(),
    onComplete: (listener: NonNullable<typeof mocks.complete>) => {
      mocks.complete = listener;
      return () => { mocks.complete = undefined; };
    },
    clearIfIdle: vi.fn(),
    send: vi.fn(),
    stop: vi.fn(),
    resolveApproval: vi.fn(),
    removeQueued: vi.fn(),
    editQueued: vi.fn(),
    moveQueued: vi.fn(),
  },
}));

function usage(conversationId: string): ConversationUsage {
  return {
    conversationId,
    messages: 0,
    apiCalls: 0,
    model: 'cx/gpt-5.6-luna',
    totalTokens: 0,
    contextUsed: 10_000,
    contextLimit: 200_000,
    contextPercent: 5,
    totalCostUsd: 0,
    provider: '',
    plan: '',
    quotaAvailable: false,
    quotaMessage: '',
    limits: [],
  };
}

describe('useConversation usage refresh', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads usage on conversation selection and again when its chat completes', async () => {
    mocks.messages.mockResolvedValue([]);
    mocks.usage.mockImplementation(async (_agentId: string, conversationId: string) => usage(conversationId));

    const { result, rerender } = renderHook(
      ({ conversationId }) => useConversation('agent-one', conversationId),
      { initialProps: { conversationId: 'conversation-one' } },
    );

    await waitFor(() => expect(mocks.usage).toHaveBeenCalledWith('agent-one', 'conversation-one'));
    expect(result.current.usage?.contextPercent).toBe(5);

    await act(async () => {
      mocks.complete?.({
        key: 'agent-one::conversation-one',
        agentId: 'agent-one',
        conversationId: 'conversation-one',
        status: 'done',
        active: true,
      });
    });
    await waitFor(() => expect(mocks.usage).toHaveBeenCalledTimes(2));

    rerender({ conversationId: 'conversation-two' });
    await waitFor(() => expect(mocks.usage).toHaveBeenCalledWith('agent-one', 'conversation-two'));
  });
});
