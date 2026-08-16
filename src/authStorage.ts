export const FIREBASE_REFRESH_TOKEN_KEY = 'xnobrain.firebase.refresh-token';
export const XNO_ACCESS_TOKEN_KEY = 'xnobrain.xno.access-token';
export const XNO_REFRESH_TOKEN_KEY = 'xnobrain.xno.refresh-token';
/** Same-origin refresh-token copy used to restore a session in a new tab. */
export const XNO_SHARED_REFRESH_TOKEN_KEY = 'xnobrain.xno.shared-refresh-token';

const XNO_ACCESS_EXPIRES_AT_KEY = 'xnobrain.xno.access-expires-at';
const XNO_REFRESH_EXPIRES_AT_KEY = 'xnobrain.xno.refresh-expires-at';
const EXPIRY_SKEW_MS = 30_000;

let accessToken: string | null = null;
let accessExpiresAt: number | null = null;
const AUTH_CHANNEL_NAME = 'xnobrain.auth-session.v1';
type TokenSessionMessage = {
  type: 'request' | 'session';
  requestId: string;
  accessToken?: string;
  refreshToken?: string;
  accessExpiresAt?: number | null;
  refreshExpiresAt?: number | null;
};
const pendingSessionRequests = new Map<string, (message?: TokenSessionMessage) => void>();
const authChannel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(AUTH_CHANNEL_NAME) : null;

function storedValue(key: string): string | null {
  try {
    return sessionStorage.getItem(key)?.trim() || null;
  } catch {
    return null;
  }
}

function sharedStoredValue(key: string): string | null {
  try {
    return localStorage.getItem(key)?.trim() || null;
  } catch {
    return null;
  }
}

function setSharedStoredValue(key: string, value?: string | number | null) {
  try {
    if (value === undefined || value === null || value === '') localStorage.removeItem(key);
    else localStorage.setItem(key, String(value));
  } catch {
    // Browsers can disable web storage.
  }
}

function setStoredValue(key: string, value?: string | number | null) {
  try {
    if (value === undefined || value === null || value === '') sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, String(value));
  } catch {
    // Browsers can disable web storage. The in-memory access token still works
    // for the current page in that case.
  }
}

// Migrate an already-open authenticated tab as soon as this module loads. This
// matters when Vite hot-reloads the app after the user signed in with an older
// build, before the shared refresh-token key existed.
const initialSessionRefreshToken = storedValue(XNO_REFRESH_TOKEN_KEY);
if (initialSessionRefreshToken) setSharedStoredValue(XNO_SHARED_REFRESH_TOKEN_KEY, initialSessionRefreshToken);

function expiryMilliseconds(value: unknown): number | null {
  const expiry = Number(value);
  if (!Number.isFinite(expiry) || expiry <= 0) return null;
  return expiry < 1_000_000_000_000 ? expiry * 1_000 : expiry;
}

function mirrorSessionRefreshToken() {
  const sessionRefreshToken = storedValue(XNO_REFRESH_TOKEN_KEY);
  // Do not let an older tab overwrite a rotated token written by another tab.
  if (sessionRefreshToken && !sharedStoredValue(XNO_SHARED_REFRESH_TOKEN_KEY)) {
    setSharedStoredValue(XNO_SHARED_REFRESH_TOKEN_KEY, sessionRefreshToken);
  }
}

function unexpiredToken(tokenKey: string, expiryKey: string): string | null {
  const token = storedValue(tokenKey);
  if (!token) return null;
  const expiresAt = expiryMilliseconds(storedValue(expiryKey));
  if (expiresAt !== null && expiresAt <= Date.now() + EXPIRY_SKEW_MS) {
    setStoredValue(tokenKey, null);
    setStoredValue(expiryKey, null);
    return null;
  }
  return token;
}

export function storedAccessToken() {
  if (accessToken && (accessExpiresAt === null || accessExpiresAt > Date.now() + EXPIRY_SKEW_MS)) {
    mirrorSessionRefreshToken();
    return accessToken;
  }
  accessToken = null;
  accessExpiresAt = null;
  accessToken = unexpiredToken(XNO_ACCESS_TOKEN_KEY, XNO_ACCESS_EXPIRES_AT_KEY);
  accessExpiresAt = expiryMilliseconds(storedValue(XNO_ACCESS_EXPIRES_AT_KEY));
  // Migrate sessions created before cross-tab recovery was introduced. The
  // access token remains tab-scoped; only the refresh token is shared.
  mirrorSessionRefreshToken();
  return accessToken;
}

export function storedRefreshToken() {
  const sessionToken = unexpiredToken(XNO_REFRESH_TOKEN_KEY, XNO_REFRESH_EXPIRES_AT_KEY);
  if (sessionToken) return sessionToken;
  return sharedStoredValue(XNO_SHARED_REFRESH_TOKEN_KEY);
}

export function storedAccessTokenExpiresAt() {
  return accessExpiresAt ?? expiryMilliseconds(storedValue(XNO_ACCESS_EXPIRES_AT_KEY));
}

function currentTokenSession(): TokenSessionMessage | null {
  const currentAccessToken = storedAccessToken();
  const currentRefreshToken = storedRefreshToken();
  if (!currentAccessToken || !currentRefreshToken) return null;
  return {
    type: 'session',
    requestId: '',
    accessToken: currentAccessToken,
    refreshToken: currentRefreshToken,
    accessExpiresAt: storedAccessTokenExpiresAt(),
    refreshExpiresAt: expiryMilliseconds(storedValue(XNO_REFRESH_EXPIRES_AT_KEY)),
  };
}

export function setAccessToken(value: string | null, expiresAt?: unknown) {
  accessToken = value?.trim() || null;
  accessExpiresAt = expiryMilliseconds(expiresAt);
  setStoredValue(XNO_ACCESS_TOKEN_KEY, accessToken);
  setStoredValue(XNO_ACCESS_EXPIRES_AT_KEY, accessExpiresAt);
}

export function setTokenSession(session: {
  accessToken: string;
  refreshToken: string;
  accessExpiresAt?: unknown;
  refreshExpiresAt?: unknown;
}) {
  setAccessToken(session.accessToken, session.accessExpiresAt);
  const refreshToken = session.refreshToken.trim();
  const refreshExpiresAt = expiryMilliseconds(session.refreshExpiresAt);
  setStoredValue(XNO_REFRESH_TOKEN_KEY, refreshToken);
  setStoredValue(XNO_REFRESH_EXPIRES_AT_KEY, refreshExpiresAt);
  setSharedStoredValue(XNO_SHARED_REFRESH_TOKEN_KEY, refreshToken);
}

export function clearTokenSession() {
  accessToken = null;
  accessExpiresAt = null;
  setStoredValue(XNO_ACCESS_TOKEN_KEY, null);
  setStoredValue(XNO_REFRESH_TOKEN_KEY, null);
  setStoredValue(XNO_ACCESS_EXPIRES_AT_KEY, null);
  setStoredValue(XNO_REFRESH_EXPIRES_AT_KEY, null);
  setSharedStoredValue(XNO_SHARED_REFRESH_TOKEN_KEY, null);
}

export function clearLegacyXnoTokens() {
  localStorage.removeItem(XNO_ACCESS_TOKEN_KEY);
  localStorage.removeItem(XNO_REFRESH_TOKEN_KEY);
}

if (authChannel) {
  authChannel.addEventListener('message', (event: MessageEvent<TokenSessionMessage>) => {
    const message = event.data;
    if (!message || typeof message.requestId !== 'string') return;
    if (message.type === 'request') {
      const session = currentTokenSession();
      if (session) authChannel.postMessage({ ...session, requestId: message.requestId });
      return;
    }
    if (message.type === 'session') pendingSessionRequests.get(message.requestId)?.(message);
  });
}

/** Securely bootstrap a same-origin new tab without putting tokens in URLs. */
export function requestCrossTabTokenSession(timeoutMs = 600): Promise<boolean> {
  if (storedAccessToken() && storedRefreshToken()) return Promise.resolve(true);
  if (!authChannel) return Promise.resolve(false);
  const requestId = typeof globalThis.crypto?.randomUUID === 'function'
    ? globalThis.crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return new Promise((resolve) => {
    const finish = (message?: TokenSessionMessage) => {
      pendingSessionRequests.delete(requestId);
      window.clearTimeout(timer);
      if (message?.accessToken && message.refreshToken) {
        setTokenSession({
          accessToken: message.accessToken,
          refreshToken: message.refreshToken,
          accessExpiresAt: message.accessExpiresAt,
          refreshExpiresAt: message.refreshExpiresAt,
        });
        resolve(true);
      } else resolve(false);
    };
    const timer = window.setTimeout(() => finish(), timeoutMs);
    pendingSessionRequests.set(requestId, finish);
    authChannel.postMessage({ type: 'request', requestId } satisfies TokenSessionMessage);
  });
}
