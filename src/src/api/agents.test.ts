import { afterEach, describe, expect, it, vi } from 'vitest';
import { agentsApi } from './agents';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('agentsApi profile display names', () => {
  it('creates a generated-id profile using display_name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: {
        id: 'a1b2c3',
        name: 'a1b2c3',
        display_name: 'Research Lead',
        description: 'Coordinates research.',
        config: { provider: 'nine-router', model: 'auto' },
      },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const profile = await agentsApi.create('Research Lead', 'Coordinates research.');

    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      display_name: 'Research Lead',
      description: 'Coordinates research.',
    });
    expect(profile.id).toBe('a1b2c3');
    expect(profile.name).toBe('a1b2c3');
    expect(profile.title).toBe('Research Lead');
  });

  it('renames only the profile display name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: {
        id: 'a1b2c3',
        name: 'a1b2c3',
        display_name: 'Operations Lead',
        description: 'Coordinates research.',
        config: { provider: 'nine-router', model: 'auto' },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const profile = await agentsApi.rename('a1b2c3', 'Operations Lead');

    expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:3000/agent-gateway/v1/agents/a1b2c3/metadata');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      display_name: 'Operations Lead',
    });
    expect(profile.name).toBe('a1b2c3');
    expect(profile.title).toBe('Operations Lead');
  });
});
