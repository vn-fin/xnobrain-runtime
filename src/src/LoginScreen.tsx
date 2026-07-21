import { useState, type FormEvent } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from './auth';

export default function LoginScreen({ optional = false }: { optional?: boolean }) {
  const { t } = useTranslation();
  const { signIn, closeLogin, authError } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      await signIn(email.trim(), password);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('login.error'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`login-screen${optional ? ' optional' : ''}`}>
      <form className="login-card" onSubmit={submit}>
        {optional && <button type="button" className="login-close icon-button" onClick={closeLogin} title={t('common.close')}><X size={17} /></button>}
        <div className="login-brand"><span className="login-logo">L</span><strong>Open Lumora</strong></div>
        <div className="login-heading"><h1>{t('login.title')}</h1><p className="login-sub">{t('login.subtitle')}</p></div>
        <label className="login-field">{t('login.username')}<input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t('login.usernamePlaceholder')} autoFocus /></label>
        <label className="login-field">{t('login.password')}<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={t('login.passwordPlaceholder')} /></label>
        {(error || authError) && <div className="login-error">{error || authError}</div>}
        <button className="login-btn" type="submit" disabled={busy || !email.trim() || !password.trim()}>{busy ? t('login.signingIn') : t('login.signIn')}</button>
      </form>
    </div>
  );
}
