import { useCallback, useEffect, useState } from 'react';
import { sandboxApi } from '../api/sandbox';
import type { AsyncStatus, SandboxData } from '../types';

export function useSandbox() {
  const [data, setData] = useState<SandboxData | null>(null);
  const [provisioned, setProvisioned] = useState(false);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [setupRunning, setSetupRunning] = useState(false);
  const [setupProgress, setSetupProgress] = useState(0);

  const refresh = useCallback(async () => {
    setStatus('loading');
    setError('');
    try {
      const result = await sandboxApi.get();
      setData(result.data);
      setProvisioned(result.provisioned);
      setStatus('ready');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load sandbox');
      setStatus('error');
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  const createSandbox = async () => {
    setSetupRunning(true);
    setSetupProgress(0);
    setError('');
    try {
      await sandboxApi.setupStream((percent) => setSetupProgress(percent));
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to create sandbox');
      setStatus('error');
    } finally {
      setSetupRunning(false);
    }
  };

  return { data, provisioned, status, error, setupRunning, setupProgress, createSandbox, refresh };
}
