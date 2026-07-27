import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSandbox } from './useSandbox';

const mocks = vi.hoisted(() => ({
  get: vi.fn(async () => ({ provisioned: false, data: null })),
  stream: vi.fn(async (_onDetail: unknown, signal?: AbortSignal) => {
    await new Promise<void>((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }));
  }),
}));

vi.mock('../api/sandbox', () => ({
  sandboxApi: {
    get: mocks.get,
    stream: mocks.stream,
    setupStream: vi.fn(),
  },
}));

afterEach(() => {
  vi.clearAllMocks();
});

describe('useSandbox stream activation', () => {
  it('does not request runtime data until its UI is active', async () => {
    const { rerender, unmount } = renderHook(
      ({ vmActive }: { vmActive: boolean }) => useSandbox(vmActive),
      { initialProps: { vmActive: false } },
    );

    expect(mocks.get).not.toHaveBeenCalled();
    expect(mocks.stream).not.toHaveBeenCalled();

    rerender({ vmActive: true });
    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));

    rerender({ vmActive: false });
    await waitFor(() => expect(mocks.stream.mock.calls[0][1]?.aborted).toBe(true));
    unmount();
  });
});
