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
  const [requestedTaskId, setRequestedTaskId] = useState<string | null>(null);
  const noticeId = useRef(0);
  const boardLoadActive = useRef(false);

  const notify = useCallback((kind: KanbanNotice['kind'], message: string) => {
    noticeId.current += 1;
    setNotice({ id: noticeId.current, kind, message });
  }, []);

  const load = useCallback(async (showLoading = true) => {
    if (showLoading) setStatus('loading');
    try {
      const loaded = await kanbanApi.getBoards();
      setBoards((current) => loaded.map((nextBoard) => {
        const cachedBoard = current.find((item) => item.id === nextBoard.id);
        if (!cachedBoard) return nextBoard;
        return {
          ...nextBoard,
          tasks: nextBoard.tasks.map((task) => {
            const cached = cachedBoard.tasks.find((item) => item.id === task.id);
            if (!cached) return task;
            return {
              ...task,
              comments: cached.comments,
              events: cached.events,
              runs: cached.runs,
              workerActivity: cached.workerActivity,
              conversation: cached.conversation,
            };
          }),
        };
      }));
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
    if (!active) {
      boardLoadActive.current = false;
      return;
    }
    if (boardLoadActive.current) return;
    boardLoadActive.current = true;
    void load();
  }, [active, load]);
  const activeRef = useRef(active);
  const loadRef = useRef(load);
  activeRef.current = active;
  loadRef.current = load;

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

  const updateSchedule = useCallback(async (
    taskId: string,
    action: 'pause' | 'resume' | 'run_now',
  ) => {
    if (!board) return null;
    try {
      const updated = await kanbanApi.scheduleAction(board.id, taskId, action);
      replaceTask(board.id, taskId, updated);
      if (action === 'run_now') void load(false);
      notify(
        'success',
        action === 'run_now'
          ? `Started “${updated.title}”.`
          : `${action === 'pause' ? 'Paused' : 'Resumed'} “${updated.title}”.`,
      );
      return updated;
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The task schedule could not be updated.');
      return null;
    }
  }, [board, load, notify, replaceTask]);

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

  const cancelTeamTask = useCallback(async (taskId: string) => {
    if (!board) return null;
    try {
      const updated = await kanbanApi.cancelTeamTask(board.id, taskId);
      replaceTask(board.id, taskId, updated);
      notify('success', `Cancelled “${updated.title}”.`);
      return updated;
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The team run could not be cancelled.');
      return null;
    }
  }, [board, notify, replaceTask]);

  const cancelTask = useCallback(async (taskId: string) => {
    if (!board) return null;
    try {
      const updated = await kanbanApi.cancelTask(board.id, taskId);
      replaceTask(board.id, taskId, updated);
      notify('success', `Cancelled “${updated.title}”.`);
      return updated;
    } catch (cause) {
      notify('error', cause instanceof Error ? cause.message : 'The task could not be cancelled.');
      return null;
    }
  }, [board, notify, replaceTask]);

  const addStatus = useCallback(async (_label: string, _column: KanbanStatusDef['column']) => {
    throw new Error('Kanban uses the five default statuses.');
  }, []);

  // The default-board event feed is the one global subscription. Board
  // details remain route-scoped and are fetched only while Kanban is open.
  const streamBoardId = 'default';
  const streamRef = useRef<{
    controller: AbortController;
    reconnectTimer?: number;
    refreshTimer?: number;
    cursor?: number;
  } | null>(null);
  const streamStopTimer = useRef<number>();
  useEffect(() => {
    if (streamStopTimer.current !== undefined) {
      window.clearTimeout(streamStopTimer.current);
      streamStopTimer.current = undefined;
    }

    let stream = streamRef.current;
    if (!stream) {
      const connection = { controller: new AbortController() } as {
        controller: AbortController;
        reconnectTimer?: number;
        refreshTimer?: number;
        cursor?: number;
      };
      stream = connection;
      streamRef.current = connection;

      const connect = async () => {
        if (connection.cursor == null) setLiveStatus('connecting');
        try {
          await kanbanApi.watchBoard(streamBoardId, (event) => {
            const eventId = Number(event.id);
            if (Number.isFinite(eventId)) connection.cursor = eventId;
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
            if (String(data.kind ?? '').toLowerCase() === 'heartbeat') return;
            const item: KanbanEvent = {
              id: Number(data.id ?? eventId),
              taskId: String(data.task_id ?? ''),
              title: String(data.title ?? data.task_id ?? 'Task'),
              kind: String(data.kind ?? 'updated'),
              createdAt: String(data.created_at ?? ''),
              assignee: data.assignee == null ? null : String(data.assignee),
              nativeStatus: String(data.status ?? 'todo') as KanbanNativeStatus,
              status: String(data.kanban_status ?? 'todo') as KanbanColumnId,
            };
            setEvents((current) => [item, ...current.filter((existing) => existing.id !== item.id)].slice(0, 20));
            setBoards((current) => current.map((currentBoard) => currentBoard.id === streamBoardId
              ? {
                  ...currentBoard,
                  tasks: currentBoard.tasks.map((task) => task.id === item.taskId
                    ? {
                        ...task,
                        events: [
                          ...task.events.filter((existing) => existing.id !== item.id),
                          {
                            id: item.id,
                            kind: item.kind,
                            payload: data.payload && typeof data.payload === 'object'
                              ? data.payload as Record<string, unknown>
                              : null,
                            createdAt: item.createdAt,
                          },
                        ],
                      }
                    : task),
                }
              : currentBoard));
            setLiveStatus('live');
            if (connection.refreshTimer !== undefined) window.clearTimeout(connection.refreshTimer);
            if (activeRef.current) {
              connection.refreshTimer = window.setTimeout(() => void loadRef.current(false), 120);
            }
          }, connection.controller.signal, connection.cursor);
        } catch {
          if (connection.controller.signal.aborted) return;
          setLiveStatus('offline');
        }
        if (!connection.controller.signal.aborted) {
          connection.reconnectTimer = window.setTimeout(() => void connect(), 1_000);
        }
      };

      void connect();
    }
    const mountedStream = stream;

    return () => {
      // StrictMode immediately re-runs mount effects in development. Deferring
      // teardown by one task lets that setup retain this connection, while a
      // real unmount still closes it promptly.
      streamStopTimer.current = window.setTimeout(() => {
        if (streamRef.current !== mountedStream) return;
        mountedStream.controller.abort();
        if (mountedStream.reconnectTimer !== undefined) window.clearTimeout(mountedStream.reconnectTimer);
        if (mountedStream.refreshTimer !== undefined) window.clearTimeout(mountedStream.refreshTimer);
        streamRef.current = null;
        streamStopTimer.current = undefined;
      }, 0);
    };
  }, []);

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
    updateSchedule,
    addComment,
    refreshTask,
    detailLoading,
    createTask,
    cancelTask,
    cancelTeamTask,
    addStatus,
    refresh: () => load(),
    liveStatus,
    events,
    requestedTaskId,
    requestOpenTask: (taskId: string) => setRequestedTaskId(taskId),
    clearRequestedTask: () => setRequestedTaskId(null),
    notice,
    dismissNotice: () => setNotice(null),
  };
}
