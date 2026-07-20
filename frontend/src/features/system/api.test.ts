import { afterEach, describe, expect, it, vi } from 'vitest';
import { systemApi } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('systemApi', () => {
  it('downloads a raw .lumora response without JSON decoding', async () => {
    const fetchMock = vi.fn(async () => new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04]), {
      status: 200,
      headers: { 'content-type': 'application/vnd.open-lumora.bundle' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const bundle = await systemApi.export(['a12345']);

    expect(bundle.size).toBe(4);
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe('POST');
    expect(init?.body).toContain('a12345');
  });

  it('uploads bundle inspection as multipart without overriding its boundary', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.body).toBeInstanceOf(FormData);
      expect(new Headers(init?.headers).has('Content-Type')).toBe(false);
      return new Response(JSON.stringify({ success: true, data: { manifest: { agents: [] }, files: 3, expanded_bytes: 20, warnings: [] } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const result = await systemApi.inspect(new File(['bundle'], 'profiles.lumora'));

    expect(result.files).toBe(3);
  });
});
