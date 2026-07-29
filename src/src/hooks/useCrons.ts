import { useEffect, useState } from 'react';
import { cronsApi, type CreateCronInput } from '../api/crons';
import type { CronDetail, CronJob } from '../types';

/** Manages cron/scheduled jobs (list + create/stop-start/delete). */
export function useCrons(enabled = true) {
  const [crons, setCrons] = useState<CronJob[]>([]);
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [error, setError] = useState('');
  const [pendingId, setPendingId] = useState('');
  const [detail, setDetail] = useState<CronDetail | null>(null);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    setStatus('loading');
    setError('');
    void cronsApi.list().then((jobs) => {
      if (cancelled) return;
      setCrons(jobs);
      setStatus('ready');
    }).catch((cause) => {
      if (cancelled) return;
      setError(cause instanceof Error ? cause.message : 'Could not load cron jobs.');
      setStatus('error');
    });
    return () => { cancelled = true; };
  }, [enabled]);

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
      await cronsApi.runNow(id);
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
      setDetail(loaded);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not load cron details.');
    }
  };

  const closeDetail = () => setDetail(null);

  return { crons, status, error, pendingId, detail, createCron, toggleCron, runCron, deleteCron, loadDetail, closeDetail };
}
