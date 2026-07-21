import type { CommunityCatalog, CommunitySkill } from '../types';
import { request } from './client';

const CATALOG_ROOT = '/api/v1/skills';

function queryString(query: string, category: string): string {
  const params = new URLSearchParams();
  if (query.trim()) params.set('q', query.trim());
  if (category && category !== 'all') params.set('category', category);
  const encoded = params.toString();
  return encoded ? `?${encoded}` : '';
}

export const communityApi = {
  async list(query = '', category = 'all', signal?: AbortSignal): Promise<CommunityCatalog> {
    const path = query.trim() ? `${CATALOG_ROOT}/search` : CATALOG_ROOT;
    return request<CommunityCatalog>(`${path}${queryString(query, category)}`, { signal });
  },

  async install(skillId: string, signal?: AbortSignal): Promise<CommunitySkill> {
    return request<CommunitySkill>(`${CATALOG_ROOT}/${encodeURIComponent(skillId)}/install`, {
      method: 'POST',
      signal,
    });
  },
};
