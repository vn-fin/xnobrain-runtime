import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../runtime', () => ({
  brain4AllRuntime: { features: { login: true } },
}));

vi.mock('../authStorage', () => ({
  storedAccessToken: () => 'access-token',
}));

import { requestRaw } from './client';

describe('authenticated API client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('adds the saved bearer token when login is enabled', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await requestRaw('/api/brain/v1/teams');

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer access-token');
  });
});
