import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { brain4AllRuntime, type RuntimeConfig } from './runtime';

export type ActiveUser = {
  userId: string;
  email: string;
  displayName: string;
  username?: string;
  fullName?: string;
  phone?: string;
  picture?: string;
  emailVerified?: boolean;
  internalVerified?: boolean;
  country?: string;
  organization?: string;
  kycVerified?: boolean;
  createdAt?: string;
  lastLogin?: string;
  tenantId: string;
  planId: string;
  roles: string[];
};

type LoginInput = {
  email: string;
  password: string;
  displayName: string;
};

type AuthContextValue = {
  config: RuntimeConfig;
  user: ActiveUser | null;
  loading: boolean;
  loginOpen: boolean;
  accessToken: string | null;
  signIn: (input: LoginInput) => Promise<void>;
  signOut: () => Promise<void>;
  openLogin: () => void;
  closeLogin: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const LOCAL_PROFILE_KEY = 'brain4all.local-profile';
const XNO_ACCESS_TOKEN_KEY = 'brain4all.xno.access-token';
const XNO_REFRESH_TOKEN_KEY = 'brain4all.xno.refresh-token';

function normalizeUser(value: unknown): ActiveUser | null {
  if (!value || typeof value !== 'object') return null;
  const user = value as Record<string, unknown>;
  const info = user.info && typeof user.info === 'object' ? user.info as Record<string, unknown> : {};
  const kyc = info.kyc && typeof info.kyc === 'object' ? info.kyc as Record<string, unknown> : {};
  const userId = String(user.user_id ?? user.userId ?? '').trim();
  const email = String(user.email ?? '').trim();
  if (!userId && !email) return null;
  return {
    userId: userId || email,
    email,
    displayName: String(user.fullname ?? user.display_name ?? user.displayName ?? user.username ?? email).trim(),
    username: String(user.username ?? '').trim() || undefined,
    fullName: String(user.fullname ?? '').trim() || undefined,
    phone: String(user.phone ?? '').trim() || undefined,
    picture: String(user.picture ?? '').trim() || undefined,
    emailVerified: typeof user.email_verified === 'boolean' ? user.email_verified : undefined,
    internalVerified: typeof user.internal_verified === 'boolean' ? user.internal_verified : undefined,
    country: String(info.country ?? '').trim() || undefined,
    organization: String(info.organization ?? '').trim() || undefined,
    kycVerified: typeof kyc.verified === 'boolean' ? kyc.verified : undefined,
    createdAt: String(user.created_at ?? '').trim() || undefined,
    lastLogin: String(user.last_login ?? '').trim() || undefined,
    tenantId: String(user.tenant_id ?? user.tenantId ?? '').trim(),
    planId: String(user.plan_id ?? user.planId ?? '').trim(),
    roles: Array.isArray(user.roles) ? user.roles.map(String) : [],
  };
}

function remoteURL(baseUrl: string, path: string) {
  return new URL(path, `${baseUrl.replace(/\/+$/, '')}/`).toString();
}

async function jsonResponse(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json().catch(() => undefined) as Record<string, unknown> | undefined;
  if (!response.ok) {
    throw new Error(String(body?.message ?? body?.error ?? `Authentication failed (${response.status}).`));
  }
  const data = body?.data;
  return data && typeof data === 'object' ? data as Record<string, unknown> : body ?? {};
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const config = brain4AllRuntime;
  const [user, setUser] = useState<ActiveUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(config.auth.provider !== 'local-profile');
  const [loginOpen, setLoginOpen] = useState(false);

  const loadGatewaySession = useCallback(async () => {
    const response = await fetch(config.auth.bootstrapPath, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (response.status === 401) {
      setUser(null);
      return;
    }
    const data = await jsonResponse(response);
    setUser(normalizeUser(data.user ?? data));
  }, [config.auth.bootstrapPath]);

  const clearXnoTokens = useCallback(() => {
    localStorage.removeItem(XNO_ACCESS_TOKEN_KEY);
    localStorage.removeItem(XNO_REFRESH_TOKEN_KEY);
    setAccessToken(null);
  }, []);

  const loadXnoUser = useCallback(async (token: string) => {
    const response = await fetch(remoteURL(config.api.remoteBaseUrl, config.auth.mePath), {
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${token}`,
      },
    });
    const data = await jsonResponse(response);
    const next = normalizeUser(data);
    if (!next) throw new Error('The account API returned no active user.');
    setAccessToken(token);
    setUser(next);
    return next;
  }, [config.api.remoteBaseUrl, config.auth.mePath]);

  const refreshXnoSession = useCallback(async () => {
    const refreshToken = localStorage.getItem(XNO_REFRESH_TOKEN_KEY);
    if (!refreshToken) throw new Error('No saved session.');
    const response = await fetch(remoteURL(config.api.remoteBaseUrl, config.auth.refreshPath), {
      method: 'POST',
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    const data = await jsonResponse(response);
    const nextAccessToken = String(data.access_token ?? '').trim();
    const nextRefreshToken = String(data.refresh_token ?? refreshToken).trim();
    if (!nextAccessToken) throw new Error('The account API returned an empty access token.');
    localStorage.setItem(XNO_ACCESS_TOKEN_KEY, nextAccessToken);
    localStorage.setItem(XNO_REFRESH_TOKEN_KEY, nextRefreshToken);
    await loadXnoUser(nextAccessToken);
  }, [config.api.remoteBaseUrl, config.auth.refreshPath, loadXnoUser]);

  const loadXnoSession = useCallback(async () => {
    const savedAccessToken = localStorage.getItem(XNO_ACCESS_TOKEN_KEY);
    if (savedAccessToken) {
      try {
        await loadXnoUser(savedAccessToken);
        return;
      } catch {
        // A stale access token may still have a valid refresh token.
      }
    }
    await refreshXnoSession();
  }, [loadXnoUser, refreshXnoSession]);

  useEffect(() => {
    let active = true;
    if (config.auth.mode === 'disabled') {
      setLoading(false);
      return undefined;
    }
    if (config.auth.provider === 'local-profile') {
      try {
        const stored = localStorage.getItem(LOCAL_PROFILE_KEY);
        if (stored && active) setUser(normalizeUser(JSON.parse(stored)));
      } catch {
        localStorage.removeItem(LOCAL_PROFILE_KEY);
      }
      setLoading(false);
      return undefined;
    }
    if (config.auth.provider === 'xno-firebase') {
      void loadXnoSession()
        .catch(() => {
          if (active) {
            clearXnoTokens();
            setUser(null);
          }
        })
        .finally(() => {
          if (active) setLoading(false);
        });
      return () => { active = false; };
    }
    void loadGatewaySession()
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [clearXnoTokens, config.auth.mode, config.auth.provider, loadGatewaySession, loadXnoSession]);

  const signIn = useCallback(async (input: LoginInput) => {
    if (config.auth.mode === 'disabled') throw new Error('Authentication is disabled.');
    if (config.auth.provider === 'local-profile') {
      const email = input.email.trim();
      const next: ActiveUser = {
        userId: `local:${email.toLowerCase()}`,
        email,
        displayName: input.displayName.trim() || email,
        tenantId: 'local',
        planId: config.edition === 'opensource' ? 'local' : config.edition,
        roles: ['owner'],
      };
      localStorage.setItem(LOCAL_PROFILE_KEY, JSON.stringify(next));
      setUser(next);
      setLoginOpen(false);
      return;
    }
    if (config.auth.provider === 'xno-firebase') {
      if (!config.auth.firebaseApiKey) throw new Error('Firebase API key is not configured.');
      const firebaseResponse = await fetch(
        `https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=${encodeURIComponent(config.auth.firebaseApiKey)}`,
        {
          method: 'POST',
          credentials: 'omit',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            email: input.email.trim(),
            password: input.password,
            returnSecureToken: true,
          }),
        },
      );
      const firebaseData = await firebaseResponse.json().catch(() => ({})) as Record<string, unknown>;
      if (!firebaseResponse.ok) {
        const firebaseError = firebaseData.error as Record<string, unknown> | undefined;
        throw new Error(String(firebaseError?.message ?? 'Firebase sign in failed.'));
      }
      const firebaseToken = String(firebaseData.idToken ?? '').trim();
      if (!firebaseToken) throw new Error('Firebase returned no identity token.');

      const tokenResponse = await fetch(remoteURL(config.api.remoteBaseUrl, config.auth.tokenPath), {
        credentials: 'omit',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${firebaseToken}`,
        },
      });
      const tokenData = await jsonResponse(tokenResponse);
      const nextAccessToken = String(tokenData.access_token ?? '').trim();
      const nextRefreshToken = String(tokenData.refresh_token ?? '').trim();
      if (!nextAccessToken || !nextRefreshToken) throw new Error('The account API returned an incomplete session.');
      localStorage.setItem(XNO_ACCESS_TOKEN_KEY, nextAccessToken);
      localStorage.setItem(XNO_REFRESH_TOKEN_KEY, nextRefreshToken);
      await loadXnoUser(nextAccessToken);
      setLoginOpen(false);
      return;
    }

    const response = await fetch(config.auth.loginPath, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ email: input.email.trim(), password: input.password }),
    });
    const data = await jsonResponse(response);
    const next = normalizeUser(data.user ?? data);
    if (next) setUser(next);
    else await loadGatewaySession();
    setLoginOpen(false);
  }, [config, loadGatewaySession, loadXnoUser]);

  const signOut = useCallback(async () => {
    if (config.auth.provider === 'local-profile') {
      localStorage.removeItem(LOCAL_PROFILE_KEY);
    } else if (config.auth.provider === 'xno-firebase') {
      clearXnoTokens();
    } else {
      const response = await fetch(config.auth.logoutPath, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Sign out failed (${response.status}).`);
    }
    setUser(null);
  }, [clearXnoTokens, config.auth.logoutPath, config.auth.provider]);

  const value = useMemo<AuthContextValue>(() => ({
    config,
    user,
    loading,
    loginOpen,
    accessToken,
    signIn,
    signOut,
    openLogin: () => setLoginOpen(true),
    closeLogin: () => setLoginOpen(false),
  }), [config, user, loading, loginOpen, accessToken, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
