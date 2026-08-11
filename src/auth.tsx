import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  clearLegacyXnoTokens,
  FIREBASE_REFRESH_TOKEN_KEY,
  setAccessToken as setStoredAccessToken,
  storedAccessToken,
} from './authStorage';
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
};

type AuthContextValue = {
  config: RuntimeConfig;
  user: ActiveUser | null;
  loading: boolean;
  loginOpen: boolean;
  accessToken: string | null;
  sessionActive: boolean;
  signIn: (input: LoginInput) => Promise<void>;
  signOut: () => Promise<void>;
  loadCurrentUser: () => Promise<ActiveUser | null>;
  openLogin: () => void;
  closeLogin: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
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
  const authBaseUrl = (
    import.meta.env.VITE_AUTH_API_URL?.trim()
    || config.api.authBaseUrl
    || import.meta.env.VITE_CONTROL_API_BASE_URL?.trim()
    || config.api.controlBaseUrl
    || config.api.remoteBaseUrl
  ).replace(/\/+$/, '');
  const accountBaseUrl = (
    import.meta.env.VITE_CONTROL_API_BASE_URL?.trim()
    || config.api.controlBaseUrl
    || authBaseUrl
  ).replace(/\/+$/, '');
  const [user, setUser] = useState<ActiveUser | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(() => (
    config.auth.provider === 'xno-firebase' ? storedAccessToken() : null
  ));
  const [loading, setLoading] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);
  const currentUserRequest = useRef<Promise<ActiveUser | null> | null>(null);
  const accountUserRequest = useRef<{ token: string; request: Promise<ActiveUser> } | null>(null);

  const loadGatewaySession = useCallback(async () => {
    const response = await fetch(remoteURL(accountBaseUrl, config.auth.bootstrapPath), {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (response.status === 401) {
      setUser(null);
      return null;
    }
    const data = await jsonResponse(response);
    const next = normalizeUser(data.user ?? data);
    setUser(next);
    return next;
  }, [accountBaseUrl, config.auth.bootstrapPath]);

  const clearXnoTokens = useCallback(() => {
    clearLegacyXnoTokens();
    setStoredAccessToken(null);
    setAccessToken(null);
  }, []);

  const saveXnoAccessToken = useCallback((token: string) => {
    setStoredAccessToken(token);
    setAccessToken(token);
  }, []);

  const loadXnoUser = useCallback(async (token: string) => {
    const response = await fetch(remoteURL(authBaseUrl, config.auth.mePath), {
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${token}`,
      },
    });
    const data = await jsonResponse(response);
    const next = normalizeUser(data);
    if (!next) throw new Error('The account API returned no active user.');
    saveXnoAccessToken(token);
    setUser(next);
    return next;
  }, [config.auth.mePath, authBaseUrl, saveXnoAccessToken]);

  const verifyXnoAccount = useCallback((token: string): Promise<ActiveUser> => {
    if (accountUserRequest.current?.token === token) return accountUserRequest.current.request;
    const request = (async () => {
      const response = await fetch(remoteURL(accountBaseUrl, config.auth.controlMePath), {
        credentials: 'omit',
        headers: {
          Accept: 'application/json',
          Authorization: `Bearer ${token}`,
        },
      });
      const data = await jsonResponse(response);
      const next = normalizeUser(data);
      if (!next) throw new Error('The public account API returned no active user.');
      setUser(next);
      return next;
    })();
    accountUserRequest.current = { token, request };
    void request.finally(() => {
      if (accountUserRequest.current?.request === request) accountUserRequest.current = null;
    }).catch(() => undefined);
    return request;
  }, [accountBaseUrl, config.auth.controlMePath]);

  const exchangeFirebaseToken = useCallback(async (firebaseToken: string) => {
    const response = await fetch(remoteURL(authBaseUrl, config.auth.tokenPath), {
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${firebaseToken}`,
      },
    });
    const data = await jsonResponse(response);
    const nextAccessToken = String(data.access_token ?? '').trim();
    if (!nextAccessToken) throw new Error('The account API returned an empty access token.');
    return loadXnoUser(nextAccessToken);
  }, [authBaseUrl, config.auth.tokenPath, loadXnoUser]);

  const refreshFirebaseToken = useCallback(async () => {
    const refreshToken = localStorage.getItem(FIREBASE_REFRESH_TOKEN_KEY)?.trim();
    if (!refreshToken) throw new Error('No saved Firebase session.');
    if (!config.auth.firebaseApiKey) throw new Error('Firebase API key is not configured.');
    const response = await fetch(
      `https://securetoken.googleapis.com/v1/token?key=${encodeURIComponent(config.auth.firebaseApiKey)}`,
      {
      method: 'POST',
      credentials: 'omit',
      headers: {
        Accept: 'application/json',
          'Content-Type': 'application/x-www-form-urlencoded',
      },
        body: new URLSearchParams({
          grant_type: 'refresh_token',
          refresh_token: refreshToken,
        }).toString(),
      },
    );
    const data = await response.json().catch(() => ({})) as Record<string, unknown>;
    if (!response.ok) {
      localStorage.removeItem(FIREBASE_REFRESH_TOKEN_KEY);
      throw new Error(String(
        (data.error as Record<string, unknown> | undefined)?.message
        ?? 'Firebase session refresh failed.',
      ));
    }
    const firebaseToken = String(data.id_token ?? '').trim();
    const nextRefreshToken = String(data.refresh_token ?? refreshToken).trim();
    if (!firebaseToken) throw new Error('Firebase returned no refreshed identity token.');
    localStorage.setItem(FIREBASE_REFRESH_TOKEN_KEY, nextRefreshToken);
    return firebaseToken;
  }, [config.auth.firebaseApiKey]);

  const restoreXnoSession = useCallback((): Promise<ActiveUser | null> => {
    if (currentUserRequest.current) return currentUserRequest.current;
    const request = (async () => {
      clearXnoTokens();
      setUser(null);
      const firebaseToken = await refreshFirebaseToken();
      return exchangeFirebaseToken(firebaseToken);
    })();
    currentUserRequest.current = request;
    void request.finally(() => {
      if (currentUserRequest.current === request) currentUserRequest.current = null;
    }).catch(() => undefined);
    return request;
  }, [clearXnoTokens, exchangeFirebaseToken, refreshFirebaseToken]);

  const loadCurrentUser = useCallback((): Promise<ActiveUser | null> => {
    if (config.auth.provider === 'gateway') return loadGatewaySession();
    const token = storedAccessToken();
    if (token) return verifyXnoAccount(token);
    if (!localStorage.getItem(FIREBASE_REFRESH_TOKEN_KEY)) return Promise.resolve(null);
    return restoreXnoSession().then((next) => {
      const refreshedToken = storedAccessToken();
      return refreshedToken ? verifyXnoAccount(refreshedToken) : next;
    });
  }, [
    config.auth.provider,
    loadGatewaySession,
    restoreXnoSession,
    verifyXnoAccount,
  ]);

  useEffect(() => {
    let active = true;
    if (config.auth.provider === 'xno-firebase') {
      clearLegacyXnoTokens();
      if (!localStorage.getItem(FIREBASE_REFRESH_TOKEN_KEY)) {
        setLoading(false);
        return undefined;
      }
      void restoreXnoSession()
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
  }, [
    clearXnoTokens,
    config.auth.provider,
    loadGatewaySession,
    restoreXnoSession,
  ]);

  const signIn = useCallback(async (input: LoginInput) => {
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
      const firebaseRefreshToken = String(firebaseData.refreshToken ?? '').trim();
      if (!firebaseToken || !firebaseRefreshToken) {
        throw new Error('Firebase returned an incomplete login session.');
      }
      localStorage.setItem(FIREBASE_REFRESH_TOKEN_KEY, firebaseRefreshToken);
      try {
        await exchangeFirebaseToken(firebaseToken);
      } catch (error) {
        clearXnoTokens();
        setUser(null);
        throw error;
      }
      setLoginOpen(false);
      return;
    }

    const response = await fetch(remoteURL(accountBaseUrl, config.auth.loginPath), {
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
  }, [accountBaseUrl, clearXnoTokens, config, exchangeFirebaseToken, loadGatewaySession]);

  const signOut = useCallback(async () => {
    if (config.auth.provider === 'xno-firebase') {
      localStorage.removeItem(FIREBASE_REFRESH_TOKEN_KEY);
      clearXnoTokens();
    } else {
      const response = await fetch(remoteURL(accountBaseUrl, config.auth.logoutPath), {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Sign out failed (${response.status}).`);
    }
    setUser(null);
  }, [accountBaseUrl, clearXnoTokens, config.auth.logoutPath, config.auth.provider]);

  const sessionActive = config.auth.provider === 'xno-firebase' ? !!accessToken : !!user;
  const value = useMemo<AuthContextValue>(() => ({
    config,
    user,
    loading,
    loginOpen,
    accessToken,
    sessionActive,
    signIn,
    signOut,
    loadCurrentUser,
    openLogin: () => setLoginOpen(true),
    closeLogin: () => setLoginOpen(false),
  }), [config, user, loading, loginOpen, accessToken, sessionActive, signIn, signOut, loadCurrentUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
