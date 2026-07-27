import { StrictMode, type PropsWithChildren } from 'react';
import { renderHook, waitFor } from '@testing-library/react';
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
});
