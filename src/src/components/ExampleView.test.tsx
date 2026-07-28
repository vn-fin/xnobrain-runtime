import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ExampleView } from './ExampleView';

const openLogin = vi.fn();

vi.mock('../auth', () => ({
  useAuth: () => ({
    config: {
      api: { remoteBaseUrl: 'https://api.dev.xnoquant.io' },
      auth: { mePath: '/auth/v1/me' },
    },
    user: null,
    accessToken: 'access-token',
    openLogin,
  }),
}));

describe('ExampleView', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('renders /me as an authenticated identity demonstration', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        data: {
          user_id: 'user-01',
          email: 'kim@example.com',
          username: 'Kim',
          fullname: 'Nguyen Tan Kim',
          email_verified: true,
          roles: ['admin', 'researcher'],
          info: { country: 'Vietnam', kyc: { verified: true } },
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<ExampleView onClose={() => undefined} />);

    expect(await screen.findByText('Nguyen Tan Kim')).toBeInTheDocument();
    expect(screen.getByText('admin')).toBeInTheDocument();
    expect(screen.getByText('researcher')).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      'https://api.dev.xnoquant.io/auth/v1/me',
      expect.objectContaining({
        credentials: 'omit',
        headers: expect.objectContaining({ Authorization: 'Bearer access-token' }),
      }),
    ));
  });
});
