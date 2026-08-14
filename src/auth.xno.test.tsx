import { StrictMode, useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './i18n';
import LoginScreen from './LoginScreen';
import { AuthProvider, useAuth } from './auth';
import { setAccessToken, setTokenSession } from './authStorage';
import { AccountView } from './components/AccountView';

vi.mock('./runtime', () => ({
  xnobrainRuntime: {
    edition: 'cloud',
    api: {
      remoteBaseUrl: 'https://runtime.example.com',
      authBaseUrl: 'https://auth.example.com',
      controlBaseUrl: 'https://control.example.com',
    },
    auth: {
      mode: 'required',
      provider: 'xno-firebase',
      firebaseApiKey: 'public-firebase-key',
      tokenPath: '/auth/v1/auth/token',
      refreshPath: '/auth/v1/auth/refresh',
      mePath: '/auth/v1/me',
      controlMePath: '/xnobrain/api/control/v1/auth/me',
      bootstrapPath: '/xnobrain/api/control/v1/account/bootstrap',
      loginPath: '/xnobrain/api/control/v1/auth/login',
      logoutPath: '/xnobrain/api/control/v1/auth/logout',
    },
    features: { login: true },
  },
}));

function Trial() {
  const { sessionActive, loading } = useAuth();
  if (loading) return <span>Loading</span>;
  return sessionActive
    ? <AccountView onClose={() => undefined} onRequestSignOut={() => undefined} />
    : <LoginScreen />;
}

function LazyAccount() {
  const { loading, sessionActive } = useAuth();
  const [open, setOpen] = useState(false);
  if (loading) return <span>Loading</span>;
  if (!sessionActive) return <span>Signed out</span>;
  return open
    ? <AccountView onClose={() => setOpen(false)} onRequestSignOut={() => undefined} />
    : <button onClick={() => setOpen(true)}>Account</button>;
}

describe('XNOQuant Firebase authentication adapter', () => {
  afterEach(cleanup);

  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
    setAccessToken(null);
    vi.restoreAllMocks();
  });

  it('uses the auth API for Firebase exchange and identity lookup', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          idToken: 'firebase-token',
          refreshToken: 'firebase-refresh-token',
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: { access_token: 'access-token', refresh_token: 'xno-refresh-token' },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            user_id: 'user-01',
            email: 'kim@example.com',
            username: 'Kim',
            fullname: 'Nguyen Tan Kim',
            roles: ['admin'],
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            user_id: 'user-01',
            email: 'kim@example.com',
            username: 'Kim',
            fullname: 'Nguyen Tan Kim',
            roles: ['admin'],
          },
        }),
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<AuthProvider><Trial /></AuthProvider>);
    await screen.findByRole('heading', { name: 'Sign in' });
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'kim@example.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'secret-password' } });
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByText('Nguyen Tan Kim')).toBeInTheDocument();
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://auth.example.com/auth/v1/auth/token',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer firebase-token' }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'https://auth.example.com/auth/v1/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer access-token' }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      'https://control.example.com/xnobrain/api/control/v1/auth/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer access-token' }),
      }),
    );
    expect(localStorage.getItem('xnobrain.firebase.refresh-token')).toBeNull();
    expect(sessionStorage.getItem('xnobrain.xno.access-token')).toBe('access-token');
    expect(sessionStorage.getItem('xnobrain.xno.refresh-token')).toBe('xno-refresh-token');
    expect(localStorage.getItem('xnobrain.xno.access-token')).toBeNull();
    expect(localStorage.getItem('xnobrain.xno.refresh-token')).toBeNull();
  });

  it('reuses the saved XNO access token on reload without refreshing Firebase', async () => {
    setTokenSession({
      accessToken: 'saved-xno-access-token',
      refreshToken: 'saved-xno-refresh-token',
      accessExpiresAt: Date.now() + 300_000,
      refreshExpiresAt: Date.now() + 3_600_000,
    });
    localStorage.setItem('xnobrain.xno.access-token', 'legacy-access-token');
    localStorage.setItem('xnobrain.xno.refresh-token', 'legacy-refresh-token');
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            user_id: 'user-01',
            email: 'kim@example.com',
            fullname: 'Nguyen Tan Kim',
            roles: ['admin'],
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            user_id: 'user-01',
            email: 'kim@example.com',
            fullname: 'Nguyen Tan Kim',
            roles: ['admin'],
          },
        }),
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<StrictMode><AuthProvider><LazyAccount /></AuthProvider></StrictMode>);
    expect(await screen.findByRole('button', { name: 'Account' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'https://auth.example.com/auth/v1/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer saved-xno-access-token' }),
      }),
    );
    expect(localStorage.getItem('xnobrain.xno.access-token')).toBeNull();
    expect(localStorage.getItem('xnobrain.xno.refresh-token')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Account' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://control.example.com/xnobrain/api/control/v1/auth/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer saved-xno-access-token' }),
      }),
    );
    expect(await screen.findByText('Nguyen Tan Kim')).toBeInTheDocument();
  });

  it('rotates an expired access token through the XNO refresh endpoint', async () => {
    setTokenSession({
      accessToken: 'expired-xno-access-token',
      refreshToken: 'saved-xno-refresh-token',
      accessExpiresAt: Date.now() - 60_000,
      refreshExpiresAt: Date.now() + 3_600_000,
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            access_token: 'rotated-xno-access-token',
            refresh_token: 'rotated-xno-refresh-token',
            access_expires_at: Date.now() + 300_000,
            refresh_expires_at: Date.now() + 3_600_000,
          },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: {
            user_id: 'user-01',
            email: 'kim@example.com',
            fullname: 'Nguyen Tan Kim',
            roles: ['admin'],
          },
        }),
      });
    vi.stubGlobal('fetch', fetchMock);

    render(<AuthProvider><LazyAccount /></AuthProvider>);
    expect(await screen.findByRole('button', { name: 'Account' })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      'https://auth.example.com/auth/v1/auth/refresh',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ refresh_token: 'saved-xno-refresh-token' }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      'https://auth.example.com/auth/v1/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer rotated-xno-access-token' }),
      }),
    );
    expect(sessionStorage.getItem('xnobrain.xno.access-token')).toBe('rotated-xno-access-token');
    expect(sessionStorage.getItem('xnobrain.xno.refresh-token')).toBe('rotated-xno-refresh-token');
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('securetoken.googleapis.com'))).toBe(false);
  });

  it('clears the session and requires login when the XNO refresh token is rejected', async () => {
    setTokenSession({
      accessToken: 'expired-xno-access-token',
      refreshToken: 'revoked-xno-refresh-token',
      accessExpiresAt: Date.now() - 60_000,
      refreshExpiresAt: Date.now() + 3_600_000,
    });
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ message: 'authentication failed' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<AuthProvider><LazyAccount /></AuthProvider>);

    expect(await screen.findByText('Signed out')).toBeInTheDocument();
    expect(sessionStorage.getItem('xnobrain.xno.access-token')).toBeNull();
    expect(sessionStorage.getItem('xnobrain.xno.refresh-token')).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('securetoken.googleapis.com'))).toBe(false);
  });
});
