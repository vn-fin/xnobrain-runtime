import { StrictMode, type PropsWithChildren } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useAnalytics } from './useAnalytics';

const mocks = vi.hoisted(() => ({
  overview: vi.fn(async () => ({
    generated_at: '2026-07-27T00:00:00Z',
    agents: [],
  })),
  models: vi.fn(async () => ({ by_model: [], by_provider: [] })),
  timeseries: vi.fn(async () => ({ series: [] })),
  setBudget: vi.fn(),
}));

vi.mock('../api/analytics', () => ({
  analyticsApi: {
    overview: mocks.overview,
    models: mocks.models,
    timeseries: mocks.timeseries,
    setBudget: mocks.setBudget,
  },
}));

describe('useAnalytics', () => {
  afterEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('reuses global agents and loads each analytics section once in parallel', async () => {
    const agents = [
      { id: 'one', title: 'News Summary', name: 'one' },
      { id: 'two', title: '', name: 'Research' },
    ];
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;
    const { result } = renderHook(() => useAnalytics(true, agents), { wrapper });

    await waitFor(() => expect(result.current.status).toBe('ready'));

    expect(mocks.overview).toHaveBeenCalledTimes(1);
    expect(mocks.models).toHaveBeenCalledTimes(1);
    expect(mocks.timeseries).toHaveBeenCalledTimes(1);
    expect(result.current.available).toEqual([
      { agent_id: 'one', display_name: 'News Summary' },
      { agent_id: 'two', display_name: 'Research' },
    ]);
  });

  it('reports aggregate progress and reuses the active refresh request', async () => {
    let resolveOverview!: (value: { generated_at: string; agents: never[] }) => void;
    let resolveModels!: (value: { by_model: never[]; by_provider: never[] }) => void;
    let resolveTimeseries!: (value: { series: never[] }) => void;
    mocks.overview.mockImplementationOnce(() => new Promise((resolve) => { resolveOverview = resolve; }));
    mocks.models.mockImplementationOnce(() => new Promise((resolve) => { resolveModels = resolve; }));
    mocks.timeseries.mockImplementationOnce(() => new Promise((resolve) => { resolveTimeseries = resolve; }));

    const { result } = renderHook(() => useAnalytics(true, []));
    await waitFor(() => expect(result.current.status).toBe('loading'));
    expect(result.current.progress).toEqual({ completed: 0, total: 3 });

    const first = result.current.refresh();
    const second = result.current.refresh();
    expect(first).toBe(second);
    expect(mocks.overview).toHaveBeenCalledTimes(1);
    expect(mocks.models).toHaveBeenCalledTimes(1);
    expect(mocks.timeseries).toHaveBeenCalledTimes(1);

    await act(async () => resolveOverview({ generated_at: '2026-07-27T00:00:00Z', agents: [] }));
    await waitFor(() => expect(result.current.progress.completed).toBe(1));
    await act(async () => {
      resolveModels({ by_model: [], by_provider: [] });
      resolveTimeseries({ series: [] });
    });
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.progress).toEqual({ completed: 3, total: 3 });
  });
});
