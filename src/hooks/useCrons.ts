import { useEffect, useState } from 'react';
import { cronsApi, type CreateCronInput } from '../api/crons';
import type { CronBlueprint, CronDeliveryOption, CronDetail, CronJob, CronJobRun } from '../types';

/** Manages cron/scheduled jobs (list + create/stop-start/delete). */
export function useCrons(enabled = true) {
  const [crons, setCrons] = useState<CronJob[]>([]);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');
  const [pendingId, setPendingId] = useState('');
  const [detail, setDetail] = useState<CronDetail | null>(null);
  const [blueprints, setBlueprints] = useState<CronBlueprint[]>([]);
  const [deliveryOptions, setDeliveryOptions] = useState<CronDeliveryOption[]>([]);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setStatus('loading');
    setError('');
    void Promise.all([
      cronsApi.list(),
      cronsApi.listBlueprints().catch(() => []),
      cronsApi.listDeliveryTargetOptions().catch(() => []),
    ]).then(([jobs, templates, options]) => {
      if (cancelled) return;
      setCrons(jobs);
      setBlueprints(templates);
      setDeliveryOptions(options);
      setStatus('ready');
    }).catch((cause) => {
      if (cancelled) return;
      setError(cause instanceof Error ? cause.message : 'Could not load cron jobs.');
      setStatus('error');
    });
    return () => { cancelled = true; };
  }, [enabled]);

  const instantiateBlueprint = async (input: { blueprint: string; agentId: string; values: Record<string, unknown>; deliverTargets?: Array<{ targetType: string; destination: string }> }) => {
    const job = await cronsApi.instantiateBlueprint(input);
    setCrons((prev) => [job, ...prev]);
    return job;
  };

  const addJobTarget = async (id: string, target: { targetType: string; destination: string }) => {
    const added = await cronsApi.addJobTarget(id, target);
    setDetail((current) => current?.job.id === id ? { ...current, targets: [...(current.targets ?? []), added] } : current);
    return added;
  };

  const removeJobTarget = async (id: string, targetId: string) => {
    await cronsApi.removeJobTarget(id, targetId);
    setDetail((current) => current?.job.id === id ? { ...current, targets: (current.targets ?? []).filter((item) => item.id !== targetId) } : current);
  };

  const loadRuns = async (id: string): Promise<CronJobRun[]> => {
    const runs = await cronsApi.listRuns(id);
    setDetail((current) => current?.job.id === id ? { ...current, runs } : current);
    return runs;
  };

  const createCron = async (input: CreateCronInput) => {
    setPendingId('create');
    setError('');
    try {
      const job = await cronsApi.create(input);
      setCrons((prev) => [job, ...prev]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not create cron job.');
      throw cause;
    } finally {
      setPendingId('');
    }
  };

  const toggleCron = async (id: string) => {
    const current = crons.find((job) => job.id === id);
    if (!current) return;
    setPendingId(id);
    setError('');
    try {
      const job = await cronsApi.setState(id, current.state === 'stopped' ? 'scheduled' : 'stopped');
      setCrons((prev) => prev.map((item) => item.id === id ? job : item));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update cron job.');
    } finally {
      setPendingId('');
    }
  };

  const runCron = async (id: string) => {
    setPendingId(id);
    setError('');
    try {
      const triggered = await cronsApi.runNow(id);
      setDetail(triggered);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start cron job.');
    } finally {
      setPendingId('');
    }
  };

  const deleteCron = async (id: string) => {
    setPendingId(id);
    setError('');
    try {
      await cronsApi.remove(id);
      setCrons((prev) => prev.filter((item) => item.id !== id));
      setDetail((current) => current?.job.id === id ? null : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not delete cron job.');
    } finally {
      setPendingId('');
    }
  };

  const loadDetail = async (id: string) => {
    try {
      const loaded = await cronsApi.detail(id);
      setDetail((current) => (
        loaded.run === null && current?.job.id === id && current.run?.state === 'running'
          ? { ...loaded, run: current.run }
          : loaded
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load cron details.');
    }
  };

  const closeDetail = () => setDetail(null);

  return { crons, status, error, pendingId, detail, blueprints, deliveryOptions, createCron, instantiateBlueprint, addJobTarget, removeJobTarget, loadRuns, toggleCron, runCron, deleteCron, loadDetail, closeDetail };
}
