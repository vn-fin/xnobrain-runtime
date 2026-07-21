import { describe, expect, it } from 'vitest';
import { communityApi, computeCommunityStats } from './community';

describe('communityApi (smoke)', () => {
  it('returns a catalog of skills with derived stats', async () => {
    const catalog = await communityApi.list();

    expect(catalog.skills.length).toBeGreaterThan(0);
    // Every entry carries the identifiers the install path needs.
    for (const skill of catalog.skills) {
      expect(skill.skill_id).toBeTruthy();
      expect(skill.source).toBeTruthy();
      expect(skill.author).toBeTruthy();
    }

    // Stats are derived from the returned list, not hard-coded.
    const expected = computeCommunityStats(catalog.skills);
    expect(catalog.stats).toEqual(expected);
    expect(catalog.stats.totalSkills).toBe(catalog.skills.length);
    expect(catalog.stats.totalInstalls).toBe(
      catalog.skills.reduce((sum, s) => sum + s.installs, 0),
    );
    expect(catalog.stats.totalAuthors).toBeLessThanOrEqual(catalog.skills.length);
  });

  it('rejects when the request is aborted', async () => {
    const controller = new AbortController();
    const pending = communityApi.list(controller.signal);
    controller.abort();
    await expect(pending).rejects.toThrow();
  });
});
