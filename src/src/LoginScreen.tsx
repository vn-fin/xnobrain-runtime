import { useState, type FormEvent } from 'react';
import { UserRound, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useAuth } from './auth';

export default function LoginScreen({ optional = false }: { optional?: boolean }) {
  const { t } = useTranslation();
  const { config, signIn, closeLogin } = useAuth();
  const localProfile = config.auth.provider === 'local-profile';
  const [displayName, setDisplayName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setBusy(true);
    try {
      await signIn({ email, password, displayName });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t('login.error'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`login-screen${optional ? ' optional' : ''}`}>
      <form className="login-card" onSubmit={submit}>
        {optional && (
          <button type="button" className="login-close icon-button" onClick={closeLogin} title={t('common.close')}>
            <X size={17} />
          </button>
        )}
        <div className="login-brand"><span className="login-logo"><UserRound size={24} /></span><strong>Brain4All</strong></div>
        <div className="login-heading">
          <h1>{localProfile ? 'Create a local profile' : t('login.title')}</h1>
          <p className="login-sub">
            {localProfile ? 'Personalize this browser without restricting local access.' : t('login.subtitle')}
          </p>
        </div>
        {localProfile && (
          <>
            <div className="login-note">This profile is stored only in this browser. It is not authentication and does not protect the local server.</div>
            <label className="login-field">Display name<input autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="Your name" autoFocus /></label>
          </>
        )}
        <label className="login-field">{t('login.username')}<input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t('login.usernamePlaceholder')} autoFocus={!localProfile} /></label>
        {!localProfile && (
          <label className="login-field">{t('login.password')}<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder={t('login.passwordPlaceholder')} /></label>
        )}
        {error && <div className="login-error" role="alert">{error}</div>}
        <button className="login-btn" type="submit" disabled={busy || !email.trim() || (!localProfile && !password)}>
          {busy ? t('login.signingIn') : localProfile ? 'Save local profile' : t('login.signIn')}
        </button>
        {optional && <button type="button" className="login-continue" onClick={closeLogin}>Continue without signing in</button>}
      </form>
    </div>
  );
}
