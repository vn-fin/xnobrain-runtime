import { useCallback, useEffect, useState } from 'react';
import { checkpointsApi } from '../api/checkpoints';
import type { Checkpoint, CheckpointDiff, CheckpointStatus, FileVersion } from '../types';

export function useCheckpoints(agentId: string, active = true) {
  const [status, setStatus] = useState<CheckpointStatus>();
  const [items, setItems] = useState<Checkpoint[]>([]);
  const [diff, setDiff] = useState<CheckpointDiff>();
  const [versions, setVersions] = useState<{ path: string; current: { exists: boolean; size?: number; modified_at?: string }; items: FileVersion[] }>();
  const [loading, setLoading] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    if (!agentId) return;
    setLoading(true); setError('');
    try {
      const nextStatus = await checkpointsApi.status(agentId);
      setStatus(nextStatus);
      setItems(nextStatus.enabled && nextStatus.available ? await checkpointsApi.list(agentId) : []);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to load restore points'); }
    finally { setLoading(false); }
  }, [agentId]);

  useEffect(() => { setStatus(undefined); setItems([]); setDiff(undefined); setVersions(undefined); if (active) void refresh(); }, [active, refresh]);

  const select = useCallback(async (id: string) => {
    setLoading(true); setError('');
    try { setDiff(await checkpointsApi.diff(agentId, id)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to preview restore point'); }
    finally { setLoading(false); }
  }, [agentId]);

  const loadVersions = useCallback(async (path: string) => {
    setLoading(true); setError('');
    try { setVersions(await checkpointsApi.versions(agentId, path)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to load version history'); }
    finally { setLoading(false); }
  }, [agentId]);

  const restore = useCallback(async (id: string, path?: string) => {
    setPending(true); setError('');
    try { await checkpointsApi.restore(agentId, id, path); await refresh(); if (path) await loadVersions(path); await select(id); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Unable to restore workspace'); throw cause; }
    finally { setPending(false); }
  }, [agentId, loadVersions, refresh, select]);

  return { status, items, diff, versions, loading, pending, error, refresh, select, loadVersions, restore };
}
