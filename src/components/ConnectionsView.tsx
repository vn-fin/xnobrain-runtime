import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Activity, ArrowDown, ArrowUp, Check, ChevronDown, ChevronRight, Plus, Trash2, X,
} from 'lucide-react';
import { ProviderBrandIcon } from './common';
import { ConfirmDialog } from './modals';
import type { ConnectionProvider } from '../types';
import type { ProviderTestOutcome } from '../hooks/useConnections';
import type { ProviderConnection, ConnectionUsage } from '../api/providers';
import { providerUsesAuthFlow, providerUsesInlineApiKey } from '../utils/providers';

export type AccountProps = {
  connectionsByProvider: Record<string, ProviderConnection[]>;
  usageByConnection: Record<string, ConnectionUsage>;
  rowPendingId: string | null;
  onLoadConnections: (providerId: string) => void;
  onAddAccount: (providerId: string, input?: { api_key: string; name?: string }) => void;
  onSetAccountActive: (providerId: string, connectionId: string, active: boolean) => void;
  onReorderAccount: (providerId: string, connectionId: string, direction: 'up' | 'down') => void;
  onTestAccount: (providerId: string, connectionId: string) => void;
  onRemoveAccount: (providerId: string, connectionId: string) => void;
  onLoadAccountUsage: (providerId: string, connectionId: string) => void;
};

function usagePercent(usage: ConnectionUsage | undefined): number | null {
  if (!usage || !usage.available || usage.quotas.length === 0) return null;
  return Math.max(0, Math.min(100, Math.min(...usage.quotas.map((q) => q.remaining_percent))));
}

function statusDotClass(testStatus: string): string {
  if (testStatus === 'valid' || testStatus === 'healthy') return 'ok';
  if (testStatus === 'unknown' || !testStatus) return 'unknown';
  return 'fail';
}

function AccountRow({
  providerId, row, usage, pending, actions, onRequestRemove,
}: {
  providerId: string;
  row: ProviderConnection;
  usage: ConnectionUsage | undefined;
  pending: boolean;
  actions: AccountProps;
  onRequestRemove: (providerId: string, connectionId: string, label: string) => void;
}) {
  const { t } = useTranslation();
  const percent = usagePercent(usage);
  const label = row.email || row.name || row.id;
  return (
    <div className={`conn-account-row${row.active ? '' : ' inactive'}`}>
      <span className={`conn-dot ${statusDotClass(row.test_status)}`} title={row.last_error || row.test_status} />
      <span className="conn-account-label" title={label}>{label}</span>
      <span className="conn-account-tag">{row.auth_type}</span>
      <label className="conn-account-active">
        <input
          type="checkbox"
          checked={row.active}
          disabled={pending}
          onChange={(e) => actions.onSetAccountActive(providerId, row.id, e.target.checked)}
        />
        {t('connections.accountActive', { defaultValue: 'Active' })}
      </label>
      <button className="conn-iconbtn" disabled={pending} title={t('connections.moveUp', { defaultValue: 'Move up' })}
        onClick={() => actions.onReorderAccount(providerId, row.id, 'up')}>
        <ArrowUp size={13} />
      </button>
      <button className="conn-iconbtn" disabled={pending} title={t('connections.moveDown', { defaultValue: 'Move down' })}
        onClick={() => actions.onReorderAccount(providerId, row.id, 'down')}>
        <ArrowDown size={13} />
      </button>
      <button className="conn-iconbtn" disabled={pending} title={t('common.test', { defaultValue: 'Test' })}
        onClick={() => actions.onTestAccount(providerId, row.id)}>
        <Activity size={13} />
      </button>
      <span className="conn-usage-bar" title={percent === null ? t('connections.usageUnavailable', { defaultValue: 'Usage unavailable' }) : `${percent}%`}>
        {percent === null
          ? <span className="conn-usage-empty">—</span>
          : <span className="conn-usage-fill" style={{ width: `${percent}%` }} />}
      </span>
      <button className="conn-iconbtn danger" disabled={pending} title={t('connections.removeAccount', { defaultValue: 'Remove account' })}
        onClick={() => onRequestRemove(providerId, row.id, label)}>
        <X size={13} />
      </button>
    </div>
  );
}

function AddAccount({ provider, actions }: { provider: ConnectionProvider; actions: AccountProps }) {
  const { t } = useTranslation();
  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  if (providerUsesAuthFlow(provider)) {
    return (
      <button className="conn-btn ghost conn-add" onClick={() => actions.onAddAccount(provider.id)}>
        <Plus size={13} /> {t('connections.addAccount', { defaultValue: 'Add account' })}
      </button>
    );
  }
  return (
    <div className="conn-add-form">
      <input type="text" placeholder={t('connections.accountName', { defaultValue: 'Label (optional)' })}
        value={name} onChange={(e) => setName(e.target.value)} />
      <input type="password" placeholder="sk-… / AIza… / sk-ant-…"
        value={key} onChange={(e) => setKey(e.target.value)} />
      <button className="conn-btn primary" disabled={!key.trim()}
        onClick={() => { actions.onAddAccount(provider.id, { api_key: key, name }); setKey(''); setName(''); }}>
        <Plus size={13} /> {t('connections.addAccount', { defaultValue: 'Add account' })}
      </button>
    </div>
  );
}

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
  embedded = false,
  ...accounts
}: {
  providers: ConnectionProvider[];
  keyProviderId: string;
  pendingId: string | null;
  onSelectKeyProvider: (id: string) => void;
  onConnect: (id: string) => void;
  onDisconnect: (id: string) => void;
  onTest: (id: string) => Promise<ProviderTestOutcome> | void;
  onSaveKey: (id: string, key: string, baseUrl?: string) => void;
  onClose: () => void;
  embedded?: boolean;
} & AccountProps) {
  const { t } = useTranslation();
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const keyPanelRef = useRef<HTMLElement>(null);
  const keyInputRef = useRef<HTMLInputElement>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [testResults, setTestResults] = useState<Record<string, ProviderTestOutcome>>({});
  const [confirmation, setConfirmation] = useState<{
    title: string;
    message: string;
    confirmLabel: string;
    action: () => void;
  } | null>(null);
  const apiKeyProviders = providers.filter(providerUsesInlineApiKey);
  const providerGroups = [
    {
      id: 'subscriptions',
      title: t('connections.subscriptions', { defaultValue: 'Subscriptions' }),
      description: t('connections.subscriptionsDesc', { defaultValue: 'Sign in with an existing coding subscription.' }),
      providers: providers.filter((provider) => !providerUsesInlineApiKey(provider)),
    },
    {
      id: 'api-keys',
      title: t('connections.apiKeys', { defaultValue: 'API keys' }),
      description: t('connections.apiKeysDesc', { defaultValue: 'Connect providers with a directly managed API key.' }),
      providers: apiKeyProviders,
    },
  ].filter((group) => group.providers.length > 0);
  const selectedKeyProvider = apiKeyProviders.find((p) => p.id === keyProviderId) ?? apiKeyProviders[0];
  const selectedBaseUrl = baseUrl || selectedKeyProvider?.base_url || '';

  const beginConnect = (provider: ConnectionProvider) => {
    if (!providerUsesInlineApiKey(provider)) {
      onConnect(provider.id);
      return;
    }
    onSelectKeyProvider(provider.id);
    setBaseUrl('');
    keyPanelRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    keyInputRef.current?.focus();
  };

  const runTest = async (id: string) => {
    setTestResults((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    const result = await onTest(id);
    if (result) setTestResults((current) => ({ ...current, [id]: result }));
  };

  const toggleExpand = (id: string) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
        accounts.onLoadConnections(id);
      }
      return next;
    });
  };

  return (
    <div className={embedded ? 'connections-view embedded' : 'connections-view'}>
      <header className="conn-topbar">
        <div>
          <h1>{t('connections.title')}</h1>
          <p>{t('connections.subtitle')}</p>
        </div>
        {!embedded && <button className="icon-button" onClick={onClose} title={t('common.close')}>
          <X size={17} />
        </button>}
      </header>

      <div className="conn-scroll">
        <div className="conn-groups">
          {providerGroups.map((group) => <section className="conn-group" key={group.id} aria-labelledby={`connector-group-${group.id}`}>
            <header className="conn-group-heading">
              <div><h2 id={`connector-group-${group.id}`}>{group.title}</h2><p>{group.description}</p></div>
              <span>{group.providers.length}</span>
            </header>
            <div className="conn-grid">
          {group.providers.map((p) => {
            const rows = accounts.connectionsByProvider[p.id] ?? [];
            const count = p.connection_count ?? (rows.length || (p.connected ? 1 : 0));
            const isOpen = expanded.has(p.id);
            const noAuth = p.connection_mode === 'no-auth';
            return (
            <article className="conn-card" key={p.id}>
              <div className="conn-card-head">
                <ProviderBrandIcon brand={p.brand} />
                <strong>{p.display_name}</strong>
                <span className={p.connected ? 'conn-badge ok' : 'conn-badge'}>
                  {noAuth
                    ? p.connected
                      ? t('common.available', { defaultValue: 'available' })
                      : t('common.unavailable', { defaultValue: 'unavailable' })
                    : p.connected
                    ? t('connections.connectedCount', { defaultValue: 'connected · {{count}} account(s)', count })
                    : p.free_models_available
                    ? t('common.available', { defaultValue: 'available' })
                    : t('connections.notConnected')}
                </span>
              </div>
              <p className="conn-desc">{p.description}</p>
              {noAuth ? (
                <div className={`conn-no-auth${p.connected ? '' : ' unavailable'}`}>
                  {p.connected ? <Check size={14} /> : <X size={14} />}
                  <span>{p.connected
                    ? t('connections.noAuthRequired', { defaultValue: 'No API key required' })
                    : t('connections.routerUnavailable', { defaultValue: 'Provider runtime unavailable' })}
                  </span>
                </div>
              ) : p.connected ? (
                <>
                  <button className="conn-accounts-toggle" onClick={() => toggleExpand(p.id)}>
                    {isOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    {t('connections.accounts', { defaultValue: 'Accounts' })}
                  </button>
                  {isOpen && (
                    <div className="conn-accounts">
                      {rows.map((row) => (
                        <AccountRow
                          key={row.id}
                          providerId={p.id}
                          row={row}
                          usage={accounts.usageByConnection[row.id]}
                          pending={accounts.rowPendingId === row.id}
                          actions={accounts}
                          onRequestRemove={(providerId, connectionId, label) => setConfirmation({
                            title: t('connections.removeAccount', { defaultValue: 'Remove account?' }),
                            message: t('connections.removeAccountConfirm', {
                              defaultValue: 'Remove the account {{label}}?',
                              label,
                            }),
                            confirmLabel: t('common.delete', { defaultValue: 'Remove' }),
                            action: () => accounts.onRemoveAccount(providerId, connectionId),
                          })}
                        />
                      ))}
                      <AddAccount provider={p} actions={accounts} />
                      <p className="conn-accounts-hint">
                        {t('connections.accountsHint', { defaultValue: 'Active accounts rotate; order sets fallback preference.' })}
                      </p>
                    </div>
                  )}
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
                      onClick={() => setConfirmation({
                        title: t('connections.removeAllAccounts', { defaultValue: 'Remove all accounts?' }),
                        message: t('connections.removeAllConfirm', {
                          defaultValue: 'Remove all {{count}} account(s) for {{name}}?',
                          count,
                          name: p.display_name,
                        }),
                        confirmLabel: t('common.delete', { defaultValue: 'Remove' }),
                        action: () => onDisconnect(p.id),
                      })}
                    >
                      <Trash2 size={14} />
                      {t('connections.removeAllAccounts', { defaultValue: 'Remove all accounts' })}
                    </button>
                  </div>
                </>
              ) : p.free_models_available ? (
                <>
                  <div className="conn-no-auth">
                    <Check size={14} />
                    <span>{t('connections.freeModelsAvailable', {
                      defaultValue: 'Free models available without an API key.',
                    })}</span>
                  </div>
                  <button className="conn-btn primary" onClick={() => beginConnect(p)}>
                    {t('connections.addApiKey')}
                  </button>
                </>
              ) : (
                <button className="conn-btn primary" onClick={() => beginConnect(p)}>
                  {providerUsesInlineApiKey(p) ? t('connections.addApiKey') : t('connections.authenticate')}
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
            );
          })}
            </div>
          </section>)}
        </div>

        {apiKeyProviders.length > 0 && <section className="conn-keypanel" ref={keyPanelRef}>
          <strong>{t('connections.addKeyTitle')}</strong>
          <p>{t('connections.addKeyDesc')}</p>
          <div className={(selectedKeyProvider?.requires_base_url || selectedKeyProvider?.base_url) ? 'conn-keyform has-base-url' : 'conn-keyform'}>
            <div className="conn-select">
              <select value={selectedKeyProvider?.id ?? ''} onChange={(e) => { onSelectKeyProvider(e.target.value); setBaseUrl(''); }}>
                {apiKeyProviders.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} />
            </div>
            {(selectedKeyProvider?.requires_base_url || selectedKeyProvider?.base_url) && (
              <input
                value={selectedBaseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder="https://api.example.com/v1"
                type="url"
                aria-label="Provider base URL"
              />
            )}
            <input ref={keyInputRef} value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-… / AIza… / sk-ant-…" type="password" />
            <button
              className="conn-btn primary"
              disabled={!apiKey.trim() || Boolean(selectedKeyProvider?.requires_base_url && !selectedBaseUrl.trim())}
              onClick={() => {
                if (selectedKeyProvider) onSaveKey(selectedKeyProvider.id, apiKey, selectedBaseUrl);
                setApiKey('');
              }}
            >
              {t('common.save')}
            </button>
          </div>
        </section>}
      </div>
      {confirmation && (
        <ConfirmDialog
          title={confirmation.title}
          message={confirmation.message}
          confirmLabel={confirmation.confirmLabel}
          danger
          onConfirm={() => {
            confirmation.action();
            setConfirmation(null);
          }}
          onCancel={() => setConfirmation(null)}
        />
      )}
    </div>
  );
}
