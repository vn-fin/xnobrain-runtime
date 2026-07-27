import { StrictMode, type PropsWithChildren } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useBlends } from './useBlends';

const mocks = vi.hoisted(() => ({
  list: vi.fn(async () => []),
}));

vi.mock('../api/blends', () => ({
  blendsApi: {
    list: mocks.list,
  },
}));

describe('useBlends', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads once per activation under StrictMode', async () => {
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;
    const { rerender } = renderHook(
      ({ active }: { active: boolean }) => useBlends(active),
      { initialProps: { active: true }, wrapper },
    );

    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(1));

    rerender({ active: false });
    rerender({ active: true });
    await waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
  });
});
