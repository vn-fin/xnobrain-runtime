import { StrictMode, useState } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import './i18n';
import LoginScreen from './LoginScreen';
import { AuthProvider, useAuth } from './auth';
import { AccountView } from './components/AccountView';

vi.mock('./runtime', () => ({
  brain4AllRuntime: {
    edition: 'cloud',
    api: {
      remoteBaseUrl: 'https://runtime.example.com',
      controlBaseUrl: 'https://control.example.com',
    },
    auth: {
      mode: 'optional',
      provider: 'xno-firebase',
      firebaseApiKey: 'public-firebase-key',
      tokenPath: '/api/brain-control/v1/auth/token',
      refreshPath: '/api/brain-control/v1/auth/refresh',
      mePath: '/api/brain-control/v1/auth/me',
      bootstrapPath: '/api/brain-control/v1/bootstrap',
      loginPath: '/api/brain-control/v1/auth/login',
      logoutPath: '/api/brain-control/v1/auth/logout',
    },
    features: { login: true },
  },
}));

function Trial() {
  const { sessionActive, loading } = useAuth();
  if (loading) return <span>Loading</span>;
  return sessionActive
    ? <AccountView onClose={() => undefined} onRequestSignOut={() => undefined} />
    : <LoginScreen optional />;
}

function LazyAccount() {
  const { sessionActive } = useAuth();
  const [open, setOpen] = useState(false);
  if (!sessionActive) return <span>Signed out</span>;
  return open
    ? <AccountView onClose={() => setOpen(false)} onRequestSignOut={() => undefined} />
    : <button onClick={() => setOpen(true)}>Account</button>;
}

describe('XNOQuant Firebase authentication adapter', () => {
  afterEach(cleanup);

  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('uses the control API for Firebase exchange and identity lookup', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ idToken: 'firebase-token' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          success: true,
          data: { access_token: 'access-token', refresh_token: 'refresh-token' },
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
      'https://control.example.com/api/brain-control/v1/auth/token',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer firebase-token' }),
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      'https://control.example.com/api/brain-control/v1/auth/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer access-token' }),
      }),
    );
    expect(localStorage.getItem('brain4all.xno.refresh-token')).toBe('refresh-token');
  });

  it('does not request /me on refresh and loads it once when Account is opened', async () => {
    localStorage.setItem('brain4all.xno.access-token', 'saved-access-token');
    localStorage.setItem('brain4all.xno.refresh-token', 'saved-refresh-token');
    const fetchMock = vi.fn().mockResolvedValue({
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
    expect(fetchMock).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Account' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(
      'https://control.example.com/api/brain-control/v1/auth/me',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer saved-access-token' }),
      }),
    );
    expect(await screen.findByText('Nguyen Tan Kim')).toBeInTheDocument();
  });
});
