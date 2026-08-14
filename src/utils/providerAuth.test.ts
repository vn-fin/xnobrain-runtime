import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  closeProviderAuthPopup,
  navigateProviderAuthPopup,
  normalizeOAuthCallbackText,
  oauthCallbackTextFromData,
  openProviderAuthPopup,
  subscribeProviderOAuthCallbacks,
} from './providerAuth';

afterEach(() => {
  closeProviderAuthPopup();
  localStorage.clear();
  vi.restoreAllMocks();
});

describe('provider OAuth popup', () => {
  it('opens a named, constrained popup and reuses it for the login URL', () => {
    const assign = vi.fn();
    const popup = {
      closed: false,
      close: vi.fn(),
      focus: vi.fn(),
      location: { assign },
    } as unknown as Window;
    const open = vi.spyOn(window, 'open').mockReturnValue(popup);

    expect(openProviderAuthPopup()).toBe(popup);
    expect(open).toHaveBeenCalledWith(
      'about:blank',
      'assistant_studio_provider_auth',
      expect.stringContaining('popup=yes'),
    );

    expect(navigateProviderAuthPopup('https://provider.example/authorize')).toBe(popup);
    expect(assign).toHaveBeenCalledWith('https://provider.example/authorize');
    expect(open).toHaveBeenCalledTimes(1);
  });
});

describe('provider OAuth callbacks', () => {
  it('accepts callback URLs and Claude code-state values but rejects ordinary clipboard text', () => {
    expect(normalizeOAuthCallbackText('http://localhost:1455/auth/callback?code=abc&state=xyz')).toContain('code=abc');
    expect(normalizeOAuthCallbackText('authorization-code#oauth-state', true)).toBe('authorization-code#oauth-state');
    expect(normalizeOAuthCallbackText('an unrelated clipboard value', true)).toBe('');
  });

  it('turns a 9router callback payload into the full value expected by the gateway', () => {
    const callback = oauthCallbackTextFromData({ code: 'code-123', state: 'state-456' });
    const parsed = new URL(callback);

    expect(parsed.searchParams.get('code')).toBe('code-123');
    expect(parsed.searchParams.get('state')).toBe('state-456');
  });

  it('accepts callback messages only from the XNOBrain origin', () => {
    const receive = vi.fn();
    const unsubscribe = subscribeProviderOAuthCallbacks(receive);

    window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://untrusted.example',
      data: { type: 'oauth_callback', data: { code: 'wrong' } },
    }));
    window.dispatchEvent(new MessageEvent('message', {
      origin: window.location.origin,
      data: { type: 'oauth_callback', data: { code: 'right', state: 'state' } },
    }));

    expect(receive).toHaveBeenCalledTimes(1);
    expect(receive.mock.calls[0][0]).toContain('code=right');
    unsubscribe();
  });
});
