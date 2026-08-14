import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../runtime', () => ({
  xnobrainRuntime: { features: { login: true } },
}));

vi.mock('../authStorage', () => ({
  storedAccessToken: () => 'access-token',
}));

import { ApiError, requestRaw } from './client';

describe('authenticated API client', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('adds the saved bearer token when login is enabled', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    await requestRaw('/xnobrain/api/runtime/v1/teams');

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(init.headers).get('Authorization')).toBe('Bearer access-token');
  });

  it('retries a transient API failure three times before succeeding', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal('fetch', fetchMock);

    const responsePromise = requestRaw('/xnobrain/api/runtime/v1/teams');
    await vi.runAllTimersAsync();

    await expect(responsePromise).resolves.toMatchObject({ status: 204 });
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('retries network failures three times before returning the final error', async () => {
    vi.useFakeTimers();
    const networkError = new TypeError('Failed to fetch');
    const fetchMock = vi.fn().mockRejectedValue(networkError);
    vi.stubGlobal('fetch', fetchMock);

    const responsePromise = requestRaw('/xnobrain/api/runtime/v1/teams');
    const rejection = expect(responsePromise).rejects.toBe(networkError);
    await vi.runAllTimersAsync();

    await rejection;
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it('does not retry non-transient API errors', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ message: 'Invalid request' }),
      { status: 400, statusText: 'Bad Request', headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await expect(requestRaw('/xnobrain/api/runtime/v1/teams')).rejects.toEqual(
      new ApiError(400, 'Invalid request', { message: 'Invalid request' }),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
