import { request, requestRaw } from './client';
import { readSSE, type SSEEvent } from './stream';
import type {
  KanbanBoard,
  KanbanBoardStats,
  KanbanColumnId,
  KanbanDependency,
  KanbanNativeStatus,
  KanbanStatusDef,
  KanbanTask,
  KanbanTaskPatchInput,
  NewKanbanBoardInput,
  NewKanbanTaskInput,
} from '../types';

export const KANBAN_COLUMNS: Array<{ id: KanbanColumnId; label: string; hint: string }> = [
  { id: 'backlog', label: 'Backlog', hint: 'Ideas and needs clarification' },
  { id: 'todo', label: 'Todo', hint: 'Work waiting or scheduled' },
  { id: 'running', label: 'In Progress', hint: 'Ready or being worked on' },
  { id: 'done', label: 'Done', hint: 'Completed or blocked work' },
];

export const ARCHIVED_COLUMN = {
  id: 'archived' as KanbanColumnId,
  label: 'Archived',
  hint: 'Work kept for history',
};

const STATUS_DEFS: KanbanStatusDef[] = [...KANBAN_COLUMNS, ARCHIVED_COLUMN].map((column) => ({
  id: column.id,
  label: column.label,
  column: column.id,
}));

type RawTask = Record<string, any>;

function defaultAllowedStatuses(nativeStatus: KanbanNativeStatus): KanbanColumnId[] {
  if (nativeStatus === 'triage') return ['todo', 'running', 'archived'];
  if (nativeStatus === 'todo') return ['running', 'done', 'archived'];
  if (nativeStatus === 'ready' || nativeStatus === 'running') return ['done', 'archived'];
  if (nativeStatus === 'scheduled') return ['archived'];
  if (nativeStatus === 'blocked') return ['todo', 'archived'];
  if (nativeStatus === 'review') return ['running', 'archived'];
  if (nativeStatus === 'done') return ['archived'];
  return [];
}

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
  const kanbanStatus = (raw.kanban_status ?? raw.status ?? 'todo') as KanbanColumnId;
  const nativeStatus = (raw.status ?? raw.hermes_status ?? 'todo') as KanbanNativeStatus;
  const advertisedStatuses = Array.isArray(raw.allowed_kanban_statuses)
    ? raw.allowed_kanban_statuses.map(String) as KanbanColumnId[]
    : defaultAllowedStatuses(nativeStatus);
  // Older/running runtime processes advertised no moves for completed tasks,
  // although Hermes' archive endpoint supports done -> archived. Normalize
  // that response during rolling upgrades so the valid action stays visible.
  const allowedStatuses = nativeStatus === 'done'
    && kanbanStatus !== 'archived'
    && !advertisedStatuses.includes('archived')
    ? [...advertisedStatuses, 'archived' as KanbanColumnId]
    : advertisedStatuses;
  const parents = Array.isArray(raw.parents) ? raw.parents : [];
  const deps: KanbanDependency[] = parents.map((dep: any) => ({
    id: String(dep.id),
    title: String(dep.title ?? dep.id),
    state: dep.kanban_status === 'done' || dep.kanban_status === 'archived' || dep.status === 'done' || dep.status === 'archived' ? 'done' : 'pending',
  }));
  const comments = Array.isArray(raw.comments) ? raw.comments : [];
  const events = Array.isArray(raw.events) ? raw.events : [];
  const runs = Array.isArray(raw.runs) ? raw.runs : [];
  const activity = raw.worker_activity && typeof raw.worker_activity === 'object'
    ? raw.worker_activity
    : null;
  const conversation = raw.conversation && typeof raw.conversation === 'object'
    ? raw.conversation
    : null;
  return {
    id: String(raw.id),
    title: String(raw.title ?? ''),
    description: String(raw.description ?? ''),
    status: kanbanStatus,
    nativeStatus,
    allowedStatuses,
    priority: raw.priority === 'high' || raw.priority === 'low' ? raw.priority : 'medium',
    assignees: Array.isArray(raw.assignees) ? raw.assignees.map(String) : raw.assignee ? [String(raw.assignee)] : [],
    tags: Array.isArray(raw.tags) ? raw.tags.map(String) : [],
    skills: Array.isArray(raw.skills) ? raw.skills.map(String) : [],
    deps,
    comments: comments.map((comment: any) => ({
      id: Number(comment.id),
      author: String(comment.author ?? 'user'),
      body: String(comment.body ?? ''),
      createdAt: String(comment.created_at ?? ''),
    })),
    events: events.map((event: any) => ({
      id: Number(event.id),
      kind: String(event.kind ?? 'updated'),
      payload: event.payload && typeof event.payload === 'object' ? event.payload : null,
      createdAt: String(event.created_at ?? ''),
    })),
    runs: runs.map((run: any) => ({
      id: Number(run.id),
      profile: run.profile == null ? null : String(run.profile),
      status: String(run.status ?? ''),
      outcome: run.outcome == null ? null : String(run.outcome),
      summary: run.summary == null ? null : String(run.summary),
      startedAt: String(run.started_at ?? ''),
      endedAt: run.ended_at == null ? null : String(run.ended_at),
    })),
    workerActivity: activity ? {
      exists: Boolean(activity.exists),
      sizeBytes: Number(activity.size_bytes ?? 0),
      entries: Array.isArray(activity.entries) ? activity.entries.map((entry: any) => ({
        kind: String(entry.kind ?? 'tool'),
        name: String(entry.name ?? 'activity'),
        durationSeconds: Number(entry.duration_seconds ?? 0),
      })) : [],
    } : null,
    conversation: conversation ? {
      id: String(conversation.id ?? ''),
      agentId: String(conversation.agent_id ?? ''),
      url: String(conversation.url ?? ''),
    } : null,
    progress: Number(raw.progress ?? 0),
    updated: relativeTime(raw.updated_at),
    block: raw.block ?? raw.state_detail?.reason ?? null,
    summary: raw.summary ?? null,
    result: raw.result ?? raw.summary ?? null,
    schedule: raw.schedule && typeof raw.schedule === 'object' ? {
      recurrence: raw.schedule.recurrence === 'interval' ? 'interval' : 'once',
      nextRunAt: raw.schedule.next_run_at == null ? null : String(raw.schedule.next_run_at),
      intervalMinutes: raw.schedule.interval_minutes == null ? null : Number(raw.schedule.interval_minutes),
      timezone: String(raw.schedule.timezone ?? 'Etc/UTC'),
      enabled: Boolean(raw.schedule.enabled),
      occurrenceCount: Number(raw.schedule.occurrence_count ?? 0),
      lastRunAt: raw.schedule.last_run_at == null ? null : String(raw.schedule.last_run_at),
    } : null,
    team: raw.team && typeof raw.team === 'object' ? {
      id: String(raw.team.id ?? ''),
      name: String(raw.team.name ?? 'Agent team'),
      orchestratorId: String(raw.team.orchestrator_id ?? ''),
      status: String(raw.team.status ?? 'todo'),
      synthesisTaskId: raw.team.synthesis_task_id == null ? null : String(raw.team.synthesis_task_id),
      progress: Number(raw.team.progress ?? 0),
      cancelled: Boolean(raw.team.cancelled),
      nodes: Array.isArray(raw.team.nodes) ? raw.team.nodes.map((node: any) => ({
        stepId: String(node.step_id ?? ''),
        taskId: String(node.task_id ?? ''),
        title: String(node.title ?? ''),
        agentId: String(node.agent_id ?? ''),
        role: String(node.role ?? 'worker'),
        needs: Array.isArray(node.needs) ? node.needs.map(String) : [],
        status: String(node.status ?? 'todo'),
        kanbanStatus: String(node.kanban_status ?? 'todo'),
        summary: node.summary == null ? null : String(node.summary),
      })) : [],
    } : null,
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
    taskCount: raw.task_count == null ? undefined : Number(raw.task_count),
  };
}

export type KanbanTaskPage = {
  boardId: string;
  tasks: KanbanTask[];
  total: number;
  offset: number;
  limit: number;
};

export const kanbanApi = {
  async getBoards(): Promise<KanbanBoard[]> {
    const data = await request<any[]>('/xnobrain/api/runtime/v1/kanban/boards');
    return (data ?? []).map(boardFromApi);
  },

  async getBoardStats(boardId: string): Promise<KanbanBoardStats> {
    let raw = await request<any>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/stats`);
    // Accept the previous eager-board shape during rolling upgrades and in
    // environments where the frontend updates before the runtime process.
    if (Array.isArray(raw)) {
      const legacy = raw.find((item) => String(item.id ?? item.slug) === boardId);
      const tasks = Array.isArray(legacy?.tasks) ? legacy.tasks : [];
      const byStatus = { backlog: 0, todo: 0, running: 0, done: 0, archived: 0 };
      for (const task of tasks) {
        const status = String(task.kanban_status ?? task.status ?? 'todo') as KanbanColumnId;
        if (status in byStatus) byStatus[status] += 1;
      }
      raw = {
        board_slug: boardId,
        total: tasks.length,
        current: tasks.length - byStatus.archived,
        completed: byStatus.done,
        archived: byStatus.archived,
        running: byStatus.running,
        blocked: tasks.filter((task: RawTask) => task.status === 'blocked').length,
        by_status: byStatus,
      };
    }
    const counts = raw.by_status && typeof raw.by_status === 'object' ? raw.by_status : {};
    return {
      boardSlug: String(raw.board_slug ?? boardId),
      total: Number(raw.total ?? 0),
      current: Number(raw.current ?? 0),
      completed: Number(raw.completed ?? 0),
      archived: Number(raw.archived ?? 0),
      running: Number(raw.running ?? 0),
      blocked: Number(raw.blocked ?? 0),
      byStatus: {
        backlog: Number(counts.backlog ?? 0),
        todo: Number(counts.todo ?? 0),
        running: Number(counts.running ?? 0),
        done: Number(counts.done ?? 0),
        archived: Number(counts.archived ?? 0),
      },
    };
  },

  async getTasks(
    boardId: string,
    options: { offset?: number; limit?: number; archived?: boolean } = {},
  ): Promise<KanbanTaskPage> {
    const params = new URLSearchParams({
      offset: String(options.offset ?? 0),
      limit: String(options.limit ?? 100),
    });
    if (options.archived) {
      params.set('include_archived', 'true');
      params.set('status', 'archived');
    }
    const raw = await request<any>(
      `/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks?${params}`,
    );
    if (Array.isArray(raw)) {
      const legacy = raw.find((item) => String(item.id ?? item.slug) === boardId);
      const all = (Array.isArray(legacy?.tasks) ? legacy.tasks : []).filter((task: RawTask) =>
        options.archived
          ? String(task.kanban_status ?? task.status) === 'archived'
          : String(task.kanban_status ?? task.status) !== 'archived');
      const offset = options.offset ?? 0;
      const limit = options.limit ?? 100;
      return {
        boardId,
        tasks: all.slice(offset, offset + limit).map(taskFromApi),
        total: all.length,
        offset,
        limit,
      };
    }
    return {
      boardId: String(raw.board_slug ?? boardId),
      tasks: Array.isArray(raw.tasks) ? raw.tasks.map(taskFromApi) : [],
      total: Number(raw.total ?? 0),
      offset: Number(raw.offset ?? 0),
      limit: Number(raw.limit ?? options.limit ?? 100),
    };
  },

  async createBoard(input: NewKanbanBoardInput): Promise<KanbanBoard> {
    const data = await request<any>('/xnobrain/api/runtime/v1/kanban/boards', {
      method: 'POST',
      body: JSON.stringify(input),
    });
    return boardFromApi(data);
  },

  async createTask(boardId: string, input: NewKanbanTaskInput): Promise<KanbanTask> {
    if (input.status !== 'backlog' && input.status !== 'todo' && input.status !== 'scheduled') {
      throw new Error('New tasks can start in Backlog, Todo, or Scheduled.');
    }
    const data = await request<RawTask>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks`, {
      method: 'POST',
      body: JSON.stringify({
        title: input.title,
        description: input.description,
        status: input.status,
        priority: input.priority,
        assignee: input.assignee,
        team_id: input.teamId || undefined,
        skills: input.skills,
        schedule: input.schedule,
      }),
    });
    return taskFromApi(data);
  },

  async getTask(boardId: string, taskId: string): Promise<KanbanTask> {
    const data = await request<RawTask>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}`);
    return taskFromApi(data);
  },

  async updateTask(boardId: string, taskId: string, input: KanbanTaskPatchInput): Promise<KanbanTask> {
    const data = await request<RawTask>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}`, {
      method: 'PATCH',
      body: JSON.stringify(input),
    });
    return taskFromApi(data);
  },

  async moveTask(boardId: string, taskId: string, status: KanbanColumnId): Promise<KanbanTask | null> {
    if (status === 'archived') {
      return this.archiveTask(boardId, taskId);
    }
    const data = await request<RawTask>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/move`, {
      method: 'POST',
      body: JSON.stringify({ status }),
    });
    return data ? taskFromApi(data) : null;
  },

  async archiveTask(boardId: string, taskId: string): Promise<KanbanTask> {
    const data = await request<RawTask>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/archive`, { method: 'POST' });
    return taskFromApi(data);
  },

  async cancelTeamTask(boardId: string, taskId: string): Promise<KanbanTask> {
    const data = await request<RawTask>(
      `/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/team/cancel`,
      { method: 'POST' },
    );
    return taskFromApi(data);
  },

  async cancelTask(boardId: string, taskId: string): Promise<KanbanTask> {
    const data = await request<RawTask>(
      `/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/cancel`,
      { method: 'POST' },
    );
    return taskFromApi(data);
  },

  async assignTask(boardId: string, taskId: string, assignee: string | null): Promise<KanbanTask> {
    const data = await request<RawTask>(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/assign`, {
      method: 'POST',
      body: JSON.stringify({ assignee }),
    });
    return taskFromApi(data);
  },

  async scheduleAction(
    boardId: string,
    taskId: string,
    action: 'pause' | 'resume' | 'run_now',
  ): Promise<KanbanTask> {
    const data = await request<RawTask>(
      `/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/schedule`,
      { method: 'POST', body: JSON.stringify({ action }) },
    );
    return taskFromApi(data);
  },

  async watchBoard(
    boardId: string,
    onEvent: (event: SSEEvent) => void,
    signal: AbortSignal,
    afterId?: number,
  ): Promise<void> {
    const query = afterId != null ? `?after=${encodeURIComponent(String(afterId))}` : '';
    const response = await requestRaw(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/events/stream${query}`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    });
    await readSSE(response, onEvent, signal);
  },

  async addComment(boardId: string, taskId: string, body: string): Promise<void> {
    await request(`/xnobrain/api/runtime/v1/kanban/boards/${encodeURIComponent(boardId)}/tasks/${encodeURIComponent(taskId)}/comments`, {
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
