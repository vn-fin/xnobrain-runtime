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
  signIn: (input: LoginInput) => Promise<void>;
  signOut: () => Promise<void>;
  openLogin: () => void;
  closeLogin: () => void;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const LOCAL_PROFILE_KEY = 'brain4all.local-profile';

function normalizeUser(value: unknown): ActiveUser | null {
  if (!value || typeof value !== 'object') return null;
  const user = value as Record<string, unknown>;
  const userId = String(user.user_id ?? user.userId ?? '').trim();
  const email = String(user.email ?? '').trim();
  if (!userId && !email) return null;
  return {
    userId: userId || email,
    email,
    displayName: String(user.display_name ?? user.displayName ?? user.username ?? email).trim(),
    tenantId: String(user.tenant_id ?? user.tenantId ?? '').trim(),
    planId: String(user.plan_id ?? user.planId ?? '').trim(),
    roles: Array.isArray(user.roles) ? user.roles.map(String) : [],
  };
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
  const [loading, setLoading] = useState(config.auth.provider === 'gateway');
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
    void loadGatewaySession()
      .catch(() => {
        if (active) setUser(null);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [config.auth.mode, config.auth.provider, loadGatewaySession]);

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
  }, [config, loadGatewaySession]);

  const signOut = useCallback(async () => {
    if (config.auth.provider === 'local-profile') {
      localStorage.removeItem(LOCAL_PROFILE_KEY);
    } else {
      const response = await fetch(config.auth.logoutPath, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error(`Sign out failed (${response.status}).`);
    }
    setUser(null);
  }, [config.auth.logoutPath, config.auth.provider]);

  const value = useMemo<AuthContextValue>(() => ({
    config,
    user,
    loading,
    loginOpen,
    signIn,
    signOut,
    openLogin: () => setLoginOpen(true),
    closeLogin: () => setLoginOpen(false),
  }), [config, user, loading, loginOpen, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
