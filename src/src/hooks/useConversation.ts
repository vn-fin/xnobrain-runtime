import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { conversationsApi } from '../api/conversations';
import { historicalRuns } from '../chat/runEvents';
import { streamStore } from '../chat/streamStore';
import type { AsyncStatus, ChatMessage, ChatRun, ConversationUsage, RunApprovalChoice } from '../types';

function abortError(value: unknown) {
  return value instanceof DOMException && value.name === 'AbortError';
}

export function useConversation(agentId: string, conversationId: string) {
  const key = `${agentId}::${conversationId}`;
  const [serverMessages, setServerMessages] = useState<ChatMessage[]>([]);
  const [serverRuns, setServerRuns] = useState<ChatRun[]>([]);
  const [usage, setUsage] = useState<ConversationUsage | null>(null);
  const [usageStatus, setUsageStatus] = useState<AsyncStatus>('idle');
  const [usageError, setUsageError] = useState('');
  const [status, setStatus] = useState<AsyncStatus>('idle');
  const [error, setError] = useState('');
  const [loadSignal, setLoadSignal] = useState<AbortSignal>();
  const loadController = useRef<AbortController>();
  const loadKey = useRef<string | null>(null);
  const generation = useRef(0);

  // Streaming state lives in the shared store so it survives tab switches.
  const session = useSyncExternalStore(
    streamStore.subscribe,
    useCallback(() => streamStore.getSnapshot(key), [key]),
  );

  const requestUsage = useCallback(async (requestGeneration = generation.current) => {
    if (!agentId || !conversationId) return;
    setUsageStatus('loading');
    setUsageError('');
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
  }, [agentId, conversationId]);

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
    streamStore.setActive(agentId, conversationId);
  }, [agentId, conversationId]);

  useEffect(() => {
    if (loadKey.current !== key) {
      loadKey.current = key;
      generation.current += 1;
      void refresh();
    }
    // No stream teardown here: streams intentionally keep running in the
    // background when switching conversations.
  }, [refresh, key]);

  const sendMessage = async (input: string) => {
    const text = input.trim();
    if (!text || !agentId || !conversationId) return;
    if (text.toLowerCase() === '/usage') {
      await requestUsage();
      return;
    }
    streamStore.send(agentId, conversationId, text);
  };

  const stopStream = async () => {
    await streamStore.stop(agentId, conversationId);
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
    () => (session.localMessages.length ? [...serverMessages, ...session.localMessages] : serverMessages),
    [serverMessages, session.localMessages],
  );
  const runs = useMemo(
    () => (session.runs.length ? [...serverRuns, ...session.runs] : serverRuns),
    [serverRuns, session.runs],
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
    refresh, requestUsage, sendMessage, stopStream, resolveRunApproval,
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
