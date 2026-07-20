import { useEffect, useState } from 'react';
import { cronsApi } from '../api/crons';
import type { CronJob } from '../types';

function nextRunFrom(intervalMinutes: number): string {
  return new Date(Date.now() + intervalMinutes * 60_000).toISOString();
}

/** Manages cron/scheduled jobs (list + create/stop-start/delete). */
export function useCrons() {
  const [crons, setCrons] = useState<CronJob[]>([]);

  useEffect(() => {
    cronsApi.list().then(setCrons);
  }, []);

  const createCron = async (input: { agentId: string; name: string; prompt: string; intervalMinutes: number; forever: boolean }) => {
    const job = await cronsApi.create(input);
    setCrons((prev) => [job, ...prev]);
  };

  const toggleCron = (id: string) => {
    setCrons((prev) =>
      prev.map((c) => {
        if (c.id !== id) return c;
        const nextState = c.state === 'stopped' ? 'scheduled' : 'stopped';
        cronsApi.setState(id, nextState);
        return nextState === 'scheduled'
          ? { ...c, state: nextState, nextRun: nextRunFrom(c.intervalMinutes) }
          : { ...c, state: nextState };
      }),
    );
  };

  const deleteCron = (id: string) => {
    cronsApi.remove(id);
    setCrons((prev) => prev.filter((c) => c.id !== id));
  };

  return { crons, createCron, toggleCron, deleteCron };
}
