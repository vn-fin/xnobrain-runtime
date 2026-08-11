import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { conversationsApi, type ConversationCompactResult } from '../api/conversations';
import { historicalRuns } from '../chat/runEvents';
import { streamStore } from '../chat/streamStore';
import type { AsyncStatus, ChatMessage, ChatRun, ConversationUsage, RunApprovalChoice } from '../types';

function abortError(value: unknown) {
  return value instanceof DOMException && value.name === 'AbortError';
}

function sameMessage(left: ChatMessage, right: ChatMessage) {
  if (left.role !== right.role || left.content.trim() !== right.content.trim()) return false;
  if (left.timestamp === undefined || right.timestamp === undefined) return true;
  return Math.abs(left.timestamp - right.timestamp) <= 60;
}

/** Keep optimistic stream messages only until their persisted copies arrive. */
export function mergeConversationMessages(server: ChatMessage[], local: ChatMessage[]) {
  if (!local.length) return server;
  const available = server.map((_, index) => index);
  const unmatched = local.filter((message) => {
    // An empty streaming assistant is only a placeholder, never persisted content.
    if (message.streaming && !message.content.trim()) return true;
    let matchAt = -1;
    for (let index = available.length - 1; index >= 0; index -= 1) {
      if (sameMessage(server[available[index]], message)) {
        matchAt = index;
        break;
      }
    }
    if (matchAt < 0) return true;
    available.splice(matchAt, 1);
    return false;
  });
  return unmatched.length ? [...server, ...unmatched] : server;
}

/** Replace a partially persisted current run with its live, updating version. */
export function mergeConversationRuns(server: ChatRun[], local: ChatRun[], localMessages: ChatMessage[]) {
  if (!local.length) return server;
  const sentAt = localMessages.find((message) => message.role === 'user')?.timestamp;
  const historical = sentAt === undefined
    ? server.filter((run) => !local.some((live) => live.id === run.id))
    : server.filter((run) => run.startedAt === undefined || run.startedAt < sentAt - 1);
  return [...historical, ...local];
}

export function useConversation(agentId: string, conversationId: string, model = '', markActive = true) {
  const key = `${agentId}::${conversationId}`;
  const [serverMessages, setServerMessages] = useState<ChatMessage[]>([]);
  const [serverRuns, setServerRuns] = useState<ChatRun[]>([]);
  const [usage, setUsage] = useState<ConversationUsage | null>(null);
  const [usageStatus, setUsageStatus] = useState<AsyncStatus>('idle');
  const [usageError, setUsageError] = useState('');
  const [status, setStatus] = useState<AsyncStatus>('idle');
  const [error, setError] = useState('');
  const [compacting, setCompacting] = useState(false);
  const [compactError, setCompactError] = useState('');
  const [compactResult, setCompactResult] = useState<ConversationCompactResult | null>(null);
  const [loadSignal, setLoadSignal] = useState<AbortSignal>();
  const loadController = useRef<AbortController>();
  const loadKey = useRef<string | null>(null);
  const usageRequests = useRef(new Map<string, Promise<void>>());
  const generation = useRef(0);

  // Streaming state lives in the shared store so it survives tab switches.
  const session = useSyncExternalStore(
    streamStore.subscribe,
    useCallback(() => streamStore.getSnapshot(key), [key]),
  );

  const requestUsage = useCallback(async (requestGeneration = generation.current) => {
    if (!agentId || !conversationId) return;
    const requestKey = `${key}::${requestGeneration}`;
    const pendingRequest = usageRequests.current.get(requestKey);
    if (pendingRequest) return pendingRequest;
    setUsageStatus('loading');
    setUsageError('');
    const request = (async () => {
      try {
        const nextUsage = await conversationsApi.usage(agentId, conversationId);
        if (generation.current !== requestGeneration) return;
        setUsage(nextUsage);
        setUsageStatus('ready');
      } catch (value) {
        if (generation.current !== requestGeneration) return;
        setUsageError(value instanceof Error ? value.message : 'Could not load usage.');
        setUsageStatus('error');
      }
    })().finally(() => {
      if (usageRequests.current.get(requestKey) === request) {
        usageRequests.current.delete(requestKey);
      }
    });
    usageRequests.current.set(requestKey, request);
    return request;
  }, [agentId, conversationId, key]);

  const refresh = useCallback(async () => {
    loadController.current?.abort();
    if (!agentId || !conversationId) {
      setServerMessages([]);
      setServerRuns([]);
      setUsage(null);
      setStatus('ready');
      return;
    }
    const controller = new AbortController();
    loadController.current = controller;
    setLoadSignal(controller.signal);
    setStatus('loading');
    setError('');
    setUsage(null);
    setUsageStatus('idle');
    setUsageError('');
    try {
      const nextMessages = await conversationsApi.messages(agentId, conversationId, controller.signal);
      if (controller.signal.aborted) return;
      setServerMessages(nextMessages);
      setServerRuns(historicalRuns(nextMessages));
      setStatus('ready');
      // Drop any finished session whose result is now in the server history.
      streamStore.clearIfIdle(agentId, conversationId);
    } catch (value) {
      if (abortError(value) || controller.signal.aborted) return;
      setError(value instanceof Error ? value.message : 'Could not load conversation.');
      setStatus('error');
    }
  }, [agentId, conversationId]);

  // Mark the active conversation so the store knows which completions are
  // "background" (and should raise a notification).
  useEffect(() => {
    if (markActive) streamStore.setActive(agentId, conversationId);
  }, [agentId, conversationId, markActive]);

  useEffect(() => {
    if (loadKey.current !== key) {
      loadKey.current = key;
      generation.current += 1;
      setCompacting(false);
      setCompactError('');
      setCompactResult(null);
      void refresh();
      void requestUsage(generation.current);
    }
    // No stream teardown here: streams intentionally keep running in the
    // background when switching conversations.
  }, [refresh, requestUsage, key]);

  useEffect(() => {
    const unsubscribe = streamStore.onComplete((event) => {
      if (event.agentId === agentId && event.conversationId === conversationId) {
        void requestUsage();
      }
    });
    return () => { unsubscribe(); };
  }, [agentId, conversationId, requestUsage]);

  const sendMessage = async (input: string) => {
    const text = input.trim();
    if (!text || !agentId || !conversationId) return;
    if (text.toLowerCase() === '/usage') {
      await requestUsage();
      return;
    }
    streamStore.send(agentId, conversationId, text, model);
  };

  const stopStream = async () => {
    await streamStore.stop(agentId, conversationId);
  };

  const compactContext = async (focus?: string) => {
    if (!agentId || !conversationId || session.runActive || compacting) return;
    setCompacting(true);
    setCompactError('');
    setCompactResult(null);
    try {
      const result = await conversationsApi.compact(agentId, conversationId, focus);
      setCompactResult(result);
      await refresh();
      await requestUsage();
      return result;
    } catch (value) {
      setCompactError(value instanceof Error ? value.message : 'Could not compact session context.');
      throw value;
    } finally {
      setCompacting(false);
    }
  };

  const resolveRunApproval = async (runId: string, choice: RunApprovalChoice) => {
    if (!agentId || !conversationId) return;
    try {
      await streamStore.resolveApproval(agentId, conversationId, runId, choice);
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not resolve approval.');
      throw value;
    }
  };

  const messages = useMemo(
    () => mergeConversationMessages(serverMessages, session.localMessages),
    [serverMessages, session.localMessages],
  );
  const runs = useMemo(
    () => mergeConversationRuns(serverRuns, session.runs, session.localMessages),
    [serverRuns, session.runs, session.localMessages],
  );

  return {
    messages,
    queuedMessages: session.queue,
    usage, usageStatus, usageError, status,
    error: error || session.error,
    streaming: session.streaming,
    streamEvents: session.events,
    runs,
    loadSignal,
    canStop: session.runActive,
    compacting, compactError, compactResult,
    refresh, requestUsage, sendMessage, stopStream, compactContext, resolveRunApproval,
    removeQueuedMessage: (id: string) => streamStore.removeQueued(agentId, conversationId, id),
    editQueuedMessage: (id: string, content: string) => streamStore.editQueued(agentId, conversationId, id, content),
    moveQueuedMessage: (id: string, direction: 'up' | 'down') => streamStore.moveQueued(agentId, conversationId, id, direction),
  };
}

/** Subscribe to the set of conversation keys (`agentId::conversationId`) that
 *  currently have an active stream — used to badge conversation tabs. */
export function useStreamingConversations(): string[] {
  return useSyncExternalStore(streamStore.subscribe, streamStore.getStreamingKeys);
}
