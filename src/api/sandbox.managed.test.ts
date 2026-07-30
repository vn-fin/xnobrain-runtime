import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../runtime', () => ({
  brain4AllRuntime: {
    edition: 'cloud',
    api: {
      remoteBaseUrl: 'https://runtime.example.com',
      controlBaseUrl: 'https://control.example.com',
    },
    auth: { provider: 'xno-firebase' },
    features: { login: true },
  },
}));

vi.mock('../authStorage', () => ({
  storedAccessToken: () => 'access-token',
}));

import { sandboxApi } from './sandbox';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('managed workspace VM API', () => {
  it('streams JSON percentage and message updates from XNOBrain', async () => {
    const stream = [
      'event: progress\ndata: {"percent":0,"message":"Initializing VM provisioning"}\n\n',
      'event: progress\ndata: {"percent":40,"message":"Creating Incus VM"}\n\n',
      'event: complete\ndata: {"percent":100,"message":"VM is ready"}\n\n',
    ].join('');
    const fetchMock = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    const updates: Array<{ percent: number; message: string }> = [];

    await sandboxApi.setupStream((progress) => updates.push(progress));

    expect(updates).toEqual([
      { percent: 0, message: 'Initializing VM provisioning' },
      { percent: 40, message: 'Creating Incus VM' },
      { percent: 100, message: 'VM is ready' },
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://control.example.com/api/brain-control/v1/workspace/create');
    expect(init?.method).toBe('POST');
    const headers = new Headers(init?.headers);
    expect(headers.get('Authorization')).toBe('Bearer access-token');
    expect(headers.get('Accept')).toBe('text/event-stream');
  });
});
