import { request } from './client';

export type BlendStrategy = 'fallback' | 'round-robin' | 'fusion';

export type Blend = {
  id: string;
  name: string;
  display_name: string;
  system: boolean;
  read_only: boolean;
  models: string[];
  strategy: BlendStrategy;
  judge_model: string | null;
  sticky_limit: number | null;
  sticky_limit_scope: string;
  created_at: string;
  updated_at: string;
};

export type BlendModel = { id: string; provider: string; name: string };

export type BlendCreateInput = {
  name: string;
  models: string[];
  strategy?: BlendStrategy;
  judge_model?: string | null;
  sticky_limit?: number | null;
};

export type BlendPatchInput = {
  name?: string;
  models?: string[];
  strategy?: BlendStrategy;
  judge_model?: string | null;
  sticky_limit?: number | null;
};

const ROOT = '/api/brain/v1/blends';
const enc = (value: string) => encodeURIComponent(value);

export const blendsApi = {
  list: async (): Promise<Blend[]> => (await request<{ blends: Blend[] }>(ROOT)).blends,
  availableModels: async (): Promise<BlendModel[]> =>
    (await request<{ data: BlendModel[] }>(`${ROOT}/available-models`)).data,
  create: (input: BlendCreateInput): Promise<Blend> =>
    request<Blend>(ROOT, { method: 'POST', body: JSON.stringify(input) }),
  update: (id: string, input: BlendPatchInput): Promise<Blend> =>
    request<Blend>(`${ROOT}/${enc(id)}`, { method: 'PATCH', body: JSON.stringify(input) }),
  remove: async (id: string): Promise<void> => {
    await request<unknown>(`${ROOT}/${enc(id)}`, { method: 'DELETE' });
  },
};
