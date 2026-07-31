import { useEffect, useState } from 'react';
import { cronsApi, type CreateCronInput } from '../api/crons';
import type { CronBlueprint, CronDeliveryOption, CronDetail, CronJob, CronJobRun } from '../types';

/** Manages cron/scheduled jobs (list + create/stop-start/delete). */
export function useCrons(enabled = true, profileIds: string[] = []) {
  const [crons, setCrons] = useState<CronJob[]>([]);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');
  const [pendingId, setPendingId] = useState('');
  const [detail, setDetail] = useState<CronDetail | null>(null);
  const [blueprints, setBlueprints] = useState<CronBlueprint[]>([]);
  const [deliveryOptions, setDeliveryOptions] = useState<CronDeliveryOption[]>([]);
  const [profileStates, setProfileStates] = useState<Record<string, 'loading' | 'ready' | 'error'>>({});
  const [deliveryOptionsProfile, setDeliveryOptionsProfile] = useState('');
  const profileKey = profileIds.join('\u0000');
  const jobAgentId = (id: string, agentId?: string) => agentId
    ?? crons.find((job) => job.id === id)?.agentId
    ?? detail?.job.agentId;

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const ids = profileKey ? profileKey.split('\u0000') : [];
    setStatus('loading');
    setError('');
    setCrons([]);
    setProfileStates(Object.fromEntries(ids.map((id) => [id, 'loading'])));
    setDeliveryOptions([]);
    setDeliveryOptionsProfile('');

    void cronsApi.listBlueprints().then((templates) => {
      if (!cancelled) setBlueprints(templates);
    }).catch(() => {
      if (!cancelled) setBlueprints([]);
    });

    if (ids.length === 0) {
      setStatus('ready');
      return () => { cancelled = true; };
    }

    let remaining = ids.length;
    let failures = 0;
    for (const id of ids) {
      void cronsApi.list(id).then((jobs) => {
        if (cancelled) return;
        setCrons((current) => [...current.filter((job) => job.agentId !== id), ...jobs]);
        setProfileStates((current) => ({ ...current, [id]: 'ready' }));
      }).catch((cause) => {
        if (cancelled) return;
        failures += 1;
        setProfileStates((current) => ({ ...current, [id]: 'error' }));
        setError(cause instanceof Error ? cause.message : `Could not load cron jobs for ${id}.`);
      }).finally(() => {
        if (cancelled) return;
        remaining -= 1;
        if (remaining === 0) setStatus(failures === ids.length ? 'error' : 'ready');
      });
    }
    return () => { cancelled = true; };
  }, [enabled, profileKey]);

  const instantiateBlueprint = async (input: { blueprint: string; agentId: string; values: Record<string, unknown>; deliverTargets?: Array<{ targetType: string; destination: string }> }) => {
    const job = await cronsApi.instantiateBlueprint(input);
    setCrons((prev) => [job, ...prev]);
    return job;
  };

  const addJobTarget = async (id: string, target: { targetType: string; destination: string }, agentId?: string) => {
    const added = await cronsApi.addJobTarget(id, target, jobAgentId(id, agentId));
    setDetail((current) => current?.job.id === id ? { ...current, targets: [...(current.targets ?? []), added] } : current);
    return added;
  };

  const removeJobTarget = async (id: string, targetId: string, agentId?: string) => {
    await cronsApi.removeJobTarget(id, targetId, jobAgentId(id, agentId));
    setDetail((current) => current?.job.id === id ? { ...current, targets: (current.targets ?? []).filter((item) => item.id !== targetId) } : current);
  };

  const loadRuns = async (id: string, requestedAgentId?: string): Promise<CronJobRun[]> => {
    const agentId = jobAgentId(id, requestedAgentId);
    const runs = await cronsApi.listRuns(id, agentId);
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

  const toggleCron = async (id: string, agentId?: string) => {
    const current = crons.find((job) => job.id === id && (!agentId || job.agentId === agentId));
    if (!current) return;
    setPendingId(id);
    setError('');
    try {
      const job = await cronsApi.setState(id, current.state === 'stopped' ? 'scheduled' : 'stopped', current.agentId);
      setCrons((prev) => prev.map((item) => item.id === id ? job : item));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not update cron job.');
    } finally {
      setPendingId('');
    }
  };

  const runCron = async (id: string, agentId?: string) => {
    setPendingId(id);
    setError('');
    try {
      const triggered = await cronsApi.runNow(id, jobAgentId(id, agentId));
      setDetail(triggered);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not start cron job.');
    } finally {
      setPendingId('');
    }
  };

  const deleteCron = async (id: string, agentId?: string) => {
    setPendingId(id);
    setError('');
    try {
      await cronsApi.remove(id, jobAgentId(id, agentId));
      setCrons((prev) => prev.filter((item) => item.id !== id || (!!agentId && item.agentId !== agentId)));
      setDetail((current) => current?.job.id === id && (!agentId || current.job.agentId === agentId) ? null : current);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not delete cron job.');
    } finally {
      setPendingId('');
    }
  };

  const loadDetail = async (id: string, requestedAgentId?: string) => {
    try {
      const agentId = jobAgentId(id, requestedAgentId);
      const loaded = await cronsApi.detail(id, agentId);
      setDetail((current) => (
        loaded.run === null && current?.job.id === id && current.run?.state === 'running'
          ? { ...loaded, run: current.run }
          : loaded
      ));
      if (loaded.job.agentId !== deliveryOptionsProfile) {
        void cronsApi.listDeliveryTargetOptions(loaded.job.agentId).then((options) => {
          setDeliveryOptions(options);
          setDeliveryOptionsProfile(loaded.job.agentId);
        }).catch(() => setDeliveryOptions([]));
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load cron details.');
    }
  };

  const closeDetail = () => setDetail(null);

  return { crons, status, profileStates, error, pendingId, detail, blueprints, deliveryOptions, createCron, instantiateBlueprint, addJobTarget, removeJobTarget, loadRuns, toggleCron, runCron, deleteCron, loadDetail, closeDetail };
}
