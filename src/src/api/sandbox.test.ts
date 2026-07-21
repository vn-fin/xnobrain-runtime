import { afterEach, describe, expect, it, vi } from 'vitest';
import { sandboxApi } from './sandbox';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe('sandboxApi', () => {
  it('maps one-second runtime SSE stats events', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(new Headers(init?.headers).get('Accept')).toBe('text/event-stream');
      return new Response(
        'event: stats\ndata: {"info":{"id":"runtime","status":"running","type":"container"},"metrics":{"cpu_percent":7.5},"health":{"healthy":true},"updated_at":"2026-07-21T00:00:00Z"}\n\n',
        { status: 200, headers: { 'content-type': 'text/event-stream' } },
      );
    });
    vi.stubGlobal('fetch', fetchMock);
    const updates: number[] = [];

    await sandboxApi.stream((result) => updates.push(result.data?.metrics.cpuPercent ?? -1));

    expect(updates).toEqual([7.5]);
    expect(String(fetchMock.mock.calls[0][0])).toContain('/sandboxes/v1/me/sandboxes/detail/stream');
  });
});
