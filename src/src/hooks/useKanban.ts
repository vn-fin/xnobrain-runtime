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
  const [board, setBoard] = useState<KanbanBoard | null>(null);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [view, setView] = useState<KanbanViewMode>('board');
  const [search, setSearch] = useState('');

  const refresh = useCallback(async () => {
    setStatus('loading');
    try {
      setBoard(await kanbanApi.getBoard());
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
    setBoard((current) => {
      if (!current) return current;
      const tasks = current.tasks.map((task) =>
        task.id === taskId ? { ...task, status: nextStatus, updated: 'just now' } : task,
      );
      return { ...current, tasks };
    });
    void kanbanApi.moveTask(taskId, nextStatus);
  }, []);

  const createTask = useCallback(async (input: NewKanbanTaskInput) => {
    const task = await kanbanApi.createTask(input);
    setBoard((current) => (current ? { ...current, tasks: [task, ...current.tasks] } : current));
    return task;
  }, []);

  const addStatus = useCallback(async (label: string, column: KanbanStatusDef['column']) => {
    const created = await kanbanApi.addStatus(label, column);
    setBoard((current) =>
      current && !current.statuses.some((entry) => entry.id === created.id)
        ? { ...current, statuses: [...current.statuses, created] }
        : current,
    );
    return created;
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
    board,
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
