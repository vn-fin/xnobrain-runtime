import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ClipboardPaste, Plus, X } from 'lucide-react';
import { ExternalLinkIcon, ProviderBrandIcon } from './common';
import type { Agent, ConnectionProvider, ProviderConnectInfo, ProviderConnector } from '../types';
import { providerConnectNeedsText } from '../utils/providers';
import { useProviderAuthPopup } from '../hooks/useProviderAuthPopup';

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

export function CreateAgentModal({ onCreate, onClose }: { onCreate: (name: string, description: string) => void; onClose: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="app-modal" onClick={(e) => e.stopPropagation()}>
        <div className="app-modal-head">
          <strong>{t('modals.createAgent')}</strong>
          <button className="icon-button" onClick={onClose} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>
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
          <button className="conn-btn ghost" onClick={onClose}>{t('common.cancel')}</button>
          <button className="conn-btn primary" disabled={!name.trim()} onClick={() => onCreate(name.trim(), description.trim())}>
            <Plus size={15} />
            {t('modals.createAgentBtn')}
          </button>
        </div>
      </div>
    </div>
  );
}

export function AgentSettingsModal({
  agent,
  providers,
  onSave,
  onClose,
}: {
  agent: Agent;
  providers: ProviderConnector[];
  onSave: (updates: Partial<Agent>) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(agent.title);
  const [description, setDescription] = useState(agent.description);
  const [provider, setProvider] = useState(agent.provider);
  const [model, setModel] = useState(agent.model);
  const [reasoningEffort, setReasoningEffort] = useState(agent.reasoningEffort);
  const [approvalMode, setApprovalMode] = useState<Agent['approvalMode']>(agent.approvalMode);
  const [confirming, setConfirming] = useState(false);

  const save = () => onSave({ title, description, provider, model, reasoningEffort, approvalMode });

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="app-modal" onClick={(e) => e.stopPropagation()}>
        <div className="app-modal-head">
          <strong>{t('modals.agentSettings')}</strong>
          <button className="icon-button" onClick={onClose} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>
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
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>{p.display_name}</option>
                ))}
              </select>
            </label>
            <label>
              {t('modals.model')}
              <input value={model} onChange={(e) => setModel(e.target.value)} />
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
  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="app-modal confirm-modal" onClick={(e) => e.stopPropagation()}>
        <div className="app-modal-head">
          <strong>{title}</strong>
          <button className="icon-button" onClick={onCancel} title={t('common.close')}>
            <X size={17} />
          </button>
        </div>
        <p className="confirm-message">{message}</p>
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
