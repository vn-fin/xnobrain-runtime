import { StrictMode, type PropsWithChildren } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from './useKanban';

const mocks = vi.hoisted(() => ({
  getBoards: vi.fn(async () => []),
  getTasks: vi.fn(async () => ({ boardId: 'default', tasks: [], total: 0, offset: 0, limit: 100 })),
  getBoardStats: vi.fn(async () => ({
    boardSlug: 'default', total: 0, current: 0, completed: 0, archived: 0,
    running: 0, blocked: 0,
    byStatus: { backlog: 0, todo: 0, scheduled: 0, running: 0, done: 0, archived: 0 },
  })),
  getTask: vi.fn(),
  watchBoard: vi.fn(async (
    _boardId: string,
    onEvent: (event: { id: string; event: string; data: unknown }) => void,
    signal: AbortSignal,
  ) => {
    onEvent({ id: '0', event: 'connected', data: { cursor: 0 } });
    await new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve(), { once: true }));
  }),
}));

vi.mock('../api/kanban', () => ({
  kanbanApi: {
    getBoards: mocks.getBoards,
    getTasks: mocks.getTasks,
    getBoardStats: mocks.getBoardStats,
    getTask: mocks.getTask,
    watchBoard: mocks.watchBoard,
  },
}));

describe('useKanban global event feed', () => {
  afterEach(() => {
    vi.clearAllMocks();
    mocks.getBoards.mockImplementation(async () => []);
    mocks.getTasks.mockImplementation(async () => ({ boardId: 'default', tasks: [], total: 0, offset: 0, limit: 100 }));
  });

  it('does not open the board or event stream while inactive', async () => {
    const { unmount } = renderHook(() => useKanban(false));
    await Promise.resolve();
    expect(mocks.getBoards).not.toHaveBeenCalled();
    expect(mocks.watchBoard).not.toHaveBeenCalled();
    unmount();
  });

  it('keeps one stream and loads boards once per activation under StrictMode', async () => {
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;
    const { rerender, unmount } = renderHook(
      ({ active }: { active: boolean }) => useKanban(active),
      { initialProps: { active: true }, wrapper },
    );

    await waitFor(() => expect(mocks.watchBoard).toHaveBeenCalledTimes(1));
    expect(mocks.watchBoard.mock.calls[0][0]).toBe('default');
    await waitFor(() => expect(mocks.getBoards).toHaveBeenCalledTimes(1));

    rerender({ active: false });
    rerender({ active: true });
    await waitFor(() => expect(mocks.getBoards).toHaveBeenCalledTimes(2));
    expect(mocks.watchBoard).toHaveBeenCalledTimes(2);
    unmount();
  });

  it('drops heartbeat SSE events before they reach notification state', async () => {
    mocks.watchBoard.mockImplementationOnce(async (
      _boardId: string,
      onEvent: (event: { id: string; event: string; data: unknown }) => void,
      signal: AbortSignal,
    ) => {
      onEvent({ id: '0', event: 'connected', data: { cursor: 0 } });
      onEvent({
        id: '1',
        event: 'task',
        data: {
          id: 1,
          task_id: 'task-1',
          title: 'Routine check-in',
          kind: 'heartbeat',
          created_at: '2026-07-27T14:06:00Z',
          status: 'running',
          kanban_status: 'running',
        },
      });
      await new Promise<void>((resolve) => signal.addEventListener('abort', () => resolve(), { once: true }));
    });
    const { result, unmount } = renderHook(() => useKanban(true));
    await waitFor(() => expect(mocks.watchBoard).toHaveBeenCalled());
    expect(result.current.events).toEqual([]);
    unmount();
  });

  it('keeps hydrated task detail when a slower list request finishes afterward', async () => {
    let releaseList!: (value: unknown) => void;
    mocks.getBoards.mockResolvedValueOnce([{
      id: 'default', name: 'Board', description: '', color: '#fff', statuses: [], tasks: [],
    }]);
    mocks.getTasks.mockImplementationOnce(() => new Promise((resolve) => { releaseList = resolve; }) as any);
    mocks.getTask.mockResolvedValueOnce({
      id: 'task-1', title: 'Durable task', description: 'Done', status: 'done', nativeStatus: 'done',
      allowedStatuses: ['archived'], priority: 'medium', assignees: [], tags: [], skills: [], deps: [],
      comments: [], events: [{ id: 7, kind: 'completed', payload: null, createdAt: '2026-08-14T00:00:00Z' }],
      runs: [], workerActivity: { exists: true, sizeBytes: 3655, entries: [] }, conversation: null,
      progress: 100, updated: 'just now', schedule: null, team: null,
    });

    const { result, unmount } = renderHook(() => useKanban(true));
    await waitFor(() => expect(result.current.board?.id).toBe('default'));
    await result.current.refreshTask('task-1');
    releaseList({
      boardId: 'default', total: 1, offset: 0, limit: 100,
      tasks: [{
        id: 'task-1', title: 'Durable task', description: 'Done', status: 'done', nativeStatus: 'done',
        allowedStatuses: ['archived'], priority: 'medium', assignees: [], tags: [], skills: [], deps: [],
        comments: [], events: [], runs: [], workerActivity: null, conversation: null,
        progress: 100, updated: 'just now', schedule: null, team: null,
      }],
    });

    await waitFor(() => expect(result.current.board?.tasks[0]?.events).toHaveLength(1));
    expect(result.current.board?.tasks[0]?.workerActivity?.sizeBytes).toBe(3655);
    unmount();
  });
});
