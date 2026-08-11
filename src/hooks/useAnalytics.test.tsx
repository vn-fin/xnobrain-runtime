import { StrictMode, type PropsWithChildren } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAnalytics } from './useAnalytics';

const mocks = vi.hoisted(() => ({
  usage: vi.fn(async () => ({
    generated_at: '2026-07-27T00:00:00Z',
    agents: [],
    by_model: [],
    by_provider: [],
    series: [],
  })),
  setBudget: vi.fn(),
}));

vi.mock('../api/analytics', () => ({
  analyticsApi: {
    usage: mocks.usage,
    setBudget: mocks.setBudget,
  },
}));

describe('useAnalytics', () => {
  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('reuses global agents and loads the combined analytics response once', async () => {
    const agents = [
      { id: 'one', title: 'News Summary', name: 'one' },
      { id: 'two', title: '', name: 'Research' },
    ];
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;
    const { result } = renderHook(() => useAnalytics(true, agents), { wrapper });

    await waitFor(() => expect(result.current.status).toBe('ready'));

    expect(mocks.usage).toHaveBeenCalledTimes(1);
    expect(result.current.available).toEqual([
      { agent_id: 'one', display_name: 'News Summary' },
      { agent_id: 'two', display_name: 'Research' },
    ]);
  });

  it('reports aggregate progress and reuses the active refresh request', async () => {
    let resolveUsage!: (value: { generated_at: string; agents: never[]; by_model: never[]; by_provider: never[]; series: never[] }) => void;
    mocks.usage.mockImplementationOnce(() => new Promise((resolve) => { resolveUsage = resolve; }));

    const { result } = renderHook(() => useAnalytics(true, []));
    await waitFor(() => expect(result.current.status).toBe('loading'));
    expect(result.current.progress).toEqual({ completed: 0, total: 1 });

    const first = result.current.refresh();
    const second = result.current.refresh();
    expect(first).toBe(second);
    expect(mocks.usage).toHaveBeenCalledTimes(1);

    await act(async () => resolveUsage({ generated_at: '2026-07-27T00:00:00Z', agents: [], by_model: [], by_provider: [], series: [] }));
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.progress).toEqual({ completed: 1, total: 1 });
  });

  it('cancels an obsolete request and loads the newest controls once', async () => {
    let firstSignal: AbortSignal | undefined;
    let resolveLatest!: (value: { generated_at: string; agents: never[]; by_model: never[]; by_provider: never[]; series: never[] }) => void;
    mocks.usage
      .mockImplementationOnce((_query, signal) => {
        firstSignal = signal;
        return new Promise((_resolve, reject) => signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
      })
      .mockImplementationOnce(() => new Promise((resolve) => { resolveLatest = resolve; }));

    const { result } = renderHook(() => useAnalytics(true, []));
    await waitFor(() => expect(result.current.status).toBe('loading'));

    act(() => result.current.setControls({ agents: [], days: 7, bucket: 'day' }));
    await waitFor(() => expect(mocks.usage).toHaveBeenCalledTimes(2));
    expect(firstSignal?.aborted).toBe(true);
    expect(mocks.usage.mock.calls[1][0]).toEqual({ agents: [], days: 7, bucket: 'day' });

    await act(async () => resolveLatest({ generated_at: '2026-07-28T00:00:00Z', agents: [], by_model: [], by_provider: [], series: [] }));
    await waitFor(() => expect(result.current.status).toBe('ready'));
  });
});
