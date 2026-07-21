import { useCallback, useEffect, useState } from 'react';
import { communityApi } from '../api/community';
import type { AsyncStatus, CommunitySkill, CommunityStats } from '../types';

const EMPTY_STATS: CommunityStats = { totalSkills: 0, totalAuthors: 0, totalInstalls: 0 };

/**
 * Loads the community skills catalog through the community API seam
 * (`src/api/community.ts`). Today that seam returns smoke data; when the real
 * catalog service is wired up only the API module changes, not this hook.
 */
export function useCommunitySkills() {
  const [skills, setSkills] = useState<CommunitySkill[]>([]);
  const [stats, setStats] = useState<CommunityStats>(EMPTY_STATS);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');

  const load = useCallback(async (signal?: AbortSignal) => {
    setStatus('loading');
    setError('');
    try {
      const catalog = await communityApi.list(signal);
      if (signal?.aborted) return;
      setSkills(catalog.skills);
      setStats(catalog.stats);
      setStatus('ready');
    } catch (cause) {
      if (signal?.aborted || (cause instanceof DOMException && cause.name === 'AbortError')) return;
      setError(cause instanceof Error ? cause.message : 'Unable to load community skills');
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const refresh = useCallback(() => void load(), [load]);

  return { skills, stats, status, error, refresh };
}
