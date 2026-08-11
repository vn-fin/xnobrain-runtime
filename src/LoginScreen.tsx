import { useState, type FormEvent } from 'react';
import { UserRound } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from './auth';
import { productName } from './config/product';

export default function LoginScreen() {
  const { t } = useTranslation();
  const { signIn } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      await signIn({ email, password });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('login.error'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="login-screen">
      <form className="login-card" onSubmit={submit}>
        <div className="login-brand"><span className="login-logo"><UserRound size={24} /></span><strong>{productName}</strong></div>
        <div className="login-heading">
          <h1>{t('login.title')}</h1>
          <p className="login-sub">{t('login.subtitle')}</p>
        </div>
        <label className="login-field">{t('login.username')}<input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t('login.usernamePlaceholder')} autoFocus /></label>
        <label className="login-field">{t('login.password')}<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={t('login.passwordPlaceholder')} /></label>
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="login-btn" type="submit" disabled={busy || !email.trim() || !password}>
          {busy ? t('login.signingIn') : t('login.signIn')}
        </button>
      </form>
    </div>
  );
}
