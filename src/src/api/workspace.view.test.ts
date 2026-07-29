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

describe('workspaceApi.read', () => {
  it('streams text from the file endpoint instead of using the size-limited JSON reader', async () => {
    const fetchMock = mockFetch(new Response('first chunk\nsecond chunk\n', {
      status: 200,
      headers: { 'content-type': 'text/plain' },
    }));

    const content = await workspaceApi.read('agent-1', 'logs/large.log');

    expect(content).toBe('first chunk\nsecond chunk\n');
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent-gateway/v1/agents-workspaces/agent-1/file?path=logs%2Flarge.log'),
      expect.objectContaining({ signal: undefined }),
    );
    expect(fetchMock.mock.calls[0][0]).not.toContain('/read');
  });
});

describe('workspaceApi.size', () => {
  it('reads total size from a one-byte range response', async () => {
    const fetchMock = mockFetch(new Response(new Uint8Array([0x50]), {
      status: 206,
      headers: {
        'content-length': '1',
        'content-range': 'bytes 0-0/18509277',
      },
    }));

    const size = await workspaceApi.size('agent-1', 'HPG model - 2026 v3.xlsm');

    expect(size).toBe(18_509_277);
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(request.headers).get('Range')).toBe('bytes=0-0');
  });
});

describe('workspaceApi.download', () => {
  it('returns raw bytes without attempting to decode large files', async () => {
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
    const fetchMock = mockFetch(new Response(bytes, {
      status: 200,
      headers: { 'content-type': 'application/octet-stream' },
    }));

    const blob = await workspaceApi.download('agent-1', 'reports/large.xlsm');

    expect(blob.size).toBe(bytes.length);
    const request = fetchMock.mock.calls[0][1] as RequestInit;
    expect(new Headers(request.headers).get('Accept')).toContain('application/octet-stream');
  });
});

describe('workspaceApi.preview', () => {
  it('loads the rendered Office preview from the dedicated PDF endpoint', async () => {
    const fetchMock = mockFetch(new Response(PDF_BYTES, {
      status: 200,
      headers: { 'content-type': 'application/pdf' },
    }));

    const blob = await workspaceApi.preview('agent-1', 'reports/report.xlsx');

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent-gateway/v1/agents-workspaces/agent-1/preview?path=reports%2Freport.xlsx'),
      expect.anything(),
    );
    expect(blob.type).toBe('application/pdf');
    expect(blob.size).toBe(PDF_BYTES.length);
  });
});

describe('workspaceApi.workbook', () => {
  it('loads a normalized workbook from the dedicated XLSX endpoint', async () => {
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
    const fetchMock = mockFetch(new Response(bytes, {
      status: 200,
      headers: {
        'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      },
    }));

    const blob = await workspaceApi.workbook('agent-1', 'reports/report.xlsx');

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agent-gateway/v1/agents-workspaces/agent-1/workbook?path=reports%2Freport.xlsx'),
      expect.anything(),
    );
    expect(blob.type).toBe('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');
    expect(blob.size).toBe(bytes.length);
  });

  it('requests macro-enabled workbooks through the normalized workbook endpoint', async () => {
    const bytes = new Uint8Array([0x50, 0x4b, 0x03, 0x04]);
    const fetchMock = mockFetch(new Response(bytes, { status: 200 }));

    await workspaceApi.workbook('agent-1', 'reports/forecast.xlsm');

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/workbook?path=reports%2Fforecast.xlsm'),
      expect.anything(),
    );
  });
});

describe('workspaceApi.upload', () => {
  it('uploads files as sequential sub-1 MiB chunks and reports aggregate progress', async () => {
    const requests: FormData[] = [];

    class FakeXMLHttpRequest {
      upload: { onprogress: ((event: ProgressEvent) => void) | null } = { onprogress: null };
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      onabort: (() => void) | null = null;
      status = 200;
      statusText = 'OK';
      response = JSON.stringify({ success: true, data: { complete: false } });
      responseText = this.response;
      responseType = '';

      open() {}
      setRequestHeader() {}
      abort() {
        this.onabort?.();
      }
      send(body: FormData) {
        requests.push(body);
        const chunk = body.get('chunk') as File;
        this.upload.onprogress?.({
          lengthComputable: true,
          loaded: chunk.size,
          total: chunk.size,
        } as ProgressEvent);
        queueMicrotask(() => this.onload?.());
      }
    }

    vi.stubGlobal('XMLHttpRequest', FakeXMLHttpRequest);
    const progress: number[] = [];
    const file = new File(
      [new Uint8Array((768 * 1024) + 25)],
      'large.xlsx',
      { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    );

    await workspaceApi.upload('agent-1', 'reports', file, (event) => {
      progress.push(event.percent ?? -1);
    });

    expect(requests).toHaveLength(2);
    expect(requests.map((form) => (form.get('chunk') as File).size)).toEqual([
      768 * 1024,
      25,
    ]);
    expect(requests.every((form) => form.get('file_name') === 'large.xlsx')).toBe(true);
    expect(requests.every((form) => form.get('total_chunks') === '2')).toBe(true);
    expect(progress.at(0)).toBe(0);
    expect(progress.at(-1)).toBe(100);
  });
});
