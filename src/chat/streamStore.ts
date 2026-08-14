import { conversationsApi } from '../api/conversations';
import type { SSEEvent } from '../api/stream';
import { reduceRunEvent } from './runEvents';
import type { ChatMessage, ChatRun, ComposerFeature, RunApprovalChoice } from '../types';
import { randomId } from '../utils/id';

// A background-capable chat stream store. Streaming sessions are keyed by
// conversation and live outside any React component, so a run keeps going when
// the user switches to another conversation tab. Components subscribe via
// `useSyncExternalStore` and read an immutable snapshot per conversation.

export type QueuedMessage = { id: string; content: string; model: string; feature?: ComposerFeature };
export type SessionStatus = 'idle' | 'streaming' | 'done' | 'error' | 'cancelled';

export type SessionSnapshot = {
  streaming: boolean;
  runActive: boolean;
  runId: string | null;
  runs: ChatRun[];
  localMessages: ChatMessage[];
  events: SSEEvent[];
  queue: QueuedMessage[];
  error: string;
  status: SessionStatus;
};

export type CompletionEvent = {
  key: string;
  agentId: string;
  conversationId: string;
  status: 'done' | 'error' | 'cancelled';
  active: boolean;
  conversationTitle?: string;
};

type Runtime = {
  agentId: string;
  conversationId: string;
  controller?: AbortController;
  cancelled: boolean;
  processing: boolean;
  conversationTitle?: string;
  lastSequence?: number;
  restoring?: boolean;
};

const EMPTY: SessionSnapshot = {
  streaming: false,
  runActive: false,
  runId: null,
  runs: [],
  localMessages: [],
  events: [],
  queue: [],
  error: '',
  status: 'idle',
};

const snapshots = new Map<string, SessionSnapshot>();
const runtimes = new Map<string, Runtime>();
const listeners = new Set<() => void>();
const completionListeners = new Set<(event: CompletionEvent) => void>();
let activeKey: string | null = null;
// Stable snapshot of the keys that currently have an active stream, so
// `useSyncExternalStore` consumers only re-render when the set changes.
let streamingKeys: string[] = [];

const keyOf = (agentId: string, conversationId: string) => `${agentId}::${conversationId}`;

function abortError(value: unknown) {
  return value instanceof DOMException && value.name === 'AbortError';
}

function nestedErrorText(value: unknown, depth = 0): string {
  if (typeof value === 'string') return value.trim();
  if (!value || typeof value !== 'object' || depth > 4) return '';
  const item = value as Record<string, unknown>;
  for (const key of ['message', 'detail', 'error', 'reason', 'code', 'type']) {
    const text = nestedErrorText(item[key], depth + 1);
    if (text) return text;
  }
  return '';
}

/** Turn provider error payloads into a stable, actionable stream message. */
export function streamErrorMessage(value: unknown): string {
  const original = nestedErrorText(value) || 'Chat stream failed.';
  const normalized = original.toLowerCase();
  if (/license|subscription/.test(normalized) && /expir|inactive|invalid/.test(normalized)) {
    return `The LLM provider subscription or license has expired. Renew it or switch provider account. (${original})`;
  }
  if (/insufficient[_ -]?quota|out of quota|quota.*exceed|billing.*limit|payment required|\b402\b/.test(normalized)) {
    return `The LLM provider account is out of quota. Add credits or switch provider account. (${original})`;
  }
  if (/rate[_ -]?limit|too many requests|\b429\b/.test(normalized)) {
    return `The LLM provider rate limit was reached. Wait and retry, or switch provider account. (${original})`;
  }
  return original;
}

function streamText(event: SSEEvent): string {
  const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : null;
  const bodyType = data && typeof data.event === 'string' ? data.event : '';
  if (bodyType) {
    if (bodyType === 'message.delta' || bodyType === 'assistant.delta') {
      return typeof data?.delta === 'string' ? data.delta : '';
    }
    return '';
  }
  if (!['delta', 'text', 'message', 'content', 'reply', 'assistant.delta'].includes(event.event)) return '';
  if (typeof event.data === 'string') {
    const raw = event.data.trim();
    return raw.startsWith('{') || raw.startsWith('[') ? '' : event.data;
  }
  if (!data) return '';
  for (const key of ['text', 'delta', 'content', 'output']) {
    if (typeof data[key] === 'string') return data[key] as string;
  }
  return '';
}

function emit() {
  listeners.forEach((listener) => listener());
}

function recomputeStreaming() {
  const next: string[] = [];
  snapshots.forEach((snap, k) => { if (snap.streaming) next.push(k); });
  next.sort();
  const changed = next.length !== streamingKeys.length || next.some((k, i) => k !== streamingKeys[i]);
  if (changed) streamingKeys = next;
}

function getSnapshot(key: string): SessionSnapshot {
  return snapshots.get(key) ?? EMPTY;
}

function patch(key: string, changes: Partial<SessionSnapshot>) {
  const current = snapshots.get(key) ?? EMPTY;
  snapshots.set(key, { ...current, ...changes });
  recomputeStreaming();
  emit();
}

function runtimeFor(key: string, agentId: string, conversationId: string): Runtime {
  let runtime = runtimes.get(key);
  if (!runtime) {
    runtime = { agentId, conversationId, cancelled: false, processing: false };
    runtimes.set(key, runtime);
  }
  runtime.agentId = agentId;
  runtime.conversationId = conversationId;
  return runtime;
}

function updateAssistant(key: string, assistantId: string, map: (message: ChatMessage) => ChatMessage) {
  const snap = getSnapshot(key);
  patch(key, { localMessages: snap.localMessages.map((message) => (
    message.id === assistantId ? map(message) : message
  )) });
}

function runEventHandler(key: string, runtime: Runtime, assistantId: string) {
  return (event: SSEEvent) => {
    if (event.data === '[DONE]') return;
    const sequence = Number(event.id);
    if (Number.isFinite(sequence)) runtime.lastSequence = Math.max(runtime.lastSequence ?? 0, sequence);
    const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};
    const type = typeof data.event === 'string' && data.event ? data.event : event.event;

    if (type === 'run.started' && typeof data.run_id === 'string') {
      patch(key, { runId: data.run_id as string, runActive: true });
    }
    const snap = getSnapshot(key);
    patch(key, { events: [...snap.events, event] });

    const runsSnap = getSnapshot(key).runs;
    if (type === 'run.started') {
      const next = reduceRunEvent(null, event);
      if (next && !runsSnap.some((run) => run.id === next.id)) {
        patch(key, { runs: [...runsSnap, { ...next, insertBeforeMessageId: assistantId }] });
      }
    } else if (runsSnap.length > 0) {
      const index = runsSnap.findIndex((run) => run.id === (typeof data.run_id === 'string' ? data.run_id : getSnapshot(key).runId));
      const target = index >= 0 ? index : runsSnap.length - 1;
      const next = reduceRunEvent(runsSnap[target], event);
      if (next) patch(key, { runs: runsSnap.map((run, runIndex) => (runIndex === target ? next : run)) });
    }

    if (type === 'error' || type === 'run.failed') {
      patch(key, { error: streamErrorMessage(event.data) });
      return;
    }
    if (type === 'run.cancelled') {
      runtime.cancelled = true;
      return;
    }
    if (type === 'assistant.completed' || type === 'message.completed') {
      if (typeof data.content === 'string') updateAssistant(key, assistantId, (message) => ({ ...message, content: data.content as string }));
      return;
    }
    if (type === 'tool.started') {
      updateAssistant(key, assistantId, (message) => ({ ...message, content: '' }));
      return;
    }
    if (type === 'run.completed') {
      if (typeof data.conversation_title === 'string' && data.conversation_title.trim()) {
        runtime.conversationTitle = data.conversation_title.trim();
      }
      if (typeof data.output === 'string' && data.output) {
        updateAssistant(key, assistantId, (message) => ({ ...message, content: data.output as string }));
      }
      return;
    }
    const delta = streamText(event);
    if (delta) updateAssistant(key, assistantId, (message) => ({ ...message, content: message.content + delta }));
  };
}

async function followRun(key: string, runtime: Runtime, runId: string, assistantId: string, controller: AbortController) {
  const handleEvent = runEventHandler(key, runtime, assistantId);
  for (;;) {
    try {
      await conversationsApi.watchRun(
        runtime.agentId,
        runtime.conversationId,
        runId,
        handleEvent,
        controller.signal,
        runtime.lastSequence ?? 0,
      );
      if (controller.signal.aborted) throw new DOMException('The operation was aborted.', 'AbortError');
      const record = await conversationsApi.run(runtime.agentId, runtime.conversationId, runId);
      if (['completed', 'failed', 'timed_out', 'cancelled'].includes(record.status)) return;
    } catch (value) {
      if (abortError(value) || controller.signal.aborted) throw value;
      // The backend owns execution. Retry a dropped observer connection from
      // the last persisted event instead of failing or cancelling the run.
    }
    await new Promise((resolve) => window.setTimeout(resolve, 1_000));
  }
}

async function execute(key: string, text: string, model: string, feature?: ComposerFeature) {
  const runtime = runtimes.get(key);
  if (!runtime) return;
  const { agentId, conversationId } = runtime;
  const controller = new AbortController();
  runtime.controller = controller;

  const userId = `local-user-${randomId()}`;
  const assistantId = `local-assistant-${randomId()}`;
  const sentAt = Date.now() / 1000;
  const base = getSnapshot(key);
  patch(key, {
    status: 'streaming',
    error: '',
    runActive: false,
    events: [],
    localMessages: [
      ...base.localMessages,
      { id: userId, role: 'user', content: text, timestamp: sentAt },
      { id: assistantId, role: 'assistant', content: '', streaming: true, timestamp: sentAt },
    ],
  });

  try {
    runtime.lastSequence = 0;
    const record = await conversationsApi.startRun(agentId, conversationId, text, model, 'auto', controller.signal, feature);
    if (runtime.cancelled || controller.signal.aborted) {
      await conversationsApi.stopRun(agentId, conversationId, record.id).catch(() => undefined);
      throw new DOMException('The operation was aborted.', 'AbortError');
    }
    patch(key, { runId: record.id, runActive: true });
    await followRun(key, runtime, record.id, assistantId, controller);
    updateAssistant(key, assistantId, (message) => ({ ...message, streaming: false }));
  } catch (value) {
    if (!abortError(value)) patch(key, { error: streamErrorMessage(value instanceof Error ? value.message : value) });
    updateAssistant(key, assistantId, (message) => ({ ...message, streaming: false }));
  } finally {
    if (runtime.controller === controller) runtime.controller = undefined;
    patch(key, { runActive: false, runId: null });
  }
}

async function resume(key: string, agentId: string, conversationId: string) {
  const runtime = runtimeFor(key, agentId, conversationId);
  if (runtime.processing || runtime.restoring || getSnapshot(key).streaming) return;
  runtime.restoring = true;
  try {
    let record;
    try {
      record = await conversationsApi.activeRun(agentId, conversationId);
    } catch {
      return;
    }
    if (!record) return;
    runtime.processing = true;
    runtime.cancelled = false;
    runtime.lastSequence = 0;
    const controller = new AbortController();
    runtime.controller = controller;
    const assistantId = `local-assistant-${randomId()}`;
    const base = getSnapshot(key);
    patch(key, {
      streaming: true,
      status: 'streaming',
      runActive: true,
      runId: record.id,
      error: '',
      events: [],
      localMessages: [
        ...base.localMessages,
        { id: assistantId, role: 'assistant', content: '', streaming: true, timestamp: record.started_at ?? record.created_at },
      ],
    });
    try {
      await followRun(key, runtime, record.id, assistantId, controller);
      updateAssistant(key, assistantId, (message) => ({ ...message, streaming: false }));
    } catch (value) {
      if (!abortError(value)) patch(key, { error: streamErrorMessage(value instanceof Error ? value.message : value) });
      updateAssistant(key, assistantId, (message) => ({ ...message, streaming: false }));
    } finally {
      if (runtime.controller === controller) runtime.controller = undefined;
      runtime.processing = false;
      const snap = getSnapshot(key);
      const status: 'done' | 'error' | 'cancelled' = runtime.cancelled ? 'cancelled' : snap.error ? 'error' : 'done';
      patch(key, { streaming: false, runActive: false, runId: null, status });
      completionListeners.forEach((listener) => listener({
        key, agentId, conversationId, status, active: activeKey === key, conversationTitle: runtime.conversationTitle,
      }));
    }
  } finally {
    runtime.restoring = false;
  }
}

async function drain(key: string) {
  const runtime = runtimes.get(key);
  if (!runtime || runtime.processing) return;
  runtime.processing = true;
  runtime.cancelled = false;
  runtime.conversationTitle = undefined;
  patch(key, { streaming: true });
  try {
    while (!runtime.cancelled && getSnapshot(key).queue.length > 0) {
      const snap = getSnapshot(key);
      const [next, ...rest] = snap.queue;
      patch(key, { queue: rest });
      if (next) await execute(key, next.content, next.model, next.feature);
    }
  } finally {
    runtime.processing = false;
    const snap = getSnapshot(key);
    const status: 'done' | 'error' | 'cancelled' = runtime.cancelled ? 'cancelled' : snap.error ? 'error' : 'done';
    patch(key, { streaming: false, status });
    const completion: CompletionEvent = {
      key,
      agentId: runtime.agentId,
      conversationId: runtime.conversationId,
      status,
      active: activeKey === key,
      conversationTitle: runtime.conversationTitle,
    };
    completionListeners.forEach((listener) => listener(completion));
  }
}

function markCancelled(key: string, runId: string | null) {
  const endedAt = Date.now() / 1000;
  const snap = getSnapshot(key);
  patch(key, {
    runs: snap.runs.map((run) => {
      if (run.status !== 'running' && run.status !== 'waiting_for_approval') return run;
      if (runId && run.id !== runId) return run;
      return {
        ...run,
        status: 'cancelled',
        endedAt,
        approval: undefined,
        steps: run.steps.map((step) => (step.status === 'running' ? { ...step, status: 'cancelled', endedAt } : step)),
      };
    }),
  });
}

export const streamStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  onComplete(listener: (event: CompletionEvent) => void) {
    completionListeners.add(listener);
    return () => completionListeners.delete(listener);
  },

  getSnapshot,

  getStreamingKeys() {
    return streamingKeys;
  },

  setActive(agentId: string, conversationId: string) {
    activeKey = agentId && conversationId ? keyOf(agentId, conversationId) : null;
  },

  isStreaming(agentId: string, conversationId: string) {
    return getSnapshot(keyOf(agentId, conversationId)).streaming;
  },

  resume(agentId: string, conversationId: string) {
    if (!agentId || !conversationId) return Promise.resolve();
    return resume(keyOf(agentId, conversationId), agentId, conversationId);
  },

  send(agentId: string, conversationId: string, text: string, model = '', feature?: ComposerFeature) {
    const clean = text.trim();
    if (!clean || !agentId || !conversationId) return;
    const key = keyOf(agentId, conversationId);
    runtimeFor(key, agentId, conversationId);
    const snap = getSnapshot(key);
    patch(key, {
      queue: [...snap.queue, {
        id: `queued-${randomId()}`,
        content: clean,
        model: model.trim(),
        feature,
      }],
    });
    void drain(key);
  },

  removeQueued(agentId: string, conversationId: string, id: string) {
    const key = keyOf(agentId, conversationId);
    const snap = getSnapshot(key);
    patch(key, { queue: snap.queue.filter((message) => message.id !== id) });
  },

  editQueued(agentId: string, conversationId: string, id: string, content: string) {
    const key = keyOf(agentId, conversationId);
    const text = content.trim();
    const snap = getSnapshot(key);
    if (!text) {
      patch(key, { queue: snap.queue.filter((message) => message.id !== id) });
      return;
    }
    patch(key, { queue: snap.queue.map((message) => (message.id === id ? { ...message, content: text } : message)) });
  },

  moveQueued(agentId: string, conversationId: string, id: string, direction: 'up' | 'down') {
    const key = keyOf(agentId, conversationId);
    const items = [...getSnapshot(key).queue];
    const index = items.findIndex((message) => message.id === id);
    if (index < 0) return;
    const target = direction === 'up' ? index - 1 : index + 1;
    if (target < 0 || target >= items.length) return;
    [items[index], items[target]] = [items[target], items[index]];
    patch(key, { queue: items });
  },

  async stop(agentId: string, conversationId: string) {
    const key = keyOf(agentId, conversationId);
    const runtime = runtimes.get(key);
    const snap = getSnapshot(key);
    const runId = snap.runId;
    if (runtime) {
      runtime.cancelled = true;
      runtime.controller?.abort();
    }
    markCancelled(key, runId);
    patch(key, { runActive: false, runId: null });
    let durableRunId = runId;
    if (!durableRunId) {
      try {
        durableRunId = (await conversationsApi.activeRun(agentId, conversationId))?.id ?? null;
      } catch {
        durableRunId = null;
      }
    }
    if (!durableRunId) return;
    try {
      await conversationsApi.stopRun(agentId, conversationId, durableRunId);
    } catch (value) {
      patch(key, { error: value instanceof Error ? value.message : 'Could not stop run.' });
    }
  },

  async resolveApproval(agentId: string, conversationId: string, runId: string, choice: RunApprovalChoice) {
    const key = keyOf(agentId, conversationId);
    const snap = getSnapshot(key);
    const subsystem = snap.runs.find((run) => run.id === runId)?.approval?.subsystem;
    await conversationsApi.resolveRunApproval(agentId, conversationId, runId, choice, false, subsystem);
    patch(key, {
      runs: snap.runs.map((run) => (run.id === runId
        ? { ...run, status: run.status === 'waiting_for_approval' ? 'running' : run.status, approval: undefined }
        : run)),
    });
  },

  // Drop a finished session's local state once the server messages that
  // superseded it have been loaded, so returning to the tab shows no dupes.
  clearIfIdle(agentId: string, conversationId: string) {
    const key = keyOf(agentId, conversationId);
    const snap = snapshots.get(key);
    if (!snap || snap.streaming) return;
    snapshots.delete(key);
    runtimes.delete(key);
    recomputeStreaming();
    emit();
  },
};
