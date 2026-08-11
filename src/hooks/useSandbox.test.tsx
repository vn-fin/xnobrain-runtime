import { StrictMode, type PropsWithChildren } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useSandbox } from './useSandbox';

const mocks = vi.hoisted(() => ({
  get: vi.fn(async () => ({ provisioned: false, data: null })),
  stream: vi.fn(async (_onDetail: unknown, signal?: AbortSignal) => {
    await new Promise<void>((resolve) => signal?.addEventListener('abort', () => resolve(), { once: true }));
  }),
  setupStream: vi.fn(),
}));

vi.mock('../api/sandbox', () => ({
  sandboxApi: {
    get: mocks.get,
    stream: mocks.stream,
    setupStream: mocks.setupStream,
  },
}));

afterEach(() => {
  vi.clearAllMocks();
});

describe('useSandbox stream activation', () => {
  it('opens one stream under StrictMode', async () => {
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;
    const { unmount } = renderHook(() => useSandbox(true), { wrapper });

    await waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    unmount();
  });

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

  it('updates both percentage and message while VM creation streams', async () => {
    let finish!: () => void;
    mocks.setupStream.mockImplementationOnce(async (onProgress: (value: { percent: number; message: string }) => void) => {
      onProgress({ percent: 0, message: 'Initializing VM provisioning' });
      onProgress({ percent: 40, message: 'Creating Incus VM' });
      await new Promise<void>((resolve) => { finish = resolve; });
      onProgress({ percent: 100, message: 'VM is ready' });
    });
    mocks.get.mockResolvedValueOnce({ provisioned: true, data: null });

    const { result, unmount } = renderHook(() => useSandbox(false));
    let creation!: Promise<void>;
    act(() => { creation = result.current.createSandbox(); });

    await waitFor(() => {
      expect(result.current.setupRunning).toBe(true);
      expect(result.current.setupProgress).toBe(40);
      expect(result.current.setupMessage).toBe('Creating Incus VM');
    });
    finish();
    await act(async () => { await creation; });

    expect(result.current.setupProgress).toBe(100);
    expect(result.current.setupMessage).toBe('VM is ready');
    expect(result.current.setupRunning).toBe(false);
    unmount();
  });
});
