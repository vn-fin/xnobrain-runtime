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
  const checked = useRef(false);

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
      checked.current = true;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Unable to load sandbox');
      setStatus('error');
    } finally {
      refreshing.current = false;
    }
  }, []);

  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    let reconnectTimer: number | undefined;
    let received = false;

    const connect = async () => {
      if (!received) setStatus('loading');
      try {
        await sandboxApi.stream((result) => {
          checked.current = true;
          received = true;
          setData(result.data);
          setProvisioned(result.provisioned);
          setError('');
          setStatus('ready');
        }, controller.signal);
      } catch (cause) {
        if (controller.signal.aborted) return;
        if (!received) {
          setError(cause instanceof Error ? cause.message : 'Unable to stream sandbox statistics');
          setStatus('error');
        }
      }
      if (!controller.signal.aborted) reconnectTimer = window.setTimeout(() => void connect(), 1_000);
    };

    void connect();
    return () => {
      controller.abort();
      if (reconnectTimer !== undefined) window.clearTimeout(reconnectTimer);
    };
  }, [active]);

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
