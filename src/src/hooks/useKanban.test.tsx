import { StrictMode, type PropsWithChildren } from 'react';
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
    expect(mocks.watchBoard).toHaveBeenCalledTimes(1);
    unmount();
  });
});
