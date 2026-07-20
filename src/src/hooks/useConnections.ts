import { useCallback, useEffect, useState } from 'react';
import { providersApi, type ProviderTestResult } from '../api/providers';
import type { AsyncStatus, ConnectionProvider, ProviderConnectInfo } from '../types';
import { providerConnectNeedsText } from '../utils/providers';
import {
  closeProviderAuthPopup,
  navigateProviderAuthPopup,
  openProviderAuthPopup,
} from '../utils/providerAuth';

/** Result of a provider connection test, plus a normalized `ok` flag. */
export type ProviderTestOutcome = ProviderTestResult & { ok: boolean };
export function useConnections() {
  const [connections, setConnections] = useState<ConnectionProvider[]>([]);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [authProviderId, setAuthProviderId] = useState<string | null>(null);
  const [authInfo, setAuthInfo] = useState<ProviderConnectInfo | null>(null);
  const [keyProviderId, setKeyProviderId] = useState('');
  const [pendingId, setPendingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setStatus('loading');
    setError('');
    try {
      const data = await providersApi.list();
      setConnections(data);
      setKeyProviderId((current) => current || data.find((provider) => provider.connection_mode === 'api-key')?.id || data[0]?.id || '');
      setStatus('ready');
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not load providers.');
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // CLI/device-code authentication can complete in the provider popup, so
  // poll the providers list. API-key providers never enter this flow: they only
  // call the backend when Save is clicked. Ignore unchanged poll responses to
  // avoid needlessly rerendering (and visually flashing) the connection UI.
  useEffect(() => {
    if (!authProviderId || !authInfo || authInfo.connection_mode === 'api-key') return;

    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const currentProvider = connections.find((provider) => provider.id === authProviderId);
    if (currentProvider && providerConnectNeedsText(currentProvider, authInfo)) return;
    let lastStatus = currentProvider?.status;
    let lastConnected = currentProvider?.connected ?? false;

    const poll = async () => {
      try {
        const data = await providersApi.list();
        if (!active) return;

        const provider = data.find((item) => item.id === authProviderId);
        if (provider && (provider.status !== lastStatus || provider.connected !== lastConnected)) {
          lastStatus = provider.status;
          lastConnected = provider.connected;
          setConnections(data);
        }

        if (provider?.connected) {
          setAuthProviderId(null);
          setAuthInfo(null);
          return;
        }
      } catch {
        // Authentication may still be pending. Keep the current UI and retry.
      }

      if (active) timer = setTimeout(() => void poll(), 1_000);
    };

    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [authProviderId, authInfo, connections]);

  const connect = async (id: string) => {
    const provider = connections.find((item) => item.id === id);
    if (!provider) return;
    if (provider.connection_mode === 'api-key') {
      setKeyProviderId(id);
      return;
    }
    openProviderAuthPopup();
    setPendingId(id);
    setError('');
    try {
      const info = await providersApi.connect(id);
      setAuthInfo(info);
      setAuthProviderId(id);
      navigateProviderAuthPopup(info.verification_url || info.login_url);
    } catch (value) {
      // Keep the popup open on error so the user can still copy the callback
      // URL from it and paste it manually, then close the window themselves.
      setError(value instanceof Error ? value.message : 'Could not connect provider.');
    } finally {
      setPendingId(null);
    }
  };

  const closeAuth = () => {
    closeProviderAuthPopup();
    setAuthProviderId(null);
    setAuthInfo(null);
  };

  const submitAuth = async (id: string, text: string): Promise<boolean> => {
    setPendingId(id);
    setError('');
    try {
      await providersApi.submitAuth(id, text);
      closeAuth();
      await refresh();
      return true;
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not submit provider authentication.');
      return false;
    } finally {
      setPendingId(null);
    }
  };

  const disconnect = async (id: string) => {
    setPendingId(id);
    try {
      await providersApi.disconnect(id);
      await refresh();
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not disconnect provider.');
    } finally {
      setPendingId(null);
    }
  };

  const test = async (id: string): Promise<ProviderTestOutcome> => {
    setPendingId(id);
    try {
      const result = await providersApi.test(id);
      const ok = result.healthy === true || result.status === 'ok' || result.status === 'healthy';
      setConnections((current) => current.map((provider) => provider.id === id
        ? { ...provider, last_test_status: ok ? 'healthy' : 'unhealthy' }
        : provider));
      return { ...result, ok };
    } catch (value) {
      const message = value instanceof Error ? value.message : 'Could not test provider.';
      setError(message);
      return { ok: false, healthy: false, status: 'error', message };
    } finally {
      setPendingId(null);
    }
  };

  const saveKey = async (id: string, key: string) => {
    if (!key.trim()) return;
    setPendingId(id);
    try {
      await providersApi.saveKey(id, key.trim());
      await refresh();
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not save provider key.');
    } finally {
      setPendingId(null);
    }
  };

  // Fetch the available models for a provider from its connect status
  // (data.provider.available_models), sorted by name.
  const loadModels = async (id: string): Promise<{ defaultModel: string; models: string[] }> => {
    return providersApi.connectModels(id);
  };

  // Initiate a provider connection (returns login info such as a URL/user code).
  // Used by the onboarding flow, which shares the popup and callback helpers.
  const startConnect = async (id: string): Promise<ProviderConnectInfo | null> => {
    const provider = connections.find((item) => item.id === id);
    if (provider?.connection_mode !== 'api-key') openProviderAuthPopup();
    setPendingId(id);
    setError('');
    try {
      const info = await providersApi.connect(id);
      navigateProviderAuthPopup(info.verification_url || info.login_url);
      return info;
    } catch (value) {
      // Keep the popup open on error so the user can copy the callback URL and
      // paste it manually, then close the window themselves.
      setError(value instanceof Error ? value.message : 'Could not start provider connection.');
      return null;
    } finally {
      setPendingId(null);
    }
  };

  // Poll the provider connect status (GET .../connect) and refresh the list.
  // Returns whether the provider is now connected. This covers device-code
  // providers and OAuth flows completed by the runtime itself.
  const checkConnect = async (id: string): Promise<boolean> => {
    try {
      await providersApi.connectStatus(id);
    } catch {
      /* status probe is best-effort */
    }
    try {
      const data = await providersApi.list();
      setConnections(data);
      const connected = Boolean(data.find((provider) => provider.id === id)?.connected);
      if (connected) closeProviderAuthPopup();
      return connected;
    } catch {
      return false;
    }
  };

  return {
    connections, status, error, refresh, pendingId,
    authProviderId, authInfo, setAuthProviderId, closeAuth,
    keyProviderId, setKeyProviderId,
    connect, submitAuth, disconnect, test, saveKey, loadModels, startConnect, checkConnect,
  };
}
