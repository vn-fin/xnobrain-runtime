import { StrictMode, type PropsWithChildren } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useTeams } from './useTeams';

const mocks = vi.hoisted(() => ({
  list: vi.fn(),
  listRuns: vi.fn(),
  getRun: vi.fn(),
}));

vi.mock('../api/teams', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/teams')>();
  return {
    ...actual,
    teamsApi: {
      ...actual.teamsApi,
      list: mocks.list,
      listRuns: mocks.listRuns,
      getRun: mocks.getRun,
    },
  };
});

describe('useTeams request deduplication', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads teams once when StrictMode replays the activation effect', async () => {
    mocks.list.mockResolvedValue([]);
    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;

    const { result } = renderHook(() => useTeams(true), { wrapper });

    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(mocks.list).toHaveBeenCalledTimes(1);
  });

  it('shares concurrent run-list requests for the same team', async () => {
    mocks.list.mockResolvedValue([]);
    mocks.listRuns.mockResolvedValue([]);
    const { result } = renderHook(() => useTeams(false));

    await act(async () => {
      await Promise.all([
        result.current.loadRuns('team-one'),
        result.current.loadRuns('team-one'),
      ]);
    });

    expect(mocks.listRuns).toHaveBeenCalledTimes(1);
    expect(mocks.listRuns).toHaveBeenCalledWith('team-one');
    expect(result.current.runsStatus).toBe('ready');
  });

  it('shares concurrent run-detail requests for the same execution', async () => {
    mocks.list.mockResolvedValue([]);
    mocks.getRun.mockResolvedValue({ id: 'run-one', team_id: 'team-one', status: 'completed' });
    const { result } = renderHook(() => useTeams(false));

    await act(async () => {
      await Promise.all([
        result.current.openRun('team-one', 'run-one'),
        result.current.openRun('team-one', 'run-one'),
      ]);
    });

    expect(mocks.getRun).toHaveBeenCalledTimes(1);
    expect(mocks.getRun).toHaveBeenCalledWith('team-one', 'run-one');
  });
});
