import { useCallback, useEffect, useRef, useState } from 'react';
import { sandboxApi } from '../api/sandbox';
import type { AsyncStatus, SandboxData } from '../types';

export function useSandbox(active: boolean) {
  const [data, setData] = useState<SandboxData | null>(null);
  const [provisioned, setProvisioned] = useState(false);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [setupRunning, setSetupRunning] = useState(false);
  const [setupProgress, setSetupProgress] = useState(0);
  const refreshing = useRef(false);

  const refresh = useCallback(async (silent = false) => {
    if (refreshing.current) return;
    refreshing.current = true;
    if (!silent) setStatus('loading');
    setError('');
    try {
      const result = await sandboxApi.get();
      setData(result.data);
      setProvisioned(result.provisioned);
      setStatus('ready');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load sandbox');
      setStatus('error');
    } finally {
      refreshing.current = false;
    }
  }, []);

  useEffect(() => {
    if (!active) return undefined;
    void refresh(false);
    const timer = window.setInterval(() => void refresh(true), 5_000);
    return () => window.clearInterval(timer);
  }, [active, refresh]);

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

  return { data, provisioned, status, error, setupRunning, setupProgress, createSandbox, refresh: () => refresh(false) };
}
