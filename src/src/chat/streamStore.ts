import { conversationsApi } from '../api/conversations';
import type { SSEEvent } from '../api/stream';
import { reduceRunEvent } from './runEvents';
import type { ChatMessage, ChatRun, RunApprovalChoice } from '../types';

// A background-capable chat stream store. Streaming sessions are keyed by
// conversation and live outside any React component, so a run keeps going when
// the user switches to another conversation tab. Components subscribe via
// `useSyncExternalStore` and read an immutable snapshot per conversation.

export type QueuedMessage = { id: string; content: string };
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
};

type Runtime = {
  agentId: string;
  conversationId: string;
  controller?: AbortController;
  cancelled: boolean;
  processing: boolean;
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

async function execute(key: string, text: string) {
  const runtime = runtimes.get(key);
  if (!runtime) return;
  const { agentId, conversationId } = runtime;
  const controller = new AbortController();
  runtime.controller = controller;

  const userId = `local-user-${crypto.randomUUID()}`;
  const assistantId = `local-assistant-${crypto.randomUUID()}`;
  const base = getSnapshot(key);
  patch(key, {
    status: 'streaming',
    error: '',
    runActive: false,
    events: [],
    localMessages: [
      ...base.localMessages,
      { id: userId, role: 'user', content: text },
      { id: assistantId, role: 'assistant', content: '', streaming: true },
    ],
  });

  const updateAssistant = (map: (message: ChatMessage) => ChatMessage) => {
    const snap = getSnapshot(key);
    patch(key, { localMessages: snap.localMessages.map((m) => (m.id === assistantId ? map(m) : m)) });
  };

  const handleEvent = (event: SSEEvent) => {
    const data = event.data && typeof event.data === 'object' ? event.data as Record<string, unknown> : {};
    const type = typeof data.event === 'string' && data.event ? data.event : event.event;

    if (type === 'run.started' && typeof data.run_id === 'string') {
      patch(key, { runId: data.run_id as string, runActive: true });
    }

    const snap = getSnapshot(key);
    patch(key, { events: [...snap.events, event] });

    // Runs
    const runsSnap = getSnapshot(key).runs;
    if (type === 'run.started') {
      const next = reduceRunEvent(null, event);
      if (next && !runsSnap.some((run) => run.id === next.id)) {
        patch(key, { runs: [...runsSnap, { ...next, insertBeforeMessageId: assistantId }] });
      }
    } else if (runsSnap.length > 0) {
      const last = runsSnap[runsSnap.length - 1];
      const next = reduceRunEvent(last, event);
      if (next) patch(key, { runs: [...runsSnap.slice(0, -1), next] });
    }

    if (type === 'error') {
      const message = typeof event.data === 'string' ? event.data : String(data.message ?? 'Chat stream failed.');
      patch(key, { error: message });
      return;
    }
    if (type === 'assistant.completed' || type === 'message.completed') {
      if (typeof data.content === 'string') updateAssistant((m) => ({ ...m, content: data.content as string }));
      return;
    }
    if (type === 'run.completed') {
      if (typeof data.output === 'string' && data.output) {
        updateAssistant((m) => (m.content.trim() ? m : { ...m, content: data.output as string }));
      }
      return;
    }
    const delta = streamText(event);
    if (delta) updateAssistant((m) => ({ ...m, content: m.content + delta }));
  };

  try {
    await conversationsApi.stream(agentId, conversationId, text, handleEvent, controller.signal);
    updateAssistant((m) => ({ ...m, streaming: false }));
  } catch (value) {
    if (!abortError(value)) patch(key, { error: value instanceof Error ? value.message : 'Chat stream failed.' });
    updateAssistant((m) => ({ ...m, streaming: false }));
  } finally {
    if (runtime.controller === controller) runtime.controller = undefined;
    patch(key, { runActive: false, runId: null });
  }
}

async function drain(key: string) {
  const runtime = runtimes.get(key);
  if (!runtime || runtime.processing) return;
  runtime.processing = true;
  runtime.cancelled = false;
  patch(key, { streaming: true });
  try {
    while (!runtime.cancelled && getSnapshot(key).queue.length > 0) {
      const snap = getSnapshot(key);
      const [next, ...rest] = snap.queue;
      patch(key, { queue: rest });
      if (next) await execute(key, next.content);
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

  send(agentId: string, conversationId: string, text: string) {
    const clean = text.trim();
    if (!clean || !agentId || !conversationId) return;
    const key = keyOf(agentId, conversationId);
    runtimeFor(key, agentId, conversationId);
    const snap = getSnapshot(key);
    patch(key, { queue: [...snap.queue, { id: `queued-${crypto.randomUUID()}`, content: clean }] });
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
    if (!runId) return;
    try {
      await conversationsApi.stopRun(agentId, conversationId, runId);
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
