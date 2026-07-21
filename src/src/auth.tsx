import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { onAuthStateChanged, signInWithEmailAndPassword, signOut as firebaseSignOut, type User } from 'firebase/auth';
import { AUTH_EXPIRED_EVENT, buildApiUrl } from './api/client';
import { auth, firebaseEnabled } from './firebase';

export type AuthUser = { email: string | null; displayName: string | null };
export type BackendUser = { aud?: string; user_id: string; email: string; phone?: string; username?: string };
export type EnterprisePlan = {
  plan_id: string;
  capabilities: Record<string, boolean>;
  hardware_class: string;
  telemetry_retention_days: number;
};
type Deployment = { mode: 'local' | 'cloud' };
type BackendTokenResponse = { access_token: string; refresh_token?: string; user: BackendUser };
type AuthContextValue = {
  user: AuthUser | null;
  backendUser: BackendUser | null;
  deploymentMode: Deployment['mode'];
  enterprisePlan: EnterprisePlan | null;
  loading: boolean;
  loginOpen: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
  openLogin: () => void;
  closeLogin: () => void;
  hasEnterpriseFeature: (capability: string) => boolean;
};

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const ACCESS_TOKEN_KEY = 'access_token';
const REFRESH_TOKEN_KEY = 'refresh_token';
const USER_INFO_KEY = 'user_info';

function authApiUrl(): string {
  const base = (import.meta.env.VITE_AUTH_API_URL ?? 'https://api.dev.xnoquant.io').replace(/\/+$/, '');
  return `${base}/auth/v1/auth/token`;
}

function saveTokens(tokens: BackendTokenResponse) {
  localStorage.setItem(ACCESS_TOKEN_KEY, tokens.access_token);
  if (tokens.refresh_token) localStorage.setItem(REFRESH_TOKEN_KEY, tokens.refresh_token);
  localStorage.setItem(USER_INFO_KEY, JSON.stringify(tokens.user));
}

function clearTokens() {
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(USER_INFO_KEY);
}

async function exchangeFirebaseToken(idToken: string): Promise<BackendTokenResponse> {
  const response = await fetch(authApiUrl(), { headers: { Authorization: `Bearer ${idToken}` } });
  const body = await response.json().catch(() => undefined) as { data?: BackendTokenResponse; message?: string; error?: string } | undefined;
  if (!response.ok || !body?.data) throw new Error(body?.message ?? body?.error ?? `Authentication failed (${response.status}).`);
  return body.data;
}

async function loadEnterprisePlan(accessToken: string): Promise<EnterprisePlan | null> {
  try {
    const response = await fetch(buildApiUrl('/api/v1/enterprise/features'), {
      headers: { Accept: 'application/json', Authorization: `Bearer ${accessToken}` },
    });
    if (response.status === 401) throw new Error('Login is invalid or expired.');
    if (!response.ok) return null;
    const body = await response.json().catch(() => undefined) as { data?: EnterprisePlan } | undefined;
    return body?.data ?? null;
  } catch (cause) {
    if (cause instanceof Error && cause.message === 'Login is invalid or expired.') throw cause;
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [backendUser, setBackendUser] = useState<BackendUser | null>(null);
  const [deploymentMode, setDeploymentMode] = useState<Deployment['mode']>('local');
  const [enterprisePlan, setEnterprisePlan] = useState<EnterprisePlan | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [deploymentLoading, setDeploymentLoading] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);

  const reset = useCallback(() => {
    clearTokens();
    setUser(null);
    setBackendUser(null);
    setEnterprisePlan(null);
  }, []);

  useEffect(() => {
    let active = true;
    fetch(buildApiUrl('/api/v1/system/deployment'), { headers: { Accept: 'application/json' } })
      .then(async (response) => {
        const body = await response.json() as { data?: Deployment };
        if (active && body.data?.mode) setDeploymentMode(body.data.mode);
      })
      .finally(() => { if (active) setDeploymentLoading(false); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    const firebaseAuth = auth;
    if (!firebaseEnabled || !firebaseAuth) {
      reset();
      setAuthLoading(false);
      return;
    }
    let active = true;
    const unsubscribe = onAuthStateChanged(firebaseAuth, async (firebaseUser: User | null) => {
      if (!firebaseUser) {
        if (active) reset();
        if (active) setAuthLoading(false);
        return;
      }
      try {
        const tokens = await exchangeFirebaseToken(await firebaseUser.getIdToken());
        const plan = await loadEnterprisePlan(tokens.access_token);
        if (!active) return;
        saveTokens(tokens);
        setUser({ email: firebaseUser.email, displayName: firebaseUser.displayName });
        setBackendUser(tokens.user);
        setEnterprisePlan(plan);
        setLoginOpen(false);
      } catch {
        if (active) reset();
        await firebaseSignOut(firebaseAuth);
      } finally {
        if (active) setAuthLoading(false);
      }
    });
    return () => { active = false; unsubscribe(); };
  }, [reset]);

  const signIn = useCallback(async (email: string, password: string) => {
    if (!firebaseEnabled || !auth) throw new Error('Authentication is not configured.');
    await signInWithEmailAndPassword(auth, email, password);
  }, []);

  const signOut = useCallback(async () => {
    if (firebaseEnabled && auth) await firebaseSignOut(auth);
    reset();
  }, [reset]);

  useEffect(() => {
    const expired = () => { void signOut(); };
    window.addEventListener(AUTH_EXPIRED_EVENT, expired);
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, expired);
  }, [signOut]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    backendUser,
    deploymentMode,
    enterprisePlan,
    loading: authLoading || deploymentLoading,
    loginOpen,
    signIn,
    signOut,
    openLogin: () => setLoginOpen(true),
    closeLogin: () => setLoginOpen(false),
    hasEnterpriseFeature: (capability) => Boolean(user && enterprisePlan?.capabilities[capability]),
  }), [user, backendUser, deploymentMode, enterprisePlan, authLoading, deploymentLoading, loginOpen, signIn, signOut]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within AuthProvider');
  return context;
}
