import { Building2, CheckCircle2, Cloud, LogOut, ShieldCheck, UserRound, X } from 'lucide-react';
import { useAuth } from '../auth';

const editionLabels = {
  opensource: 'Open Source',
  pro: 'Pro',
  cloud: 'Cloud',
  enterprise: 'Enterprise',
} as const;

export function AccountView({ onClose }: { onClose: () => void }) {
  const { config, user, signOut } = useAuth();
  if (!user) return null;

  return (
    <section className="account-view">
      <header className="view-header">
        <div><h1>Account</h1><p>Active user and deployment information.</p></div>
        <button className="icon-button" onClick={onClose} title="Close"><X size={18} /></button>
      </header>

      <div className="account-grid">
        <article className="account-card account-identity">
          <div className="account-large-avatar">{(user.displayName || user.email).charAt(0).toUpperCase()}</div>
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
            <div><dt>Tenant</dt><dd>{user.tenantId || 'Not assigned'}</dd></div>
            <div><dt>Plan</dt><dd>{user.planId || editionLabels[config.edition]}</dd></div>
            <div><dt>Roles</dt><dd>{user.roles.join(', ') || 'user'}</dd></div>
          </dl>
        </article>

        <article className="account-card">
          <h3>{config.edition === 'opensource' ? <Building2 size={17} /> : <Cloud size={17} />} Deployment</h3>
          <div className="account-status"><CheckCircle2 size={17} /><span>{editionLabels[config.edition]} active</span></div>
          <p>
            {config.auth.provider === 'local-profile'
              ? 'Local profile mode personalizes this browser. The standalone server remains available without signing in.'
              : 'Your session is verified by the Brain4All gateway before workspace requests are forwarded.'}
          </p>
        </article>

        <article className="account-card">
          <h3><ShieldCheck size={17} /> Session</h3>
          <p>{config.auth.provider === 'gateway' ? 'Protected by a same-origin gateway session.' : 'Browser-local profile; not an access-control boundary.'}</p>
          <button className="account-signout" onClick={() => void signOut()}><LogOut size={16} /> Sign out</button>
        </article>
      </div>
    </section>
  );
}
