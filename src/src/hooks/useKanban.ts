import { useCallback, useEffect, useMemo, useState } from 'react';
import { kanbanApi } from '../api/kanban';
import type {
  AsyncStatus,
  KanbanBoard,
  KanbanColumnId,
  KanbanStatusDef,
  KanbanViewMode,
  NewKanbanTaskInput,
} from '../types';

const PRIORITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };

/**
 * Manages the real Hermes-backed Kanban board and exposes small optimistic
 * mutations for moving and creating tasks.
 */
export function useKanban() {
  const [boards, setBoards] = useState<KanbanBoard[]>([]);
  const [activeBoardId, setActiveBoardIdState] = useState(
    () => window.localStorage.getItem('brain4all-kanban-board') ?? '',
  );
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [view, setView] = useState<KanbanViewMode>('board');
  const [search, setSearch] = useState('');

  const refresh = useCallback(async () => {
    setStatus('loading');
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
    void refresh();
  }, [refresh]);

  const board = useMemo(
    () => boards.find((item) => item.id === activeBoardId) ?? boards[0] ?? null,
    [activeBoardId, boards],
  );

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
        setBoards((current) => current.map((item) => item.id === board.id
          ? { ...item, tasks: item.tasks.map((task) => task.id === taskId ? updated : task) }
          : item));
      }
    } catch (cause) {
      setBoards(previous);
      throw cause;
    }
  }, [board, boards]);

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

  // Tasks filtered by the search box, sorted by priority within their group.
  const visibleTasks = useMemo(() => {
    if (!board) return [];
    const query = search.trim().toLowerCase();
    const rows = query
      ? board.tasks.filter((task) =>
          [task.id, task.title, task.tags.join(' '), task.assignees.join(' '), task.status]
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
    createTask,
    addStatus,
    refresh,
  };
}
