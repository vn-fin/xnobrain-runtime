import { useCallback, useEffect, useState } from 'react';
import { Building2, CheckCircle2, Cloud, LogOut, RefreshCw, ShieldCheck, UserRound, X } from 'lucide-react';
import { useAuth } from '../auth';

const editionLabels = {
  opensource: 'Open Source',
  pro: 'Pro',
  cloud: 'Cloud',
  enterprise: 'Enterprise',
} as const;

export function AccountView({
  onClose,
  onRequestSignOut,
}: {
  onClose: () => void;
  onRequestSignOut: () => void;
}) {
  const { config, user, loadCurrentUser } = useAuth();
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setStatus('loading');
    setError('');
    try {
      const next = await loadCurrentUser();
      setStatus(next ? 'ready' : 'error');
      if (!next) setError('No active account was returned.');
    } catch (cause) {
      setStatus('error');
      setError(cause instanceof Error ? cause.message : 'Could not load the account.');
    }
  }, [loadCurrentUser]);

  useEffect(() => { void load(); }, [load]);

  return (
    <section className="account-view">
      <header className="view-header">
        <div><h1>Account</h1><p>Active user and deployment information.</p></div>
        <button className="icon-button" onClick={onClose} title="Close"><X size={18} /></button>
      </header>

      {status === 'loading' && !user && <div className="account-state">Loading account…</div>}
      {status === 'error' && !user && (
        <div className="account-state error" role="alert">
          <span>{error}</span>
          <button className="conn-btn ghost" onClick={() => void load()}><RefreshCw size={15} /> Retry</button>
        </div>
      )}
      {user && <div className="account-grid">
        <article className="account-card account-identity">
          {user.picture
            ? <img className="account-large-avatar account-avatar-image" src={user.picture} alt="" referrerPolicy="no-referrer" />
            : <div className="account-large-avatar">{(user.displayName || user.email).charAt(0).toUpperCase()}</div>}
          <div>
            <span className={`edition-badge ${config.edition}`}>{editionLabels[config.edition]}</span>
            <h2>{user.displayName || user.email}</h2>
            <p>{user.email}</p>
          </div>
        </article>

        <article className="account-card">
          <h3><UserRound size={17} /> Active user</h3>
          <dl className="account-details">
            <div><dt>User ID</dt><dd>{user.userId}</dd></div>
            {user.phone && <div><dt>Phone</dt><dd>{user.phone}</dd></div>}
            {user.organization && <div><dt>Organization</dt><dd>{user.organization}</dd></div>}
            {user.country && <div><dt>Country</dt><dd>{user.country}</dd></div>}
            <div><dt>Tenant</dt><dd>{user.tenantId || 'Not assigned'}</dd></div>
            <div><dt>Plan</dt><dd>{user.planId || editionLabels[config.edition]}</dd></div>
            <div><dt>Roles</dt><dd>{user.roles.join(', ') || 'user'}</dd></div>
          </dl>
        </article>

        <article className="account-card">
          <h3>{config.edition === 'opensource' ? <Building2 size={17} /> : <Cloud size={17} />} Deployment</h3>
          <div className="account-status"><CheckCircle2 size={17} /><span>{editionLabels[config.edition]} active</span></div>
          <p>
            {config.auth.provider === 'xno-firebase'
                ? 'Your Firebase identity was exchanged for an XNOQuant API session.'
                : 'Your session is verified by the Brain4All gateway before workspace requests are forwarded.'}
          </p>
        </article>

        <article className="account-card">
          <h3><ShieldCheck size={17} /> Session</h3>
          <p>
            {config.auth.provider === 'gateway'
              ? 'Protected by a same-origin gateway session.'
              : config.auth.provider === 'xno-firebase'
                ? 'Authenticated XNOBrain cloud session.'
                : 'Browser-local profile; not an access-control boundary.'}
          </p>
          <button
            className="account-signout"
            onClick={onRequestSignOut}
          >
            <LogOut size={16} /> Sign out
          </button>
        </article>
      </div>}
    </section>
  );
}
