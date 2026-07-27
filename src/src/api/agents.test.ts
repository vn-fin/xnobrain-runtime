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
        name: 'Research Lead',
        display_name: 'Research Lead',
        description: 'Coordinates research.',
        config: { model: 'auto' },
      },
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const profile = await agentsApi.create('Research Lead', 'Coordinates research.');

    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      display_name: 'Research Lead',
      description: 'Coordinates research.',
    });
    expect(profile.id).toBe('a1b2c3');
    expect(profile.name).toBe('Research Lead');
    expect(profile.title).toBe('Research Lead');
    expect(profile.provider).toBe('nine-router');
    expect(profile.workspace).toBe('a1b2c3/workspace');
  });

  it('renames only the profile display name', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: {
        id: 'a1b2c3',
        name: 'Operations Lead',
        display_name: 'Operations Lead',
        description: 'Coordinates research.',
        config: { model: 'auto' },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const profile = await agentsApi.rename('a1b2c3', 'Operations Lead');

    expect(fetchMock.mock.calls[0][0]).toBe('http://localhost:3000/agent-gateway/v1/agents/a1b2c3/metadata');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      display_name: 'Operations Lead',
    });
    expect(profile.name).toBe('Operations Lead');
    expect(profile.title).toBe('Operations Lead');
  });

  it('routes an upstream model selection through the single Hermes provider', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: null,
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        success: true,
        data: {
          id: 'a1b2c3',
          name: 'Research Lead',
          display_name: 'Research Lead',
          config: { model: 'cx/gpt-5.4' },
        },
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await agentsApi.update('a1b2c3', { provider: 'codex', model: 'cx/gpt-5.4' });

    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      provider: 'nine-router',
      model: 'cx/gpt-5.4',
    });
  });

  it('calls the permanent assistant delete endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: {
        deleted: true,
        recoverable: false,
        kanban_tasks_deleted: 2,
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    await agentsApi.remove('a1b2c3');

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:3000/agent-gateway/v1/agents/a1b2c3/delete',
      expect.objectContaining({ method: 'DELETE' }),
    );
  });
});
