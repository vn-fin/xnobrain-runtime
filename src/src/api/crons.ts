import { request } from './client';
import type { CronDetail, CronJob } from '../types';

const ROOT = '/agent-gateway/v1/cron/jobs';
type CronDTO = {
  id: string;
  agent_id: string;
  name: string;
  enabled: boolean;
  schedule: string;
  prompt: string;
  next_run_at?: string;
};
type CronRunDTO = {
  id: string;
  state: 'running' | 'success' | 'failed';
  triggered_at?: string;
  completed_at?: string;
  output?: string;
  error?: string;
};

export type CreateCronInput = {
  agentId: string;
  name: string;
  prompt: string;
  intervalMinutes: number;
};

function intervalMinutes(schedule: string): number {
  return Number(schedule.match(/(?:@?every\s+)?(\d+)m$/i)?.[1] ?? 0);
}

function mapCron(value: CronDTO): CronJob {
  return {
    id: value.id,
    agentId: value.agent_id,
    name: value.name,
    state: value.enabled ? 'scheduled' : 'stopped',
    schedule: value.schedule,
    intervalMinutes: intervalMinutes(value.schedule),
    nextRun: value.next_run_at ?? '',
    prompt: value.prompt,
  };
}

function mapRun(value: CronRunDTO | null): CronDetail['run'] {
  if (!value) return null;
  return {
    id: value.id,
    state: value.state,
    triggeredAt: value.triggered_at ?? '',
    completedAt: value.completed_at ?? '',
    output: value.output ?? '',
    error: value.error ?? '',
  };
}

export const cronsApi = {
  list: async () => (await request<CronDTO[]>(ROOT)).map(mapCron),
  detail: async (id: string): Promise<CronDetail> => {
    const value = await request<{ job: CronDTO; run: CronRunDTO | null }>(`${ROOT}/${encodeURIComponent(id)}`);
    return { job: mapCron(value.job), run: mapRun(value.run) };
  },
  create: async (input: CreateCronInput): Promise<CronJob> =>
    mapCron(await request<CronDTO>(ROOT, {
      method: 'POST',
      body: JSON.stringify({
        agent_id: input.agentId,
        name: input.name,
        prompt: input.prompt,
        interval_minutes: input.intervalMinutes,
      }),
    })),
  setState: async (id: string, state: 'scheduled' | 'stopped') =>
    mapCron(await request<CronDTO>(`${ROOT}/${encodeURIComponent(id)}/${state === 'scheduled' ? 'resume' : 'pause'}`, { method: 'POST' })),
  runNow: async (id: string): Promise<CronDetail> => {
    const value = await request<{ job: CronDTO; run: CronRunDTO | null }>(
      `${ROOT}/${encodeURIComponent(id)}/run`,
      { method: 'POST' },
    );
    return { job: mapCron(value.job), run: mapRun(value.run) };
  },
  remove: (id: string) => request(`${ROOT}/${encodeURIComponent(id)}`, { method: 'DELETE' }),
};
