import { request } from './client';
import type { CronJob } from '../types';

const ROOT = '/agent-gateway/v1/cron/jobs';
type CronDTO = { id: string; name: string; enabled: boolean; schedule: string; prompt: string; next_run_at?: string };

function mapCron(value: CronDTO): CronJob {
  return {
    id: value.id,
    name: value.name,
    state: value.enabled ? 'scheduled' : 'stopped',
    intervalMinutes: Number(value.schedule.match(/(\d+)m$/)?.[1] ?? 60),
    forever: true,
    nextRun: value.next_run_at ?? new Date().toISOString(),
    prompt: value.prompt,
  };
}

export const cronsApi = {
  list: async () => (await request<CronDTO[]>(ROOT)).map(mapCron),
  create: async (input: { agentId: string; name: string; prompt: string; intervalMinutes: number; forever: boolean }): Promise<CronJob> =>
    mapCron(await request<CronDTO>(ROOT, {
      method: 'POST',
      body: JSON.stringify({ agent_id: input.agentId, name: input.name, prompt: input.prompt, interval_minutes: input.intervalMinutes }),
    })),
  setState: (id: string, state: 'scheduled' | 'stopped') =>
    request(`${ROOT}/${encodeURIComponent(id)}/${state === 'scheduled' ? 'resume' : 'pause'}`, { method: 'POST' }),
  remove: (id: string) => request(`${ROOT}/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};
