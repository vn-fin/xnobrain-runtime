import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatMessage, ChatRun, ConversationUsage } from '../types';
import { mergeConversationMessages, mergeConversationRuns, useConversation } from './useConversation';

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
    resume: vi.fn(async () => undefined),
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

describe('conversation stream reconciliation', () => {
  it('does not render a prompt twice after its server copy arrives', () => {
    const server: ChatMessage[] = [
      { id: 10, role: 'user', content: 'Check the latest news', timestamp: 100.2 },
    ];
    const local: ChatMessage[] = [
      { id: 'local-user', role: 'user', content: 'Check the latest news', timestamp: 100 },
      { id: 'local-assistant', role: 'assistant', content: '', streaming: true, timestamp: 100 },
    ];

    expect(mergeConversationMessages(server, local)).toEqual([server[0], local[1]]);
  });

  it('keeps an older identical prompt instead of reconciling it with the new turn', () => {
    const server: ChatMessage[] = [
      { id: 10, role: 'user', content: 'Try again', timestamp: 10 },
    ];
    const local: ChatMessage[] = [
      { id: 'local-user', role: 'user', content: 'Try again', timestamp: 100 },
    ];

    expect(mergeConversationMessages(server, local)).toHaveLength(2);
  });

  it('uses the live current run instead of its partially persisted history run', () => {
    const makeRun = (id: string, startedAt: number): ChatRun => ({
      id, startedAt, status: 'completed', steps: [], assistantContent: '',
    });
    const oldRun = makeRun('history-old', 50);
    const persistedCurrent = makeRun('history-current', 101);
    const liveCurrent = { ...makeRun('run-current', 100), status: 'running' as const };
    const local: ChatMessage[] = [
      { id: 'local-user', role: 'user', content: 'Question', timestamp: 100 },
    ];

    expect(mergeConversationRuns([oldRun, persistedCurrent], [liveCurrent], local)).toEqual([
      oldRun,
      liveCurrent,
    ]);
  });
});
