export const FIREBASE_REFRESH_TOKEN_KEY = 'brain4all.firebase.refresh-token';
export const XNO_ACCESS_TOKEN_KEY = 'brain4all.xno.access-token';
export const XNO_REFRESH_TOKEN_KEY = 'brain4all.xno.refresh-token';

let accessToken: string | null = null;

export function storedAccessToken() {
  return accessToken;
}

export function setAccessToken(value: string | null) {
  accessToken = value?.trim() || null;
}

export function clearLegacyXnoTokens() {
  localStorage.removeItem(XNO_ACCESS_TOKEN_KEY);
  localStorage.removeItem(XNO_REFRESH_TOKEN_KEY);
}
