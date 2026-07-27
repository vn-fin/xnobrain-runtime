import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from './useKanban';

const mocks = vi.hoisted(() => ({
  getBoards: vi.fn(async () => []),
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
    watchBoard: mocks.watchBoard,
  },
}));

describe('useKanban global event feed', () => {
  afterEach(() => vi.clearAllMocks());

  it('keeps one default-board stream without loading board data until Kanban opens', async () => {
    const { rerender, unmount } = renderHook(
      ({ active }: { active: boolean }) => useKanban(active),
      { initialProps: { active: false } },
    );

    await waitFor(() => expect(mocks.watchBoard).toHaveBeenCalledTimes(1));
    expect(mocks.watchBoard.mock.calls[0][0]).toBe('default');
    expect(mocks.getBoards).not.toHaveBeenCalled();

    rerender({ active: true });
    await waitFor(() => expect(mocks.getBoards).toHaveBeenCalledTimes(1));
    expect(mocks.watchBoard).toHaveBeenCalledTimes(1);
    unmount();
  });
});
