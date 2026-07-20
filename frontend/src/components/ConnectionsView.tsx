import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, Check, ChevronDown, X } from 'lucide-react';
import { ProviderBrandIcon } from './common';
import type { ConnectionProvider } from '../types';
import type { ProviderTestOutcome } from '../hooks/useConnections';

export function ConnectionsView({
  providers,
  keyProviderId,
  pendingId,
  onSelectKeyProvider,
  onConnect,
  onDisconnect,
  onTest,
  onSaveKey,
  onClose,
}: {
  providers: ConnectionProvider[];
  keyProviderId: string;
  pendingId: string | null;
  onSelectKeyProvider: (id: string) => void;
  onConnect: (id: string) => void;
  onDisconnect: (id: string) => void;
  onTest: (id: string) => Promise<ProviderTestOutcome> | void;
  onSaveKey: (id: string, key: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState('');
  const [testResults, setTestResults] = useState<Record<string, ProviderTestOutcome>>({});
  const apiKeyProviders = providers.filter((p) => p.connection_mode === 'api-key');
  const selectedKeyProvider = providers.find((p) => p.id === keyProviderId);

  const runTest = async (id: string) => {
    setTestResults((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    const result = await onTest(id);
    if (result) setTestResults((current) => ({ ...current, [id]: result }));
  };

  return (
    <div className="connections-view">
      <header className="conn-topbar">
        <div>
          <h1>{t('connections.title')}</h1>
          <p>{t('connections.subtitle')}</p>
        </div>
        <button className="icon-button" onClick={onClose} title={t('common.close')}>
          <X size={17} />
        </button>
      </header>

      <div className="conn-scroll">
        <div className="conn-grid">
          {providers.map((p) => (
            <article className="conn-card" key={p.id}>
              <div className="conn-card-head">
                <ProviderBrandIcon brand={p.brand} />
                <strong>{p.display_name}</strong>
                <span className={p.connected ? 'conn-badge ok' : 'conn-badge'}>
                  {p.connected ? (p.last_test_status === 'healthy' ? t('connections.healthy') : t('connections.connected')) : t('connections.notConnected')}
                </span>
              </div>
              <p className="conn-desc">{p.description}</p>
              {p.connected ? (
                <div className="conn-actions">
                  <button
                    className="conn-btn ghost"
                    disabled={pendingId === p.id}
                    onClick={() => void runTest(p.id)}
                  >
                    {pendingId === p.id ? (
                      <>
                        <span className="async-spinner conn-spinner" />
                        {t('connections.testing')}
                      </>
                    ) : (
                      <>
                        <Activity size={14} />
                        {t('common.test')}
                      </>
                    )}
                  </button>
                  <button
                    className="conn-btn danger"
                    disabled={pendingId === p.id}
                    onClick={() => onDisconnect(p.id)}
                  >
                    {t('connections.disconnect')}
                  </button>
                </div>
              ) : (
                <button className="conn-btn primary" onClick={() => onConnect(p.id)}>
                  {p.connection_mode === 'api-key' ? t('connections.addApiKey') : t('connections.authenticate')}
                </button>
              )}
              {p.connected && pendingId !== p.id && testResults[p.id] && (() => {
                const result = testResults[p.id];
                const headline = result.message
                  || (result.ok ? t('connections.testHealthy') : result.status === 'error' ? t('connections.testError') : t('connections.testUnhealthy'));
                return (
                  <div className={`conn-test-result ${result.ok ? 'ok' : 'fail'}`}>
                    <div className="conn-test-head">
                      {result.ok ? <Check size={14} /> : <X size={14} />}
                      <strong>{headline}</strong>
                      {result.status && <span className="conn-test-status">{result.status}</span>}
                    </div>
                    {result.capabilities && result.capabilities.length > 0 && (
                      <div className="conn-test-caps">
                        <span className="conn-test-caps-label">{t('connections.capabilities')}</span>
                        {result.capabilities.map((cap) => (
                          <span key={cap} className="conn-cap">{cap.replaceAll('_', ' ')}</span>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })()}
            </article>
          ))}
        </div>

        <section className="conn-keypanel">
          <strong>{t('connections.addKeyTitle')}</strong>
          <p>{t('connections.addKeyDesc', { env: selectedKeyProvider?.environment_variable ?? 'PROVIDER_API_KEY' })}</p>
          <div className="conn-keyform">
            <div className="conn-select">
              <select value={keyProviderId} onChange={(e) => onSelectKeyProvider(e.target.value)}>
                {apiKeyProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} />
            </div>
            <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-… / AIza… / sk-ant-…" type="password" />
            <button
              className="conn-btn primary"
              disabled={!apiKey.trim()}
              onClick={() => {
                onSaveKey(keyProviderId, apiKey);
                setApiKey('');
              }}
            >
              {t('common.save')}
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
