import { afterEach, describe, expect, it, vi } from 'vitest';
import { systemApi } from './api';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('systemApi', () => {
  it('reads the non-secret deployment summary for Settings', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ success: true, data: { mode: 'local', runtime_transport: 'in-process' } }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);

    const deployment = await systemApi.deployment();

    expect(deployment.mode).toBe('local');
    expect(deployment.runtime_transport).toBe('in-process');
  });

  it('downloads a standard ZIP archive in parts', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/brain/v1/bundles/exports')) {
        return new Response(JSON.stringify({ success: true, data: {
          export_id: 'export-1', filename: 'profile.zip', size: 4,
          sha256: '0'.repeat(64), chunk_size: 4, total_parts: 1,
        } }), { status: 201, headers: { 'content-type': 'application/json' } });
      }
      if (url.includes('/parts/0')) {
        return new Response(new Uint8Array([0x50, 0x4b, 0x03, 0x04]).buffer, {
          status: 200, headers: { 'content-type': 'application/octet-stream' },
        });
      }
      return new Response(JSON.stringify({ success: true, data: { deleted: true } }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const bundle = await systemApi.export(['a12345']);

    expect(bundle.blob.size).toBe(4);
    expect(bundle.filename).toBe('profile.zip');
    expect(bundle.blob.type).toBe('application/zip');
    const [, init] = fetchMock.mock.calls[0];
    expect(init?.method).toBe('POST');
    expect(init?.body).toContain('a12345');
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/parts/0'))).toBe(true);
  });

  it('requests an explicit Team snapshot without relying on coordinator matching', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/brain/v1/bundles/exports')) {
        return new Response(JSON.stringify({ success: true, data: {
          export_id: 'export-team', filename: 'team.zip', size: 1,
          sha256: '0'.repeat(64), chunk_size: 4, total_parts: 1,
        } }), { status: 201, headers: { 'content-type': 'application/json' } });
      }
      if (url.includes('/parts/0')) return new Response(new Uint8Array([1]));
      return new Response(JSON.stringify({ success: true, data: { deleted: true } }), {
        status: 200, headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    await systemApi.export([], undefined, ['team-1']);

    const body = JSON.parse(String(fetchMock.mock.calls[0][1]?.body));
    expect(body).toEqual({ agent_ids: [], team_ids: ['team-1'] });
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

    const result = await systemApi.inspect(new File(['bundle'], 'profiles.zip'));

    expect(result.files).toBe(3);
  });

  it('uploads profile archives as parts before server-side merge', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith('/api/brain/v1/bundles/uploads')) {
        return new Response(JSON.stringify({ success: true, data: {
          upload_id: 'upload-1', filename: 'profile.zip', size: 5,
          sha256: '', chunk_size: 3, total_parts: 2,
        } }), { status: 201, headers: { 'content-type': 'application/json' } });
      }
      if (url.endsWith('/complete')) {
        return new Response(JSON.stringify({ success: true, data: {
          upload_id: 'upload-1', filename: 'profile.zip', size: 5,
          sha256: '0'.repeat(64), chunk_size: 3, total_parts: 2, complete: true,
          preview: { inspection: { manifest: { agents: [] }, files: 2, expanded_bytes: 5, warnings: [] } },
        } }), { status: 200, headers: { 'content-type': 'application/json' } });
      }
      return new Response(JSON.stringify({ success: true, data: { part_number: 0 } }), {
        status: 201, headers: { 'content-type': 'application/json' },
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const progress: number[] = [];
    const result = await systemApi.upload(new File(['abcde'], 'profile.zip'), (item) => progress.push(item.percent));

    const partCalls = fetchMock.mock.calls.filter(([input]) => String(input).includes('/parts/'));
    expect(partCalls).toHaveLength(2);
    expect((partCalls[0][1]?.body as Blob).size).toBe(3);
    expect((partCalls[1][1]?.body as Blob).size).toBe(2);
    expect(progress).toEqual([60, 100]);
    expect(result.complete).toBe(true);
  });
});
