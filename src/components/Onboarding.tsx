import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Activity, ArrowLeft, ArrowRight, Check, ChevronDown, ClipboardPaste, ExternalLink, Languages, Plug, Plus, Server } from 'lucide-react';
import { ProviderBrandIcon } from './common';
import type { AsyncStatus, ConnectionProvider, ProviderConnectInfo } from '../types';
import { providerConnectNeedsText, providerUsesInlineApiKey } from '../utils/providers';
import { useProviderAuthPopup } from '../hooks/useProviderAuthPopup';
import { closeProviderAuthPopup } from '../utils/providerAuth';
import { SUPPORTED_LANGUAGES } from '../i18n';
import { brain4AllRuntime } from '../runtime';

const STEP_ICONS = [Server, Plug];
const TOTAL = 2;

export function Onboarding({
  sandboxStatus,
  sandboxProvisioned,
  setupRunning,
  setupProgress,
  setupMessage,
  sandboxError,
  onCreateSandbox,
  providers,
  providerPendingId,
  onStartConnect,
  onCheckConnect,
  onSubmitConnectText,
  onTestProvider,
  onSaveKey,
}: {
  sandboxStatus: AsyncStatus;
  sandboxProvisioned: boolean;
  setupRunning: boolean;
  setupProgress: number;
  setupMessage: string;
  sandboxError?: string;
  onCreateSandbox: () => void;
  providers: ConnectionProvider[];
  providerPendingId: string | null;
  onStartConnect: (id: string) => Promise<ProviderConnectInfo | null>;
  onCheckConnect: (id: string) => Promise<boolean>;
  onSubmitConnectText: (id: string, text: string) => Promise<boolean>;
  onTestProvider: (id: string) => void;
  onSaveKey: (id: string, key: string, baseUrl?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const managedVM = brain4AllRuntime.edition === 'cloud';
  const [step, setStep] = useState(0);

  const connected = providers.filter((p) => p.connected);
  const [keyDrafts, setKeyDrafts] = useState<Record<string, string>>({});
  const [baseUrlDrafts, setBaseUrlDrafts] = useState<Record<string, string>>({});

  // Connect step: work with a single selected provider at a time.
  const [selectedProviderId, setSelectedProviderId] = useState('');
  const selectedProvider = providers.find((p) => p.id === selectedProviderId);
  const selectedVerified = Boolean(selectedProvider?.connected) && selectedProvider?.last_test_status === 'healthy';
  const [connectInfo, setConnectInfo] = useState<ProviderConnectInfo | null>(null);
  const [connectText, setConnectText] = useState('');
  const [submittingConnectText, setSubmittingConnectText] = useState(false);
  const connectNeedsText = Boolean(selectedProvider && connectInfo && providerConnectNeedsText(selectedProvider, connectInfo));
  const apiKeyConnectFlow = connectInfo?.connection_mode === 'api-key';

  // Default the connect step to the first available provider.
  useEffect(() => {
    if (!selectedProviderId && providers.length > 0) {
      setSelectedProviderId(providers.find((p) => p.connection_mode === 'api-key')?.id ?? providers[0].id);
    }
  }, [providers, selectedProviderId]);

  // While awaiting a device-code/runtime-managed connection, poll the connect
  // status so completion is reflected without a callback submission.
  const awaitingConnect = Boolean(connectInfo) && Boolean(selectedProvider) && !selectedProvider?.connected && !connectNeedsText;
  useEffect(() => {
    if (!awaitingConnect || !selectedProviderId) return;
    let active = true;
    const timer = setInterval(() => {
      void onCheckConnect(selectedProviderId).then((ok) => {
        if (ok && active) setConnectInfo(null);
      });
    }, 3000);
    return () => { active = false; clearInterval(timer); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [awaitingConnect, selectedProviderId]);

  const authenticate = async () => {
    if (!selectedProvider) return;
    const info = await onStartConnect(selectedProvider.id);
    setConnectInfo(info);
    setConnectText('');
  };

  const submitProviderCallback = async (value: string): Promise<boolean> => {
    const callbackText = value.trim();
    if (!selectedProvider || !callbackText || submittingConnectText) return false;
    setConnectText(callbackText);
    setSubmittingConnectText(true);
    try {
      const ok = await onSubmitConnectText(selectedProvider.id, callbackText);
      if (ok) {
        setConnectInfo(null);
        setConnectText('');
      }
      return ok;
    } finally {
      setSubmittingConnectText(false);
    }
  };
  const { openPopup, pasteFromClipboard } = useProviderAuthPopup({
    active: connectNeedsText,
    onCallbackText: submitProviderCallback,
    onClipboardText: setConnectText,
  });

  useEffect(() => () => closeProviderAuthPopup(), []);

  const canProceed = step === 0 ? sandboxProvisioned : connected.length > 0;

  const stepTitles = [
    t('onboarding.vm.title'),
    t('onboarding.provider.title'),
  ];

  return (
    <div className="onboarding">
      <div className="onboarding-card">
        <header className="onboarding-head">
          <div className="ob-lang" title={t('language.label')}>
            <Languages size={14} />
            <select
              value={i18n.resolvedLanguage}
              onChange={(e) => void i18n.changeLanguage(e.target.value)}
              aria-label={t('language.label')}
            >
              {SUPPORTED_LANGUAGES.map((lng) => (
                <option key={lng} value={lng}>{t(`language.${lng}`)}</option>
              ))}
            </select>
            <ChevronDown size={13} />
          </div>
          <h1>{t('onboarding.title')}</h1>
          <p>{t('onboarding.step', { current: step + 1, total: TOTAL })} · {stepTitles[step]}</p>
          <div className="ob-dots">
            {STEP_ICONS.map((Icon, i) => {
              const state = i < step ? 'done' : i === step ? 'active' : 'todo';
              return (
                <span key={i} className={`ob-dot ${state}`}>
                  {state === 'done' ? <Check size={14} /> : <Icon size={14} />}
                </span>
              );
            })}
          </div>
        </header>

        <div className="ob-panel">
          {step === 0 && (
            <div className="ob-step-body">
              <strong>{managedVM ? 'Create your VM' : t('onboarding.vm.title')}</strong>
              <span className="ob-step-desc">{managedVM ? 'Provision your private Incus VM before configuring the Brain4All runtime.' : t('onboarding.vm.desc')}</span>
              {setupRunning ? (
                <div className="ob-progress">
                  <div className="sbx-progress-track" role="progressbar" aria-label="VM provisioning progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={setupProgress}>
                    <div className="sbx-progress-fill" style={{ width: `${setupProgress}%` }} />
                  </div>
                  <span className="sbx-progress-pct">{t('onboarding.vm.provisioning', { percent: setupProgress })}</span>
                  <span className="sbx-progress-message" aria-live="polite">{setupMessage}</span>
                </div>
              ) : sandboxProvisioned ? (
                <span className="ob-done-note">{t('onboarding.vm.ready')}</span>
              ) : (
                <>
                  <button className="ob-step-btn primary" disabled={sandboxStatus === 'loading'} onClick={onCreateSandbox}>
                    <Plus size={15} />
                    {sandboxStatus === 'loading' ? t('onboarding.checking') : managedVM ? 'Create VM' : t('onboarding.vm.action')}
                  </button>
                  {sandboxError && <span className="ob-error">{sandboxError}</span>}
                </>
              )}
            </div>
          )}

          {step === 1 && (
            <div className="ob-step-body">
              <strong>{t('onboarding.provider.title')}</strong>
              <span className="ob-step-desc">{t('onboarding.provider.desc')}</span>

              <label className="ob-field">
                <span>{t('modals.provider')}</span>
                <div className="ob-select">
                  <select
                    value={selectedProviderId}
                    onChange={(e) => {
                      closeProviderAuthPopup();
                      setSelectedProviderId(e.target.value);
                      setConnectInfo(null);
                      setConnectText('');
                    }}
                  >
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>{p.display_name}</option>
                    ))}
                  </select>
                  <ChevronDown size={15} />
                </div>
              </label>

              {selectedProvider && (
                <div className="ob-provider">
                  <div className="ob-provider-head">
                    <ProviderBrandIcon brand={selectedProvider.brand} />
                    <strong>{selectedProvider.display_name}</strong>
                    <span className={selectedProvider.connected ? 'conn-badge ok' : 'conn-badge'}>
                      {selectedProvider.connection_mode === 'no-auth'
                        ? selectedProvider.connected
                          ? t('common.available', { defaultValue: 'available' })
                          : t('common.unavailable', { defaultValue: 'unavailable' })
                        : selectedProvider.connected
                        ? (selectedVerified ? t('onboarding.provider.verified') : t('connections.connected'))
                        : t('connections.notConnected')}
                    </span>
                  </div>
                  <p className="ob-step-desc">{selectedProvider.description}</p>

                  {selectedProvider.connection_mode === 'no-auth' && selectedProvider.connected ? (
                    <span className="ob-done-note">
                      <Check size={14} />
                      {t('connections.noAuthRequired', { defaultValue: 'No API key required' })}
                    </span>
                  ) : selectedProvider.connected ? (
                    selectedVerified ? (
                      <span className="ob-done-note"><Check size={14} /> {t('onboarding.provider.verifiedNote', { name: selectedProvider.display_name })}</span>
                    ) : (
                      <button className="conn-btn primary" disabled={providerPendingId === selectedProvider.id} onClick={() => onTestProvider(selectedProvider.id)}>
                        <Activity size={14} />
                        {providerPendingId === selectedProvider.id ? t('onboarding.checking') : t('onboarding.provider.verify')}
                      </button>
                    )
                  ) : providerUsesInlineApiKey(selectedProvider) ? (
                    <div className="ob-key-row">
                      {(selectedProvider.requires_base_url || selectedProvider.base_url) && (
                        <input
                          type="url"
                          value={baseUrlDrafts[selectedProvider.id] ?? selectedProvider.base_url ?? ''}
                          placeholder="https://api.example.com/v1"
                          aria-label="Provider base URL"
                          onChange={(e) => setBaseUrlDrafts((drafts) => ({ ...drafts, [selectedProvider.id]: e.target.value }))}
                        />
                      )}
                      <input
                        type="password"
                        value={keyDrafts[selectedProvider.id] ?? ''}
                        placeholder="sk-… / AIza… / sk-ant-…"
                        onChange={(e) => setKeyDrafts((d) => ({ ...d, [selectedProvider.id]: e.target.value }))}
                      />
                      <button
                        className="conn-btn primary"
                        disabled={!(keyDrafts[selectedProvider.id] ?? '').trim()
                          || Boolean(selectedProvider.requires_base_url && !(baseUrlDrafts[selectedProvider.id] ?? selectedProvider.base_url ?? '').trim())
                          || providerPendingId === selectedProvider.id}
                        onClick={() => onSaveKey(
                          selectedProvider.id,
                          keyDrafts[selectedProvider.id] ?? '',
                          baseUrlDrafts[selectedProvider.id] ?? selectedProvider.base_url,
                        )}
                      >
                        {t('common.save')}
                      </button>
                    </div>
                  ) : connectInfo ? (
                    <div className="ob-connect-wait">
                      {connectInfo.instructions && <p className="ob-step-desc">{connectInfo.instructions}</p>}
                      {(connectInfo.verification_url || connectInfo.login_url) && (
                        <button
                          className="conn-btn ghost"
                          type="button"
                          onClick={() => openPopup(connectInfo.verification_url || connectInfo.login_url)}
                        >
                          <ExternalLink size={14} />
                          {t('onboarding.provider.openLogin')}
                        </button>
                      )}
                      {connectInfo.user_code && <div className="ob-code">{connectInfo.user_code}</div>}
                      {connectNeedsText ? (
                        <div className="ob-connect-response">
                          {!apiKeyConnectFlow && <p className="ob-step-desc">{t('auth.pasteInstructions')}</p>}
                          <label className="ob-field">
                            <span>{connectInfo.text_label || t('auth.pasteHint')}</span>
                            <div className="auth-response-wrap">
                              <textarea
                                value={connectText}
                                onChange={(e) => setConnectText(e.target.value)}
                                placeholder={apiKeyConnectFlow ? connectInfo.text_label : t('auth.pasteHint')}
                                rows={4}
                              />
                              {!apiKeyConnectFlow && <button
                                className="auth-paste-button"
                                type="button"
                                disabled={submittingConnectText}
                                title={t('auth.pasteClipboard')}
                                onClick={() => void pasteFromClipboard()}
                              >
                                <ClipboardPaste size={16} />
                              </button>}
                            </div>
                          </label>
                          <button
                            className="conn-btn primary"
                            disabled={!connectText.trim() || submittingConnectText || providerPendingId === selectedProvider.id}
                            onClick={() => void submitProviderCallback(connectText)}
                          >
                            <Check size={14} />
                            {submittingConnectText || providerPendingId === selectedProvider.id ? t('onboarding.checking') : t('auth.submit')}
                          </button>
                        </div>
                      ) : (
                        <>
                          <div className="ob-waiting">
                            <span className="async-spinner ob-spinner" />
                            <span>{t('onboarding.provider.waiting')}</span>
                          </div>
                          <button className="conn-btn ghost" onClick={() => void onCheckConnect(selectedProvider.id).then((ok) => ok && setConnectInfo(null))}>
                            {t('onboarding.provider.check')}
                          </button>
                        </>
                      )}
                    </div>
                  ) : (
                    <button className="conn-btn primary" disabled={providerPendingId === selectedProvider.id} onClick={authenticate}>
                      {providerPendingId === selectedProvider.id ? t('onboarding.checking') : t('connections.authenticate')}
                    </button>
                  )}
                </div>
              )}
            </div>
          )}

        </div>

        <footer className="ob-nav">
          <button className="ob-step-btn" disabled={step === 0} onClick={() => setStep((s) => Math.max(0, s - 1))}>
            <ArrowLeft size={15} />
            {t('common.back')}
          </button>
          {step < TOTAL - 1 && (
            <button className="ob-step-btn primary" disabled={!canProceed} onClick={() => setStep((s) => Math.min(TOTAL - 1, s + 1))}>
              {t('common.next')}
              <ArrowRight size={15} />
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}
