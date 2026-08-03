import { afterEach, describe, expect, it, vi } from 'vitest';
import { conversationsApi } from './conversations';

afterEach(() => vi.unstubAllGlobals());

describe('conversationsApi usage', () => {
  it('lets the runtime choose a unique title for a default session', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: { id: 'session-two', title: 'New Session 2' },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await conversationsApi.create('agent-one');

    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({});
  });

  it('maps current context occupancy separately from cumulative tokens', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: {
        conversation_id: 'session-one',
        model: 'cx/gpt-5.6-luna',
        tokens: { total: 42_000 },
        context: { used: 10_000, limit: 200_000, percent: 5 },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));

    const usage = await conversationsApi.usage('agent-one', 'session-one');

    expect(usage.totalTokens).toBe(42_000);
    expect(usage.contextUsed).toBe(10_000);
    expect(usage.contextLimit).toBe(200_000);
    expect(usage.contextPercent).toBe(5);
  });
});
