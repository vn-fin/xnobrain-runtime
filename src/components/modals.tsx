import { useId, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ClipboardPaste, FileArchive, Plus, Upload, X } from 'lucide-react';
import { ExternalLinkIcon, ProviderBrandIcon } from './common';
import type { Agent, ConnectionProvider, ProviderConnectInfo, ProviderConnector } from '../types';
import { providerConnectNeedsText } from '../utils/providers';
import { useProviderAuthPopup } from '../hooks/useProviderAuthPopup';
import { systemApi, type BundleTransfer, type ImportReport, type TransferProgress } from '../features/system/api';

export function AuthModal({
  provider,
  info,
  onSubmit,
  onClose,
}: {
  provider: ConnectionProvider;
  info: ProviderConnectInfo;
  onSubmit: (code: string) => void | Promise<unknown>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const needsText = providerConnectNeedsText(provider, info);
  const authURL = info.verification_url || info.login_url;
  const pasteStep = info.user_code ? 3 : 2;

  const submitText = async (value: string): Promise<boolean> => {
    const text = value.trim();
    if (!text || submitting) return false;
    setSubmitting(true);
    try {
      return (await onSubmit(text)) !== false;
    } finally {
      setSubmitting(false);
    }
  };
  const { openPopup, pasteFromClipboard } = useProviderAuthPopup({
    active: needsText,
    onCallbackText: submitText,
    onClipboardText: setCode,
  });

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="auth-modal" onClick={(e) => e.stopPropagation()}>
        <div className="auth-modal-head">
          <div className="auth-modal-title">
            <ProviderBrandIcon brand={provider.brand} />
            <strong>{t('auth.connect', { name: provider.display_name })}</strong>
          </div>
          <button className="icon-button" onClick={onClose} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>

        <p className="auth-instructions">{info.instructions}</p>

        {authURL && (
          <div className="auth-step">
            <span className="auth-step-num">1</span>
            <div className="auth-step-grow">
              <div className="auth-step-label">{t('auth.openUrl')}</div>
              <button className="auth-url" type="button" onClick={() => openPopup(authURL)}>
                <ExternalLinkIcon />
                {t('onboarding.provider.openLogin')}
              </button>
            </div>
          </div>
        )}

        {info.user_code && (
          <div className="auth-step">
            <span className="auth-step-num">2</span>
            <div className="auth-step-grow">
              <div className="auth-step-label">{t('auth.enterCode')}</div>
              <div className="auth-usercode">{info.user_code}</div>
            </div>
          </div>
        )}

        {needsText ? (
          <div className="auth-step">
            <span className="auth-step-num">{pasteStep}</span>
            <div className="auth-step-grow">
              <label className="auth-step-label">{info.text_label || t('auth.pasteHint')}</label>
              <p className="auth-instructions">{t('auth.pasteInstructions')}</p>
              <div className="auth-response-wrap">
                <textarea
                  className="auth-response-text"
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  placeholder={t('auth.pasteHint')}
                  rows={4}
                />
                <button
                  className="auth-paste-button"
                  type="button"
                  disabled={submitting}
                  title={t('auth.pasteClipboard')}
                  onClick={() => void pasteFromClipboard()}
                >
                  <ClipboardPaste size={16} />
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="auth-waiting">
            <span className="async-spinner" />
            <span>{t('onboarding.provider.waiting')}</span>
          </div>
        )}

        <div className="auth-modal-actions">
          <button className="conn-btn ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          {needsText && (
            <button className="conn-btn primary" disabled={!code.trim() || submitting} onClick={() => void submitText(code)}>
              <Check size={15} />
              {submitting ? t('onboarding.checking') : t('auth.submit')}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export function CreateAgentModal({
  onCreate,
  onImported,
  onClose,
}: {
  onCreate: (name: string, description: string) => void;
  onImported: (report: ImportReport) => void | Promise<void>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<'new' | 'upload'>('new');
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [file, setFile] = useState<File>();
  const [transfer, setTransfer] = useState<BundleTransfer>();
  const [progress, setProgress] = useState<TransferProgress>();
  const [environment, setEnvironment] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const close = () => {
    if (busy) return;
    if (transfer?.upload_id) void systemApi.cancelUpload(transfer.upload_id).catch(() => undefined);
    onClose();
  };

  const upload = async () => {
    if (!file) return;
    setBusy('upload'); setError(''); setProgress(undefined);
    try {
      const result = await systemApi.upload(file, setProgress);
      setTransfer(result);
      setEnvironment(Object.fromEntries((result.preview?.missing_environment ?? []).map((key) => [key, ''])));
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not upload profile.');
    } finally {
      setBusy(''); setProgress(undefined);
    }
  };

  const apply = async () => {
    if (!transfer?.upload_id) return;
    setBusy('apply'); setError('');
    try {
      const filled = Object.fromEntries(Object.entries(environment).filter(([, value]) => value.trim()));
      const report = await systemApi.applyUpload(transfer.upload_id, filled);
      setTransfer(undefined);
      await onImported(report);
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not create agent from profile.');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="modal-overlay" onClick={close}>
      <div className="app-modal" role="dialog" aria-modal="true" aria-label={t('modals.createAgent')} onClick={(e) => e.stopPropagation()}>
        <div className="app-modal-head">
          <strong>{t('modals.createAgent')}</strong>
          <button className="icon-button" onClick={close} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>
        <div className="create-agent-tabs">
          <button className={mode === 'new' ? 'active' : ''} onClick={() => setMode('new')}><Plus size={14} />New agent</button>
          <button className={mode === 'upload' ? 'active' : ''} onClick={() => setMode('upload')}><Upload size={14} />Upload profile</button>
        </div>
        {mode === 'new' ? (
          <>
            <p className="app-modal-sub">{t('modals.createAgentApi')}</p>
            <div className="modal-form">
              <label>
                {t('modals.name')}
                <input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('modals.namePlaceholder')} autoFocus />
              </label>
              <label>
                {t('modals.description')}
                <textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('modals.descPlaceholder')} rows={3} />
              </label>
            </div>
            <div className="modal-actions">
              <button className="conn-btn ghost" onClick={close}>{t('common.cancel')}</button>
              <button className="conn-btn primary" disabled={!name.trim()} onClick={() => onCreate(name.trim(), description.trim())}>
                <Plus size={15} />
                {t('modals.createAgentBtn')}
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="app-modal-sub">Credentials are removed from the archive and synchronized from this server's default profile.</p>
            <label className="profile-upload-picker">
              <FileArchive size={20} />
              <span><strong>{file?.name ?? 'Choose a .zip profile archive'}</strong><small>Uploads use verified 4 MB parts.</small></span>
              <input type="file" accept=".zip,application/zip" onChange={(event) => { if (transfer?.upload_id) void systemApi.cancelUpload(transfer.upload_id); setFile(event.target.files?.[0]); setTransfer(undefined); setError(''); }} />
            </label>
            {busy === 'upload' && <div className="profile-transfer-progress"><span style={{ width: `${progress?.percent ?? 0}%` }} /><small>{progress?.percent ?? 0}% uploaded</small></div>}
            {transfer?.preview && (
              <div className="profile-import-preview">
                <strong>{transfer.preview.inspection.manifest.agents.length} profile ready to import</strong>
                <small>{transfer.preview.inspection.files} files · approvals reset · credentials copied from default profile</small>
                {(transfer.preview.missing_environment ?? []).map((key) => (
                  <label key={key}>{key}<input type="password" value={environment[key] ?? ''} onChange={(event) => setEnvironment((current) => ({ ...current, [key]: event.target.value }))} placeholder="Optional missing secret or environment value" autoComplete="off" /></label>
                ))}
              </div>
            )}
            {error && <div className="system-error">{error}</div>}
            <div className="modal-actions">
              <button className="conn-btn ghost" onClick={close}>{t('common.cancel')}</button>
              {!transfer ? (
                <button className="conn-btn primary" disabled={!file || !!busy} onClick={() => void upload()}><Upload size={15} />{busy === 'upload' ? 'Uploading…' : 'Upload & inspect'}</button>
              ) : (
                <button className="conn-btn primary" disabled={!!busy} onClick={() => void apply()}><Plus size={15} />{busy === 'apply' ? 'Creating…' : 'Create from profile'}</button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function providerModels(provider: ProviderConnector | undefined): string[] {
  if (!provider) return [];
  return Array.from(new Set([
    provider.default_model,
    ...(provider.available_models ?? []),
  ].filter(Boolean)));
}

export function AgentSettingsModal({
  agent,
  providers,
  onSave,
  onClose,
  embedded = false,
}: {
  agent: Agent;
  providers: ProviderConnector[];
  onSave: (updates: Partial<Agent>) => void;
  onClose: () => void;
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(agent.title);
  const [description, setDescription] = useState(agent.description);
  const initialProvider = providers.find((item) => providerModels(item).includes(agent.model))
    ?? providers.find((item) => item.id === agent.provider);
  const initialModels = providerModels(initialProvider);
  const [provider, setProvider] = useState(initialProvider?.id ?? agent.provider);
  const [model, setModel] = useState(
    initialModels.includes(agent.model)
      ? agent.model
      : initialProvider?.default_model || initialModels[0] || agent.model,
  );
  const [reasoningEffort, setReasoningEffort] = useState(agent.reasoningEffort);
  const [approvalMode, setApprovalMode] = useState<Agent['approvalMode']>(agent.approvalMode);
  const [confirming, setConfirming] = useState(false);
  const selectedProvider = providers.find((item) => item.id === provider);
  const selectedProviderModels = providerModels(selectedProvider);
  const modelOptions = selectedProviderModels.length
    ? selectedProviderModels
    : model ? [model] : [];

  const save = () => onSave({ title, description, provider, model, reasoningEffort, approvalMode });

  return (
    <div className={embedded ? 'agent-settings-embedded' : 'modal-overlay'} onClick={embedded ? undefined : onClose}>
      <div
        className={embedded ? 'agent-settings-surface' : 'app-modal agent-settings-modal'}
        role={embedded ? undefined : 'dialog'}
        aria-modal={embedded ? undefined : true}
        aria-label={embedded ? undefined : t('modals.agentSettings')}
        onClick={(e) => e.stopPropagation()}
      >
        {!embedded && (
          <div className="app-modal-head">
            <strong>{t('modals.agentSettings')}</strong>
            <button className="icon-button" onClick={onClose} title={t('common.close')}>
              <X size={17} />
            </button>
          </div>
        )}

        <div className="agent-settings-general">
            <p className="app-modal-sub">PATCH /agents/{'{id}'}/metadata · PATCH /agents-configs/{'{id}'}</p>

            <div className="modal-form">
              <label>
                {t('modals.settingsTitle')}
                <input value={title} onChange={(e) => setTitle(e.target.value)} />
              </label>
              <label>
                {t('modals.description')}
                <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={2} />
              </label>
              <div className="modal-form-row">
                <label>
                  {t('modals.provider')}
                  <select
                    value={provider}
                    onChange={(e) => {
                      const nextProvider = providers.find((item) => item.id === e.target.value);
                      const nextModels = providerModels(nextProvider);
                      setProvider(e.target.value);
                      setModel(nextProvider?.default_model || nextModels[0] || '');
                    }}
                  >
                    {providers.map((p) => (
                      <option key={p.id} value={p.id}>{p.display_name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  {t('modals.model')}
                  <select value={model} onChange={(e) => setModel(e.target.value)}>
                    {modelOptions.map((item) => (
                      <option key={item} value={item}>{item}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="modal-form-row">
                <label>
                  {t('modals.reasoningEffort')}
                  <select value={reasoningEffort} onChange={(e) => setReasoningEffort(e.target.value)}>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                  </select>
                </label>
                <label>
                  {t('modals.approvalMode')}
                  <select value={approvalMode} onChange={(e) => setApprovalMode(e.target.value as Agent['approvalMode'])}>
                    <option value="manual">manual</option>
                    <option value="auto">auto</option>
                  </select>
                </label>
              </div>
            </div>

            {confirming ? (
              <div className="modal-confirm">
                <span>{t('modals.saveConfirm', { name: title })}</span>
                <div className="modal-actions">
                  <button className="conn-btn ghost" onClick={() => setConfirming(false)}>{t('modals.back')}</button>
                  <button className="conn-btn primary" onClick={save}>
                    <Check size={15} />
                    {t('modals.confirmSave')}
                  </button>
                </div>
              </div>
            ) : (
              <div className="modal-actions">
                <button className="conn-btn ghost" onClick={onClose}>{t('common.cancel')}</button>
                <button className="conn-btn primary" onClick={() => setConfirming(true)}>{t('common.save')}</button>
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

export function ConfirmDialog({
  title,
  message,
  confirmLabel,
  danger,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const messageId = useId();
  return (
    <div className="modal-overlay alert-overlay" onClick={onCancel}>
      <div
        className={`app-modal confirm-modal${danger ? ' danger' : ''}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="app-modal-head">
          <strong id={titleId}>{title}</strong>
          <button className="icon-button" onClick={onCancel} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>
        <p className="confirm-message" id={messageId}>{message}</p>
        <div className="modal-actions">
          <button className="conn-btn ghost" onClick={onCancel}>{t('common.cancel')}</button>
          <button className={danger ? 'conn-btn danger-solid' : 'conn-btn primary'} onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PromptDialog({
  title,
  message,
  label,
  placeholder,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string;
  message?: string;
  label: string;
  placeholder?: string;
  confirmLabel: string;
  onConfirm: (value: string) => void;
  onCancel: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();
  const [value, setValue] = useState('');
  const submit = () => {
    const next = value.trim();
    if (next) onConfirm(next);
  };

  return (
    <div className="modal-overlay alert-overlay" onClick={onCancel}>
      <form
        className="app-modal prompt-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={message ? descriptionId : undefined}
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <div className="app-modal-head">
          <strong id={titleId}>{title}</strong>
          <button type="button" className="icon-button" onClick={onCancel} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>
        {message && <p className="app-modal-message" id={descriptionId}>{message}</p>}
        <label className="prompt-field">
          <span>{label}</span>
          <input value={value} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} autoFocus />
        </label>
        <div className="modal-actions">
          <button type="button" className="conn-btn ghost" onClick={onCancel}>{t('common.cancel')}</button>
          <button type="submit" className="conn-btn primary" disabled={!value.trim()}>{confirmLabel}</button>
        </div>
      </form>
    </div>
  );
}
