import { StrictMode, type PropsWithChildren } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useBlends } from './useBlends';

const mocks = vi.hoisted(() => ({
  list: vi.fn(async () => []),
  availableModels: vi.fn(async () => [{ id: 'cx/gpt-test' }]),
}));

vi.mock('../api/blends', () => ({
  blendsApi: {
    list: mocks.list,
    availableModels: mocks.availableModels,
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

  it('shares and caches the available-model request', async () => {
    const { result } = renderHook(() => useBlends(false));

    const [first, second] = await Promise.all([
      result.current.availableModels(),
      result.current.availableModels(),
    ]);

    expect(first).toEqual(second);
    expect(mocks.availableModels).toHaveBeenCalledTimes(1);
  });
});
