import { afterEach, describe, expect, it, vi } from 'vitest';
import { skillsApi } from './skills';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('skillsApi', () => {
  it('loads the default profile catalog from the unscoped skills endpoint', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: [{
        skill_id: 'default-notes',
        name: 'default-notes',
        category: 'office',
        description: 'Default notes',
        enabled: true,
        installed: true,
        path: 'office/default-notes',
      }],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await skillsApi.listDefault();

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/xnobrain/api/runtime/v1/agents-skills`);
    expect(result.skills.map((skill) => skill.skill_id)).toEqual(['default-notes']);
  });

  it('installs a pasted source into the default Hermes profile', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: [{ skill_id: 'pdf', name: 'pdf', installed: true, enabled: true }],
    }), { status: 201, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const result = await skillsApi.installDefault({ source: 'skills-sh/anthropics/skills/pdf', enable: false });

    expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/xnobrain/api/runtime/v1/agents-skills`);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toEqual({
      source: 'skills-sh/anthropics/skills/pdf',
      enable: false,
    });
    expect(result[0].skill_id).toBe('pdf');
  });
});
