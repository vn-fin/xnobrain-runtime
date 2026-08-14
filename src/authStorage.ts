export const FIREBASE_REFRESH_TOKEN_KEY = 'xnobrain.firebase.refresh-token';
export const XNO_ACCESS_TOKEN_KEY = 'xnobrain.xno.access-token';
export const XNO_REFRESH_TOKEN_KEY = 'xnobrain.xno.refresh-token';

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

function setStoredValue(key: string, value?: string | number | null) {
  try {
    if (value === undefined || value === null || value === '') sessionStorage.removeItem(key);
    else sessionStorage.setItem(key, String(value));
  } catch {
    // Browsers can disable web storage. The in-memory access token still works
    // for the current page in that case.
  }
}

function expiryMilliseconds(value: unknown): number | null {
  const expiry = Number(value);
  if (!Number.isFinite(expiry) || expiry <= 0) return null;
  return expiry < 1_000_000_000_000 ? expiry * 1_000 : expiry;
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
    return accessToken;
  }
  accessToken = null;
  accessExpiresAt = null;
  accessToken = unexpiredToken(XNO_ACCESS_TOKEN_KEY, XNO_ACCESS_EXPIRES_AT_KEY);
  accessExpiresAt = expiryMilliseconds(storedValue(XNO_ACCESS_EXPIRES_AT_KEY));
  return accessToken;
}

export function storedRefreshToken() {
  return unexpiredToken(XNO_REFRESH_TOKEN_KEY, XNO_REFRESH_EXPIRES_AT_KEY);
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
  setStoredValue(XNO_REFRESH_TOKEN_KEY, session.refreshToken.trim());
  setStoredValue(XNO_REFRESH_EXPIRES_AT_KEY, expiryMilliseconds(session.refreshExpiresAt));
}

export function clearTokenSession() {
  accessToken = null;
  accessExpiresAt = null;
  setStoredValue(XNO_ACCESS_TOKEN_KEY, null);
  setStoredValue(XNO_REFRESH_TOKEN_KEY, null);
  setStoredValue(XNO_ACCESS_EXPIRES_AT_KEY, null);
  setStoredValue(XNO_REFRESH_EXPIRES_AT_KEY, null);
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
