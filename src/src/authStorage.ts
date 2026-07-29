export const XNO_ACCESS_TOKEN_KEY = 'brain4all.xno.access-token';
export const XNO_REFRESH_TOKEN_KEY = 'brain4all.xno.refresh-token';

export function storedAccessToken() {
  return localStorage.getItem(XNO_ACCESS_TOKEN_KEY)?.trim() || null;
}
