import { useCallback, useEffect, useState } from 'react';
import {
  analyticsApi,
  type AnalyticsQuery,
  type Bucket,
  type BudgetPatch,
  type SelectableAgent,
  type UsageSummary,
} from '../api/analytics';

export type AnalyticsControls = {
  /** Empty = all agents. */
  agents: string[];
  /** Relative window in days; ignored when `from`/`to` are set. */
  days: number;
  from?: string;
  to?: string;
  bucket: Bucket;
};

export type AnalyticsStatus = 'idle' | 'loading' | 'ready' | 'error';

const STORAGE_KEY = 'brain4all.analytics.controls';
const DEFAULT_CONTROLS: AnalyticsControls = { agents: [], days: 30, bucket: 'day' };

function loadControls(): AnalyticsControls {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_CONTROLS;
    const parsed = JSON.parse(raw) as Partial<AnalyticsControls>;
    return {
      agents: Array.isArray(parsed.agents) ? parsed.agents.map(String) : [],
      days: typeof parsed.days === 'number' ? parsed.days : 30,
      from: typeof parsed.from === 'string' ? parsed.from : undefined,
      to: typeof parsed.to === 'string' ? parsed.to : undefined,
      bucket: (['hour', 'day', 'week', 'month'] as Bucket[]).includes(parsed.bucket as Bucket)
        ? (parsed.bucket as Bucket)
        : 'day',
    };
  } catch {
    return DEFAULT_CONTROLS;
  }
}

function toQuery(controls: AnalyticsControls): AnalyticsQuery {
  const base: AnalyticsQuery = { agents: controls.agents, bucket: controls.bucket };
  if (controls.from || controls.to) return { ...base, from: controls.from, to: controls.to };
  return { ...base, days: controls.days };
}

export type AnalyticsState = {
  controls: AnalyticsControls;
  setControls: (next: AnalyticsControls) => void;
  available: SelectableAgent[];
  summary: UsageSummary | null;
  status: AnalyticsStatus;
  error: string | null;
  generatedAt: string | null;
  refresh: () => Promise<void>;
  setBudget: (agentId: string, patch: BudgetPatch) => Promise<void>;
};

export function useAnalytics(active: boolean): AnalyticsState {
  const [controls, setControlsState] = useState<AnalyticsControls>(() => loadControls());
  const [available, setAvailable] = useState<SelectableAgent[]>([]);
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [status, setStatus] = useState<AnalyticsStatus>('idle');
  const [error, setError] = useState<string | null>(null);

  const setControls = useCallback((next: AnalyticsControls) => {
    setControlsState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* storage may be unavailable; controls still live in state */
    }
  }, []);

  const loadAgents = useCallback(async () => {
    try {
      setAvailable(await analyticsApi.agents());
    } catch {
      /* the picker degrades to whatever the summary reports */
    }
  }, []);

  const refresh = useCallback(async () => {
    setStatus((current) => (current === 'ready' ? 'ready' : 'loading'));
    setError(null);
    try {
      setSummary(await analyticsApi.usage(toQuery(controls)));
      setStatus('ready');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to load usage');
      setStatus('error');
    }
  }, [controls]);

  useEffect(() => {
    if (active) void loadAgents();
  }, [active, loadAgents]);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  const setBudget = useCallback(
    async (agentId: string, patch: BudgetPatch) => {
      await analyticsApi.setBudget(agentId, patch);
      await refresh();
    },
    [refresh],
  );

  return {
    controls,
    setControls,
    available,
    summary,
    status,
    error,
    generatedAt: summary?.generated_at ?? null,
    refresh,
    setBudget,
  };
}
