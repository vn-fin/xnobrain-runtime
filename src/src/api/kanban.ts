import { request, requestRaw } from './client';
import { readSSE, type SSEEvent } from './stream';
import type {
  KanbanBoard,
  KanbanColumnId,
  KanbanDependency,
  KanbanNativeStatus,
  KanbanStatusDef,
  KanbanTask,
  NewKanbanTaskInput,
} from '../types';

export const KANBAN_COLUMNS: Array<{ id: KanbanColumnId; label: string; hint: string }> = [
  { id: 'backlog', label: 'Backlog', hint: 'Ideas and needs clarification' },
  { id: 'todo', label: 'Todo', hint: 'Work waiting to start' },
  { id: 'running', label: 'Running', hint: 'Ready or being worked on' },
  { id: 'done', label: 'Done', hint: 'Completed or blocked work' },
  { id: 'archived', label: 'Archived', hint: 'Work kept for history' },
];

const STATUS_DEFS: KanbanStatusDef[] = KANBAN_COLUMNS.map((column) => ({
  id: column.id,
  label: column.label,
  column: column.id,
}));

type RawTask = Record<string, any>;

function relativeTime(value: string | null | undefined): string {
  if (!value) return '—';
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return value;
  const seconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function taskFromApi(raw: RawTask): KanbanTask {
  const status = (raw.kanban_status ?? raw.status ?? 'todo') as KanbanColumnId;
  const nativeStatus = (raw.status ?? raw.hermes_status ?? 'todo') as KanbanNativeStatus;
  const parents = Array.isArray(raw.parents) ? raw.parents : [];
  const deps: KanbanDependency[] = parents.map((dep: any) => ({
    id: String(dep.id),
    title: String(dep.title ?? dep.id),
    state: dep.kanban_status === 'done' || dep.kanban_status === 'archived' || dep.status === 'done' || dep.status === 'archived' ? 'done' : 'pending',
  }));
  return {
    id: String(raw.id),
    title: String(raw.title ?? ''),
    description: String(raw.description ?? ''),
    status,
    nativeStatus,
    priority: raw.priority === 'high' || raw.priority === 'low' ? raw.priority : 'medium',
    assignees: Array.isArray(raw.assignees) ? raw.assignees.map(String) : raw.assignee ? [String(raw.assignee)] : [],
    tags: Array.isArray(raw.tags) ? raw.tags.map(String) : [],
    deps,
    progress: Number(raw.progress ?? 0),
    updated: relativeTime(raw.updated_at),
    block: raw.block ?? raw.state_detail?.reason ?? null,
    summary: raw.summary ?? null,
  };
}

function boardFromApi(raw: any): KanbanBoard {
  return {
    id: String(raw.id ?? raw.slug ?? 'default'),
    name: String(raw.name ?? raw.slug ?? 'Task board'),
    description: String(raw.description ?? ''),
    color: String(raw.color || '#4f8cff'),
    statuses: STATUS_DEFS.map((status) => ({ ...status })),
    tasks: Array.isArray(raw.tasks) ? raw.tasks.map(taskFromApi) : [],
  };
}

export const kanbanApi = {
  async getBoards(): Promise<KanbanBoard[]> {
    const data = await request<any[]>('/agent-gateway/v1/kanban/boards?include_archived=true');
    return (data ?? []).map(boardFromApi);
  },

  async createTask(boardId: string, input: NewKanbanTaskInput): Promise<KanbanTask> {
    if (input.status !== 'backlog' && input.status !== 'todo') {
      throw new Error('New tasks can start in Backlog or Todo.');
    }
    const data = await request<RawTask>(`/agent-gateway/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks`, {
      method: 'POST',
      body: JSON.stringify({
        title: input.title,
        description: input.description,
        status: input.status,
        priority: input.priority,
        assignee: input.assignee,
      }),
    });
    return taskFromApi(data);
  },

  async moveTask(boardId: string, taskId: string, status: KanbanColumnId): Promise<KanbanTask | null> {
    if (status === 'archived') {
      return this.archiveTask(boardId, taskId);
    }
    const data = await request<RawTask>(`/agent-gateway/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/move`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    });
    return data ? taskFromApi(data) : null;
  },

  async archiveTask(boardId: string, taskId: string): Promise<KanbanTask> {
    const data = await request<RawTask>(`/agent-gateway/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/archive`, { method: 'POST' });
    return taskFromApi(data);
  },

  async assignTask(boardId: string, taskId: string, assignee: string | null): Promise<KanbanTask> {
    const data = await request<RawTask>(`/agent-gateway/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/assign`, {
      method: 'POST',
      body: JSON.stringify({ assignee }),
    });
    return taskFromApi(data);
  },

  async watchBoard(
    boardId: string,
    onEvent: (event: SSEEvent) => void,
    signal: AbortSignal,
    afterId?: number,
  ): Promise<void> {
    const query = afterId != null ? `?after=${encodeURIComponent(String(afterId))}` : '';
    const response = await requestRaw(`/agent-gateway/v1/kanban/boards/${encodeURIComponent(boardId)}/events/stream${query}`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    });
    await readSSE(response, onEvent, signal);
  },

  async addComment(boardId: string, taskId: string, body: string): Promise<void> {
    await request(`/agent-gateway/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/comments`, {
      method: 'POST',
      body: JSON.stringify({ body, author: 'user' }),
    });
  },

  // Kept as an explicit rejection so no UI can accidentally reintroduce
  // custom workflow columns.
  async addStatus(): Promise<never> {
    throw new Error('Kanban uses the five default statuses.');
  },
};
