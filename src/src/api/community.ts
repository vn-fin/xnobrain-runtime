import type { CommunityCatalog, CommunitySkill, CommunityStats } from '../types';

// ---------------------------------------------------------------------------
// SMOKE SEAM — community skills catalog.
//
// The community catalog is served by a separate service that is not wired up
// yet. This module returns example data with the exact shape the UI expects so
// the Community tab renders end-to-end. When the real endpoint lands, replace
// the body of `communityApi.list` with a `request<CommunityCatalogDTO>(...)`
// call and a mapper — the returned `CommunityCatalog` shape should stay stable
// so no component or hook changes are required.
//
//   const { data } = await requestWithMeta<CommunityCatalogDTO>(CATALOG_ROOT);
//   return { skills: data.skills.map(mapCommunitySkill), stats: mapStats(data) };
// ---------------------------------------------------------------------------

// export const CATALOG_ROOT = '/community/v1/skills';

/** Simulated network latency for the smoke catalog, in milliseconds. */
const SMOKE_LATENCY_MS = 250;

const SMOKE_SKILLS: CommunitySkill[] = [
  {
    skill_id: 'community-pdf',
    name: 'pdf',
    category: 'data',
    description: 'Read, split, and extract structured content from PDF documents.',
    source: 'skills-sh/anthropics/skills/pdf',
    author: 'anthropics',
    installs: 4820,
    version: '2.1.0',
    updatedAt: '2026-07-12T09:00:00Z',
    rating: 4.8,
    ratingCount: 312,
    tags: ['documents', 'extraction', 'ocr'],
    verified: true,
    homepage: 'https://skills.sh/anthropics/pdf',
  },
  {
    skill_id: 'community-web-search',
    name: 'web-search',
    category: 'web',
    description: 'Query the open web and summarize results with source citations.',
    source: 'skills-sh/anthropics/skills/web-search',
    author: 'anthropics',
    installs: 3910,
    version: '1.6.2',
    updatedAt: '2026-07-05T14:30:00Z',
    rating: 4.6,
    ratingCount: 264,
    tags: ['search', 'research', 'citations'],
    verified: true,
  },
  {
    skill_id: 'community-github',
    name: 'github',
    category: 'code',
    description: 'Open issues and pull requests, review diffs, and comment on GitHub.',
    source: 'github.com/openai/skills/github',
    author: 'openai',
    installs: 3120,
    version: '3.0.1',
    updatedAt: '2026-07-18T11:15:00Z',
    rating: 4.7,
    ratingCount: 198,
    tags: ['git', 'issues', 'pull-requests'],
    verified: true,
  },
  {
    skill_id: 'community-spreadsheet',
    name: 'spreadsheet',
    category: 'data',
    description: 'Build and edit spreadsheets, run formulas, and export CSV/XLSX.',
    source: 'skills-sh/anthropics/skills/spreadsheet',
    author: 'anthropics',
    installs: 2740,
    version: '1.3.0',
    updatedAt: '2026-06-28T08:45:00Z',
    rating: 4.5,
    ratingCount: 173,
    tags: ['excel', 'csv', 'formulas'],
    verified: true,
  },
  {
    skill_id: 'community-browser',
    name: 'browser',
    category: 'web',
    description: 'Drive a headless browser to navigate pages and fill out forms.',
    source: 'skills-sh/community/browser',
    author: 'lumora-community',
    installs: 2210,
    version: '0.9.4',
    updatedAt: '2026-07-10T16:20:00Z',
    rating: 4.2,
    ratingCount: 141,
    tags: ['automation', 'scraping', 'forms'],
  },
  {
    skill_id: 'community-sql',
    name: 'sql',
    category: 'data',
    description: 'Connect to SQL databases, run read-only queries, and shape results.',
    source: 'skills-sh/community/sql',
    author: 'lumora-community',
    installs: 1980,
    version: '1.1.0',
    updatedAt: '2026-07-02T10:05:00Z',
    rating: 4.4,
    ratingCount: 120,
    tags: ['database', 'query', 'analytics'],
  },
  {
    skill_id: 'community-slack',
    name: 'slack',
    category: 'workspace',
    description: 'Post messages, read channels, and react in a connected Slack workspace.',
    source: 'github.com/openai/skills/slack',
    author: 'openai',
    installs: 1640,
    version: '2.0.0',
    updatedAt: '2026-06-20T13:40:00Z',
    rating: 4.3,
    ratingCount: 96,
    tags: ['chat', 'notifications'],
    verified: true,
  },
  {
    skill_id: 'community-image-gen',
    name: 'image-gen',
    category: 'runtime',
    description: 'Generate and edit images from text prompts through a hosted model.',
    source: 'skills-sh/community/image-gen',
    author: 'lumora-community',
    installs: 1450,
    version: '0.7.1',
    updatedAt: '2026-07-15T18:00:00Z',
    rating: 4.1,
    ratingCount: 88,
    tags: ['images', 'generation'],
  },
  {
    skill_id: 'community-calendar',
    name: 'calendar',
    category: 'workspace',
    description: 'Read availability and schedule events on a connected calendar.',
    source: 'skills-sh/community/calendar',
    author: 'lumora-community',
    installs: 1180,
    version: '1.0.2',
    updatedAt: '2026-06-30T09:25:00Z',
    rating: 4.0,
    ratingCount: 74,
    tags: ['scheduling', 'events'],
  },
];

/** Derive the header-strip aggregates from a skill list. */
export function computeCommunityStats(skills: CommunitySkill[]): CommunityStats {
  const authors = new Set(skills.map((s) => s.author));
  return {
    totalSkills: skills.length,
    totalAuthors: authors.size,
    totalInstalls: skills.reduce((sum, s) => sum + (s.installs ?? 0), 0),
  };
}

export const communityApi = {
  /**
   * Fetch the community catalog. Smoke implementation: resolves example data
   * after a short simulated delay. Swap the body for a real request when the
   * catalog service is available (see file header).
   */
  async list(signal?: AbortSignal): Promise<CommunityCatalog> {
    await new Promise<void>((resolve, reject) => {
      const timer = setTimeout(resolve, SMOKE_LATENCY_MS);
      signal?.addEventListener('abort', () => {
        clearTimeout(timer);
        reject(new DOMException('Aborted', 'AbortError'));
      });
    });
    const skills = SMOKE_SKILLS.slice();
    return { skills, stats: computeCommunityStats(skills) };
  },
};
