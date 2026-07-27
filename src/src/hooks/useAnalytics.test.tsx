import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { UsageSummary } from '../api/analytics';
import { useAnalytics } from './useAnalytics';

const summary = {
  generated_at: '2026-07-27T00:00:00Z',
  agents: [],
} as unknown as UsageSummary;

const mocks = vi.hoisted(() => ({
  usage: vi.fn(async () => summary),
  setBudget: vi.fn(),
}));

vi.mock('../api/analytics', () => ({
  analyticsApi: {
    usage: mocks.usage,
    setBudget: mocks.setBudget,
  },
}));

describe('useAnalytics', () => {
  afterEach(() => vi.clearAllMocks());

  it('reuses global agent summaries and makes only the usage request', async () => {
    const agents = [
      { id: 'one', title: 'News Summary', name: 'one' },
      { id: 'two', title: '', name: 'Research' },
    ];
    const { result } = renderHook(() => useAnalytics(true, agents));

    await waitFor(() => expect(result.current.status).toBe('ready'));

    expect(mocks.usage).toHaveBeenCalledTimes(1);
    expect(result.current.available).toEqual([
      { agent_id: 'one', display_name: 'News Summary' },
      { agent_id: 'two', display_name: 'Research' },
    ]);
  });
});
