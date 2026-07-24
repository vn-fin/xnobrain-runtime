import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { kanbanApi } from '../api/kanban';
import type {
  AsyncStatus,
  KanbanBoard,
  KanbanColumnId,
  KanbanEvent,
  KanbanNativeStatus,
  KanbanNotice,
  KanbanStatusDef,
  KanbanTaskPatchInput,
  KanbanViewMode,
  NewKanbanTaskInput,
} from '../types';

const PRIORITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

/**
 * Manages the real Hermes-backed Kanban board and exposes small optimistic
 * mutations for moving and creating tasks.
 */
export function useKanban(active = true) {
  const [boards, setBoards] = useState<KanbanBoard[]>([]);
  const [activeBoardId, setActiveBoardIdState] = useState(
    () => window.localStorage.getItem('brain4all-kanban-board') ?? '',
  );
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [view, setView] = useState<KanbanViewMode>('board');
  const [search, setSearch] = useState('');
  const [liveStatus, setLiveStatus] = useState<'connecting' | 'live' | 'offline'>('connecting');
  const [events, setEvents] = useState<KanbanEvent[]>([]);
  const [notice, setNotice] = useState<KanbanNotice | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const noticeId = useRef(0);

  const notify = useCallback((kind: KanbanNotice['kind'], message: string) => {
    noticeId.current += 1;
    setNotice({ id: noticeId.current, kind, message });
  }, []);

  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setStatus('loading');
    try {
      const loaded = await kanbanApi.getBoards();
      setBoards(loaded);
      setActiveBoardIdState((current) =>
        loaded.some((board) => board.id === current) ? current : loaded[0]?.id ?? '',
      );
      setStatus('ready');
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load the board.');
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    if (active) void load();
  }, [active, load]);

  const board = useMemo(
    () => boards.find((item) => item.id === activeBoardId) ?? boards[0] ?? null,
    [activeBoardId, boards],
  );

  const replaceTask = useCallback((boardId: string, taskId: string, updated: KanbanBoard['tasks'][number]) => {
    setBoards((current) => current.map((item) => item.id === boardId
      ? { ...item, tasks: item.tasks.map((task) => task.id === taskId ? updated : task) }
      : item));
  }, []);

  const refreshTask = useCallback(async (taskId: string, showLoading = true) => {
    if (!board) return null;
    if (showLoading) setDetailLoading(true);
    try {
      const updated = await kanbanApi.getTask(board.id, taskId);
      replaceTask(board.id, taskId, updated);
      return updated;
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The task details could not be loaded.');
      return null;
    } finally {
      if (showLoading) setDetailLoading(false);
    }
  }, [board, notify, replaceTask]);

  const setActiveBoardId = useCallback((boardId: string) => {
    setActiveBoardIdState(boardId);
    setSearch('');
    window.localStorage.setItem('brain4all-kanban-board', boardId);
  }, []);

  /** Product API already returns one of the five fixed columns. */
  const columnOf = useCallback(
    (taskStatus: string): KanbanColumnId =>
      (board?.statuses.find((entry) => entry.id === taskStatus)?.column ?? 'todo') as KanbanColumnId,
    [board],
  );

  const statusLabel = useCallback(
    (taskStatus: string): string =>
      board?.statuses.find((entry) => entry.id === taskStatus)?.label ?? taskStatus,
    [board],
  );

  const moveTask = useCallback(async (taskId: string, nextStatus: KanbanColumnId) => {
    if (!board) return;
    const title = board.tasks.find((task) => task.id === taskId)?.title ?? taskId;
    const label = board.statuses.find((item) => item.id === nextStatus)?.label ?? nextStatus;
    const previous = boards;
    setBoards((current) =>
      current.map((item) =>
        item.id === board.id
          ? {
              ...item,
              tasks: item.tasks.map((task) =>
                task.id === taskId ? { ...task, status: nextStatus, updated: 'just now' } : task,
              ),
            }
          : item,
      ),
    );
    try {
      const updated = await kanbanApi.moveTask(board.id, taskId, nextStatus);
      if (updated) {
        replaceTask(board.id, taskId, updated);
        void refreshTask(taskId, false);
      }
      notify('success', `Moved “${title}” to ${label}.`);
    } catch (cause) {
      setBoards(previous);
      notify('error', cause instanceof Error ? cause.message : 'The task could not be moved.');
    }
  }, [board, boards, notify, refreshTask, replaceTask]);

  const assignTask = useCallback(async (taskId: string, assignee: string | null) => {
    if (!board) return;
    try {
      const updated = await kanbanApi.assignTask(board.id, taskId, assignee);
      replaceTask(board.id, taskId, updated);
      void refreshTask(taskId, false);
      notify('success', assignee ? `Assigned “${updated.title}”.` : `Unassigned “${updated.title}”.`);
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The assignee could not be changed.');
    }
  }, [board, notify, refreshTask, replaceTask]);

  const updateTask = useCallback(async (taskId: string, input: KanbanTaskPatchInput) => {
    if (!board) return null;
    try {
      const updated = await kanbanApi.updateTask(board.id, taskId, input);
      replaceTask(board.id, taskId, updated);
      notify('success', `Saved “${updated.title}”.`);
      return updated;
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The task could not be saved.');
      return null;
    }
  }, [board, notify, replaceTask]);

  const addComment = useCallback(async (taskId: string, body: string) => {
    if (!board) return false;
    try {
      await kanbanApi.addComment(board.id, taskId, body);
      await refreshTask(taskId, false);
      notify('success', 'Comment added.');
      return true;
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The comment could not be added.');
      return false;
    }
  }, [board, notify, refreshTask]);

  const createTask = useCallback(async (input: NewKanbanTaskInput) => {
    if (!board) throw new Error('No board is selected.');
    const task = await kanbanApi.createTask(board.id, input);
    setBoards((current) =>
      current.map((item) => (item.id === board.id ? { ...item, tasks: [task, ...item.tasks] } : item)),
    );
    return task;
  }, [board]);

  const addStatus = useCallback(async (_label: string, _column: KanbanStatusDef['column']) => {
    throw new Error('Kanban uses the five default statuses.');
  }, []);

  const streamBoardId = activeBoardId || boards[0]?.id || '';
  useEffect(() => {
    if (!active || !streamBoardId) return undefined;
    const controller = new AbortController();
    let reconnectTimer: number | undefined;
    let refreshTimer: number | undefined;
    let cursor: number | undefined;

    const connect = async () => {
      if (cursor == null) setLiveStatus('connecting');
      try {
        await kanbanApi.watchBoard(streamBoardId, (event) => {
          const eventId = Number(event.id);
          if (Number.isFinite(eventId)) cursor = eventId;
          if (event.event === 'connected') {
            setLiveStatus('live');
            return;
          }
          if (event.event === 'error') {
            setLiveStatus('offline');
            return;
          }
          if (event.event !== 'task' || !event.data || typeof event.data !== 'object') return;
          const data = event.data as Record<string, unknown>;
          const item: KanbanEvent = {
            id: Number(data.id ?? eventId),
            taskId: String(data.task_id ?? ''),
            kind: String(data.kind ?? 'updated'),
            createdAt: String(data.created_at ?? ''),
            assignee: data.assignee == null ? null : String(data.assignee),
            nativeStatus: String(data.status ?? 'todo') as KanbanNativeStatus,
            status: String(data.kanban_status ?? 'todo') as KanbanColumnId,
          };
          setEvents((current) => [item, ...current.filter((existing) => existing.id !== item.id)].slice(0, 20));
          setLiveStatus('live');
          if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
          refreshTimer = window.setTimeout(() => void load(false), 120);
        }, controller.signal, cursor);
      } catch {
        if (controller.signal.aborted) return;
        setLiveStatus('offline');
      }
      if (!controller.signal.aborted) reconnectTimer = window.setTimeout(() => void connect(), 1_000);
    };

    void connect();
    return () => {
      controller.abort();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
      if (refreshTimer !== undefined) window.clearTimeout(refreshTimer);
    };
  }, [active, load, streamBoardId]);

  // Tasks filtered by the search box, sorted by priority within their group.
  const visibleTasks = useMemo(() => {
    if (!board) return [];
    const query = search.trim().toLowerCase();
    const rows = query
      ? board.tasks.filter((task) =>
          [task.id, task.title, task.tags.join(' '), task.assignees.join(' '), task.status, task.nativeStatus]
            .join(' ')
            .toLowerCase()
            .includes(query),
        )
      : board.tasks;
    return [...rows].sort((a, b) => (PRIORITY_RANK[a.priority] ?? 9) - (PRIORITY_RANK[b.priority] ?? 9));
  }, [board, search]);

  return {
    boards,
    board,
    activeBoardId: board?.id ?? activeBoardId,
    setActiveBoardId,
    status,
    error,
    view,
    setView,
    search,
    setSearch,
    visibleTasks,
    columnOf,
    statusLabel,
    moveTask,
    assignTask,
    updateTask,
    addComment,
    refreshTask,
    detailLoading,
    createTask,
    addStatus,
    refresh: () => load(),
    liveStatus,
    events,
    notice,
    dismissNotice: () => setNotice(null),
  };
}
