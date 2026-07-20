import { afterEach, describe, expect, it, vi } from 'vitest';
import { workspaceApi } from './workspace';

const PDF_BYTES = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d, 0x31, 0x2e, 0x34]); // "%PDF-1.4"

function toBase64(bytes: Uint8Array): string {
  let binary = '';
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

function mockFetch(response: Response) {
  const fetchMock = vi.fn(async () => response);
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('workspaceApi.view', () => {
  it('returns a correctly-typed PDF blob when the gateway streams raw binary as octet-stream', async () => {
    mockFetch(new Response(PDF_BYTES, {
      status: 200,
      headers: { 'content-type': 'application/octet-stream' },
    }));

    const blob = await workspaceApi.view('agent-1', 'reports/report.pdf');

    // Generic octet-stream is re-typed from the extension so the browser previews inline.
    expect(blob.type).toBe('application/pdf');
    expect(blob.size).toBe(PDF_BYTES.length);
  });

  it('decodes base64 content from a JSON envelope and prefers the extension MIME over a generic one', async () => {
    const body = JSON.stringify({
      success: true,
      data: { content_base64: toBase64(PDF_BYTES), mime_type: 'application/octet-stream' },
    });
    mockFetch(new Response(body, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));

    const blob = await workspaceApi.view('agent-1', 'reports/report.pdf');

    expect(blob.type).toBe('application/pdf');
    expect(blob.size).toBe(PDF_BYTES.length);
  });

  it('keeps a specific server MIME type from JSON when it is not generic', async () => {
    const body = JSON.stringify({ content_base64: toBase64(PDF_BYTES), mime_type: 'image/png' });
    mockFetch(new Response(body, {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }));

    const blob = await workspaceApi.view('agent-1', 'unknown-name');

    expect(blob.type).toBe('image/png');
  });
});
