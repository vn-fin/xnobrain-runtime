const PROVIDER_AUTH_POPUP_NAME = 'assistant_studio_provider_auth';
const OAUTH_CALLBACK_CHANNEL = 'oauth_callback';
const OAUTH_CALLBACK_STORAGE_KEY = 'oauth_callback';

let providerAuthPopup: Window | null = null;

type OAuthCallbackData = {
  code?: unknown;
  token?: unknown;
  state?: unknown;
  error?: unknown;
  errorDescription?: unknown;
  error_description?: unknown;
  fullUrl?: unknown;
};

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function popupFeatures(): string {
  const width = Math.min(600, Math.max(360, window.screen.availWidth - 32));
  const height = Math.min(700, Math.max(520, window.screen.availHeight - 32));
  const left = Math.max(0, Math.round(window.screenX + (window.outerWidth - width) / 2));
  const top = Math.max(0, Math.round(window.screenY + (window.outerHeight - height) / 2));
  return [
    'popup=yes',
    `width=${width}`,
    `height=${height}`,
    `left=${left}`,
    `top=${top}`,
    'resizable=yes',
    'scrollbars=yes',
  ].join(',');
}

export function openProviderAuthPopup(url = 'about:blank'): Window | null {
  providerAuthPopup = window.open(url, PROVIDER_AUTH_POPUP_NAME, popupFeatures());
  providerAuthPopup?.focus();
  return providerAuthPopup;
}

export function navigateProviderAuthPopup(url: string): Window | null {
  const destination = url.trim();
  if (!destination) return null;
  if (!providerAuthPopup || providerAuthPopup.closed) {
    return openProviderAuthPopup(destination);
  }
  providerAuthPopup.location.assign(destination);
  providerAuthPopup.focus();
  return providerAuthPopup;
}

export function closeProviderAuthPopup(): void {
  if (providerAuthPopup && !providerAuthPopup.closed) providerAuthPopup.close();
  providerAuthPopup = null;
}

export function providerAuthPopupExists(): boolean {
  return providerAuthPopup !== null;
}

export function providerAuthPopupIsOpen(): boolean {
  return Boolean(providerAuthPopup && !providerAuthPopup.closed);
}

export function normalizeOAuthCallbackText(value: unknown, allowRawCode = false): string {
  const text = stringValue(value);
  if (!text) return '';

  try {
    const callbackURL = new URL(text);
    if (
      callbackURL.searchParams.has('code')
      || callbackURL.searchParams.has('token')
      || callbackURL.searchParams.has('error')
    ) {
      return text;
    }
  } catch {
    // Claude can return a copyable code in the form "code#state".
  }

  if (allowRawCode && text.length >= 16 && !/\s/.test(text) && text.includes('#')) {
    return text;
  }
  return '';
}

export function oauthCallbackTextFromData(value: unknown): string {
  if (!value || typeof value !== 'object') return '';
  const data = value as OAuthCallbackData;
  const fullURL = normalizeOAuthCallbackText(data.fullUrl);
  if (fullURL) return fullURL;

  const code = stringValue(data.code) || stringValue(data.token);
  const error = stringValue(data.error);
  if (!code && !error) return '';

  const callbackURL = new URL('http://localhost/callback');
  if (code) callbackURL.searchParams.set(data.token ? 'token' : 'code', code);
  if (error) callbackURL.searchParams.set('error', error);
  const state = stringValue(data.state);
  if (state) callbackURL.searchParams.set('state', state);
  const description = stringValue(data.errorDescription) || stringValue(data.error_description);
  if (description) callbackURL.searchParams.set('error_description', description);
  return callbackURL.toString();
}

export async function readProviderOAuthCallbackFromClipboard(): Promise<string> {
  if (!navigator.clipboard?.readText) return '';
  try {
    return normalizeOAuthCallbackText(await navigator.clipboard.readText(), true);
  } catch {
    return '';
  }
}

export function subscribeProviderOAuthCallbacks(onCallback: (callbackText: string) => void): () => void {
  const receive = (value: unknown) => {
    const callbackText = oauthCallbackTextFromData(value);
    if (callbackText) onCallback(callbackText);
  };

  const handleMessage = (event: MessageEvent) => {
    if (event.origin !== window.location.origin || event.data?.type !== OAUTH_CALLBACK_CHANNEL) return;
    if (providerAuthPopup && event.source && event.source !== providerAuthPopup) return;
    receive(event.data.data);
  };
  window.addEventListener('message', handleMessage);

  let channel: BroadcastChannel | null = null;
  try {
    channel = new BroadcastChannel(OAUTH_CALLBACK_CHANNEL);
    channel.onmessage = (event) => receive(event.data);
  } catch {
    channel = null;
  }

  const handleStorage = (event: StorageEvent) => {
    if (event.key !== OAUTH_CALLBACK_STORAGE_KEY || !event.newValue) return;
    try {
      receive(JSON.parse(event.newValue));
      localStorage.removeItem(OAUTH_CALLBACK_STORAGE_KEY);
    } catch {
      // Ignore malformed callback data from another same-origin page.
    }
  };
  window.addEventListener('storage', handleStorage);

  try {
    const stored = localStorage.getItem(OAUTH_CALLBACK_STORAGE_KEY);
    if (stored) {
      receive(JSON.parse(stored));
      localStorage.removeItem(OAUTH_CALLBACK_STORAGE_KEY);
    }
  } catch {
    // Storage may be disabled by the browser.
  }

  return () => {
    window.removeEventListener('message', handleMessage);
    window.removeEventListener('storage', handleStorage);
    channel?.close();
  };
}
