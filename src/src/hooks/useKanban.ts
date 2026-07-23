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
 * Manages the Kanban board: loads the (smoke) board, tracks the active view
 * mode / search filter, and exposes optimistic mutations for moving tasks,
 * creating tasks, and adding custom statuses.
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

  /** Resolve a task's fine-grained status to one of the three board columns. */
  const columnOf = useCallback(
    (taskStatus: string): KanbanColumnId =>
      board?.statuses.find((entry) => entry.id === taskStatus)?.column ?? 'todo',
    [board],
  );

  const statusLabel = useCallback(
    (taskStatus: string): string =>
      board?.statuses.find((entry) => entry.id === taskStatus)?.label ?? taskStatus,
    [board],
  );

  const moveTask = useCallback((taskId: string, nextStatus: string) => {
    if (!board) return;
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
    void kanbanApi.moveTask(board.id, taskId, nextStatus);
  }, [board]);

  const createTask = useCallback(async (input: NewKanbanTaskInput) => {
    if (!board) throw new Error('No board is selected.');
    const task = await kanbanApi.createTask(board.id, input);
    setBoards((current) =>
      current.map((item) => (item.id === board.id ? { ...item, tasks: [task, ...item.tasks] } : item)),
    );
    return task;
  }, [board]);

  const addStatus = useCallback(async (label: string, column: KanbanStatusDef['column']) => {
    if (!board) throw new Error('No board is selected.');
    const created = await kanbanApi.addStatus(board.id, label, column);
    setBoards((current) =>
      current.map((item) =>
        item.id === board.id && !item.statuses.some((entry) => entry.id === created.id)
          ? { ...item, statuses: [...item.statuses, created] }
          : item,
      ),
    );
    return created;
  }, [board]);

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
