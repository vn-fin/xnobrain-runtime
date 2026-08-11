import { request } from './client';
import type { CronBlueprint, CronDeliveryOption, CronDeliveryTarget, CronDetail, CronJob, CronJobRun } from '../types';

const ROOT = '/xnobrain/api/runtime/v1/cron/jobs';
const CRON_ROOT = '/xnobrain/api/runtime/v1/cron';
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
  occurrence_id?: string;
  deliveries?: Array<{ target_id: string; target_type: 'channel' | 'email' | 'kanban' | 'file'; status: 'delivered' | 'failed' | 'degraded'; at: string; reason?: string | null }>;
};
type TargetDTO = { id: string; target_type: 'channel' | 'email' | 'kanban' | 'file'; destination: string; available: boolean; degraded_reason?: string | null; name?: string };
type BlueprintDTO = Omit<CronBlueprint, 'scheduleHuman'> & { schedule_human?: string; scheduleHuman?: string };

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
    occurrenceId: value.occurrence_id,
    deliveries: (value.deliveries ?? []).map((item) => ({ targetId: item.target_id, targetType: item.target_type, status: item.status, at: item.at, reason: item.reason })),
  };
}

function mapTarget(value: TargetDTO): CronDeliveryTarget {
  return { id: value.id, targetType: value.target_type, destination: value.destination, available: value.available, degradedReason: value.degraded_reason };
}

function mapRunHistory(value: CronRunDTO): CronJobRun {
  return { ...(mapRun(value) as NonNullable<CronDetail['run']>), deliveries: (value.deliveries ?? []).map((item) => ({ targetId: item.target_id, targetType: item.target_type, status: item.status, at: item.at, reason: item.reason })) };
}

function scoped(path: string, agentId?: string): string {
  return agentId ? `${path}?agent_id=${encodeURIComponent(agentId)}` : path;
}

export const cronsApi = {
  list: async (agentId?: string) => {
    const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    return (await request<CronDTO[]>(`${ROOT}${query}`)).map(mapCron);
  },
  detail: async (id: string, agentId?: string): Promise<CronDetail> => {
    const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    const value = await request<{ job: CronDTO; run: CronRunDTO | null; targets?: TargetDTO[]; runs?: CronRunDTO[] }>(`${ROOT}/${encodeURIComponent(id)}${query}`);
    return { job: mapCron(value.job), run: mapRun(value.run), targets: (value.targets ?? []).map(mapTarget), runs: (value.runs ?? []).map(mapRunHistory) };
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
  setState: async (id: string, state: 'scheduled' | 'stopped', agentId?: string) =>
    mapCron(await request<CronDTO>(scoped(`${ROOT}/${encodeURIComponent(id)}/${state === 'scheduled' ? 'resume' : 'pause'}`, agentId), { method: 'POST' })),
  runNow: async (id: string, agentId?: string): Promise<CronDetail> => {
    const value = await request<{ job: CronDTO; run: CronRunDTO | null }>(
      scoped(`${ROOT}/${encodeURIComponent(id)}/run`, agentId),
      { method: 'POST' },
    );
    return { job: mapCron(value.job), run: mapRun(value.run) };
  },
  listBlueprints: async (): Promise<CronBlueprint[]> => {
    const value = await request<{ blueprints: BlueprintDTO[] }>(`${CRON_ROOT}/blueprints`);
    return value.blueprints.map((item) => ({ ...item, scheduleHuman: item.schedule_human ?? item.scheduleHuman ?? '' }));
  },
  instantiateBlueprint: async (input: { blueprint: string; agentId: string; values: Record<string, unknown>; deliverTargets?: Array<{ targetType: string; destination: string }> }): Promise<CronJob> =>
    mapCron(await request<CronDTO>(`${CRON_ROOT}/blueprints/instantiate`, { method: 'POST', body: JSON.stringify({ blueprint: input.blueprint, agent_id: input.agentId, values: input.values, deliver_targets: (input.deliverTargets ?? []).map((item) => ({ target_type: item.targetType, destination: item.destination })) }) })),
  listDeliveryTargetOptions: async (agentId?: string): Promise<CronDeliveryOption[]> => {
    const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    const value = await request<{ options: TargetDTO[] }>(`${CRON_ROOT}/delivery-targets${query}`);
    return value.options.map((item) => ({ ...mapTarget(item), name: item.name ?? item.id }));
  },
  listJobTargets: async (id: string, agentId?: string): Promise<CronDeliveryTarget[]> => {
    const value = await request<{ targets: TargetDTO[] }>(scoped(`${ROOT}/${encodeURIComponent(id)}/delivery-targets`, agentId));
    return value.targets.map(mapTarget);
  },
  addJobTarget: async (id: string, target: { targetType: string; destination: string }, agentId?: string): Promise<CronDeliveryTarget> =>
    mapTarget(await request<TargetDTO>(scoped(`${ROOT}/${encodeURIComponent(id)}/delivery-targets`, agentId), { method: 'POST', body: JSON.stringify({ target_type: target.targetType, destination: target.destination }) })),
  removeJobTarget: (id: string, targetId: string, agentId?: string) => request(scoped(`${ROOT}/${encodeURIComponent(id)}/delivery-targets/${encodeURIComponent(targetId)}`, agentId), { method: 'DELETE' }),
  listRuns: async (id: string, agentId?: string): Promise<CronJobRun[]> => {
    const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : '';
    const value = await request<{ runs: CronRunDTO[] }>(`${ROOT}/${encodeURIComponent(id)}/runs${query}`);
    return value.runs.map(mapRunHistory);
  },
  remove: (id: string, agentId?: string) => request(scoped(`${ROOT}/${encodeURIComponent(id)}`, agentId), { method: 'DELETE' }),
};
