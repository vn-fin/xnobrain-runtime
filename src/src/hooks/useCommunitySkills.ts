import { useCallback, useEffect, useState } from 'react';
import { communityApi } from '../api/community';
import type { AsyncStatus, CommunitySkill, CommunityStats } from '../types';

const EMPTY_STATS: CommunityStats = { totalSkills: 0, totalAuthors: 0, totalInstalls: 0 };

export function useCommunitySkills(query = '', category = 'all', enabled = true) {
  const [skills, setSkills] = useState<CommunitySkill[]>([]);
  const [stats, setStats] = useState<CommunityStats>(EMPTY_STATS);
  const [status, setStatus] = useState<AsyncStatus>(enabled ? 'loading' : 'ready');
  const [error, setError] = useState('');
  const [installError, setInstallError] = useState('');

  const load = useCallback(async (signal?: AbortSignal) => {
    if (!enabled) {
      setSkills([]);
      setStats(EMPTY_STATS);
      setStatus('ready');
      setError('');
      return;
    }
    setStatus('loading');
    setError('');
    try {
      const catalog = await communityApi.list(query, category, signal);
      if (signal?.aborted) return;
      setSkills(catalog.skills);
      setStats(catalog.stats);
      setStatus('ready');
    } catch (cause) {
      if (signal?.aborted || (cause instanceof DOMException && cause.name === 'AbortError')) return;
      setError(cause instanceof Error ? cause.message : 'Unable to load Enterprise skills');
      setStatus('error');
    }
  }, [category, enabled, query]);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => void load(controller.signal), query.trim() ? 250 : 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [load, query]);

  const refresh = useCallback(() => void load(), [load]);
  const install = useCallback(async (skillId: string): Promise<CommunitySkill | null> => {
    setInstallError('');
    try {
      return await communityApi.install(skillId);
    } catch (cause) {
      setInstallError(cause instanceof Error ? cause.message : 'Unable to install Enterprise skill');
      return null;
    }
  }, []);

  return { skills, stats, status, error, installError, refresh, install };
}
