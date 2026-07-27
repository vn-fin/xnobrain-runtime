import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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

export function useAnalytics(
  active: boolean,
  agents: ReadonlyArray<{ id: string; title: string; name: string }>,
): AnalyticsState {
  const [controls, setControlsState] = useState<AnalyticsControls>(() => loadControls());
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [status, setStatus] = useState<AnalyticsStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const automaticLoadKey = useRef('');
  const requestSerial = useRef(0);

  const setControls = useCallback((next: AnalyticsControls) => {
    setControlsState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* storage may be unavailable; controls still live in state */
    }
  }, []);

  const refresh = useCallback(async () => {
    const serial = ++requestSerial.current;
    setStatus((current) => (current === 'ready' ? 'ready' : 'loading'));
    setError(null);
    try {
      const query = toQuery(controls);
      const [overview, breakdown, timeseries] = await Promise.all([
        analyticsApi.overview(query),
        analyticsApi.models(query),
        analyticsApi.timeseries(query),
      ]);
      if (serial !== requestSerial.current) return;
      setSummary({
        ...overview,
        by_model: breakdown.by_model,
        by_provider: breakdown.by_provider,
        series: timeseries.series,
      });
      setStatus('ready');
    } catch (cause) {
      if (serial !== requestSerial.current) return;
      setError(cause instanceof Error ? cause.message : 'Failed to load usage');
      setStatus('error');
    }
  }, [controls]);

  useEffect(() => {
    if (!active) {
      automaticLoadKey.current = '';
      return;
    }
    const key = JSON.stringify(toQuery(controls));
    if (automaticLoadKey.current === key) return;
    automaticLoadKey.current = key;
    void refresh();
  }, [active, controls, refresh]);

  const setBudget = useCallback(
    async (agentId: string, patch: BudgetPatch) => {
      await analyticsApi.setBudget(agentId, patch);
      await refresh();
    },
    [refresh],
  );
  const available = useMemo<SelectableAgent[]>(
    () => agents
      .map((agent) => ({
        agent_id: agent.id,
        display_name: agent.title || agent.name || agent.id,
      }))
      .sort((a, b) => a.display_name.localeCompare(b.display_name)),
    [agents],
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
