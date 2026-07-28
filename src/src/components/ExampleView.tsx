import { useCallback, useEffect, useState } from 'react';
import { BadgeCheck, Building2, FlaskConical, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { useAuth, type ActiveUser } from '../auth';

type MeResponse = {
  success?: boolean;
  message?: string;
  data?: Record<string, unknown>;
};

function endpoint(baseUrl: string, path: string) {
  return new URL(path, `${baseUrl.replace(/\/+$/, '')}/`).toString();
}

function text(value: unknown) {
  return String(value ?? '').trim();
}

function accountFromMe(value: Record<string, unknown>): ActiveUser {
  const info = value.info && typeof value.info === 'object' ? value.info as Record<string, unknown> : {};
  const kyc = info.kyc && typeof info.kyc === 'object' ? info.kyc as Record<string, unknown> : {};
  return {
    userId: text(value.user_id),
    email: text(value.email),
    displayName: text(value.fullname || value.username || value.email),
    username: text(value.username) || undefined,
    fullName: text(value.fullname) || undefined,
    phone: text(value.phone) || undefined,
    picture: text(value.picture) || undefined,
    emailVerified: typeof value.email_verified === 'boolean' ? value.email_verified : undefined,
    internalVerified: typeof value.internal_verified === 'boolean' ? value.internal_verified : undefined,
    country: text(info.country) || undefined,
    organization: text(info.organization) || undefined,
    kycVerified: typeof kyc.verified === 'boolean' ? kyc.verified : undefined,
    createdAt: text(value.created_at) || undefined,
    lastLogin: text(value.last_login) || undefined,
    tenantId: '',
    planId: '',
    roles: Array.isArray(value.roles) ? value.roles.map(String) : [],
  };
}

export function ExampleView({ onClose }: { onClose: () => void }) {
  const { config, user, accessToken, openLogin } = useAuth();
  const [remoteUser, setRemoteUser] = useState<ActiveUser | null>(null);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    if (!accessToken) {
      setRemoteUser(null);
      setStatus('idle');
      return;
    }
    setStatus('loading');
    setError('');
    try {
      const response = await fetch(endpoint(config.api.remoteBaseUrl, config.auth.mePath), {
        credentials: 'omit',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${accessToken}`,
        },
      });
      const body = await response.json() as MeResponse;
      if (!response.ok || !body.data) throw new Error(body.message || `Account API returned ${response.status}.`);
      setRemoteUser(accountFromMe(body.data));
      setStatus('ready');
    } catch (cause) {
      setStatus('error');
      setError(cause instanceof Error ? cause.message : 'Could not load /auth/v1/me.');
    }
  }, [accessToken, config.api.remoteBaseUrl, config.auth.mePath]);

  useEffect(() => { void load(); }, [load]);

  const account = remoteUser ?? user;

  return (
    <section className="example-view identity-demo">
      <header className="view-header">
        <div>
          <h1>Authenticated API example</h1>
          <p>A separate visual treatment for the live <code>/auth/v1/me</code> response.</p>
        </div>
        <button className="icon-button" onClick={onClose} title="Close"><X size={18} /></button>
      </header>

      {!accessToken ? (
        <div className="identity-empty">
          <FlaskConical size={32} />
          <h2>Sign in to call the example API</h2>
          <p>The request will use your XNOQuant access token and will not contact the Brain4All VM.</p>
          <button className="login-btn" onClick={openLogin}>Sign in</button>
        </div>
      ) : (
        <>
          <div className="example-toolbar">
            <span className="example-endpoint">{config.api.remoteBaseUrl}{config.auth.mePath}</span>
            <button className="secondary-button" onClick={() => void load()} disabled={status === 'loading'}>
              <RefreshCw size={15} className={status === 'loading' ? 'spin' : ''} /> Refresh
            </button>
          </div>

          {status === 'loading' && !account && <div className="example-state">Loading authenticated identity…</div>}
          {status === 'error' && <div className="example-state error" role="alert">{error}</div>}
          {account && (
            <div className="identity-board">
              <aside className="identity-pass">
                {account.picture
                  ? <img src={account.picture} alt="" referrerPolicy="no-referrer" />
                  : <div className="identity-pass-avatar">{account.displayName.charAt(0).toUpperCase()}</div>}
                <span>AUTHENTICATED SUBJECT</span>
                <h2>{account.fullName || account.displayName}</h2>
                <p>@{account.username || account.email}</p>
                <code>{account.userId}</code>
              </aside>

              <div className="identity-facts">
                <article>
                  <h3><ShieldCheck size={17} /> Verification</h3>
                  <div className="identity-checks">
                    <span className={account.emailVerified ? 'verified' : ''}><BadgeCheck size={15} /> Email</span>
                    <span className={account.kycVerified ? 'verified' : ''}><BadgeCheck size={15} /> KYC</span>
                    <span className={account.internalVerified ? 'verified' : ''}><BadgeCheck size={15} /> Internal</span>
                  </div>
                </article>
                <article>
                  <h3><Building2 size={17} /> Profile</h3>
                  <dl>
                    <div><dt>Email</dt><dd>{account.email}</dd></div>
                    <div><dt>Phone</dt><dd>{account.phone || '—'}</dd></div>
                    <div><dt>Country</dt><dd>{account.country || '—'}</dd></div>
                    <div><dt>Organization</dt><dd>{account.organization || '—'}</dd></div>
                    <div><dt>Last login</dt><dd>{account.lastLogin ? new Date(account.lastLogin).toLocaleString() : '—'}</dd></div>
                  </dl>
                </article>
                <article className="identity-roles">
                  <h3>Roles returned by XNOQuant</h3>
                  <div>{account.roles.map((role) => <span key={role}>{role}</span>)}</div>
                </article>
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
