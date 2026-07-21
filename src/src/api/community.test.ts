import { afterEach, describe, expect, it, vi } from 'vitest';
import { communityApi } from './community';

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('communityApi', () => {
  it('lists and searches the authenticated Enterprise catalog', async () => {
    localStorage.setItem('access_token', 'login-token');
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(new Response(JSON.stringify({
      success: true,
      data: {
        skills: [{
          skill_id: 'community-pdf',
          name: 'pdf',
          category: 'data',
          description: 'PDF documents',
          source: 'skills-sh/anthropics/skills/pdf',
          author: 'anthropics',
          installs: 4820,
        }],
        stats: { totalSkills: 1, totalAuthors: 1, totalInstalls: 4820 },
      },
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })));
    vi.stubGlobal('fetch', fetchMock);

    const listed = await communityApi.list();
    const searched = await communityApi.list('pdf', 'data');

    expect(listed.skills[0].skill_id).toBe('community-pdf');
    expect(searched.stats.totalInstalls).toBe(4820);
    expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/api/v1/skills`);
    expect(fetchMock.mock.calls[1][0]).toBe(`${window.location.origin}/api/v1/skills/search?q=pdf&category=data`);
    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('Authorization')).toBe('Bearer login-token');
  });

  it('resolves an Enterprise skill source for local installation', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      success: true,
      data: {
        skill_id: 'community-pdf',
        name: 'pdf',
        category: 'data',
        description: 'PDF documents',
        source: 'skills-sh/anthropics/skills/pdf',
        author: 'anthropics',
        installs: 4820,
      },
    }), { status: 202, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', fetchMock);

    const selected = await communityApi.install('community-pdf');

    expect(selected.source).toBe('skills-sh/anthropics/skills/pdf');
    expect(fetchMock.mock.calls[0][0]).toBe(`${window.location.origin}/api/v1/skills/community-pdf/install`);
    expect(fetchMock.mock.calls[0][1].method).toBe('POST');
  });
});
