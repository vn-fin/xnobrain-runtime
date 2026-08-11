import { request } from './client';

export type Bucket = 'hour' | 'day' | 'week' | 'month';
export type CostBasis = 'estimated' | 'actual';
export type BudgetState = 'ok' | 'warning' | 'exceeded' | 'unset';

export type UsageTotals = {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  reasoning_tokens: number;
  estimated_cost_usd: number;
  actual_cost_usd: number;
  cost_usd: number;
  cost_basis: CostBasis;
  sessions: number;
  api_calls: number;
};

export type ModelUsage = {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  cost_basis: CostBasis;
  sessions: number;
};

export type ProviderUsage = Omit<ModelUsage, 'model'>;

export type BucketUsage = {
  bucket: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  cost_basis: CostBasis;
  sessions: number;
};

export type BudgetStatus = {
  monthly_usd: number | null;
  daily_usd: number | null;
  warn_threshold_percent: number;
  cost_basis: CostBasis;
  currency: string;
  period_start: string | null;
  spend_usd: number;
  daily_spend_usd: number;
  percent_used: number;
  status: BudgetState;
  advisory: boolean;
};

export type AgentUsage = {
  agent_id: string;
  display_name: string;
  totals: UsageTotals;
  budget: BudgetStatus | null;
};

export type QuotaWindow = {
  name: string;
  used: number;
  total: number;
  remaining_percent: number;
  reset_at: string;
  unlimited: boolean;
};

export type QuotaOverlay = {
  available: boolean;
  provider: string;
  model: string;
  plan: string;
  message: string;
  quotas: QuotaWindow[];
};

export type AnalyticsSource = {
  kind: 'nine_router' | 'live_profiles';
  durable: boolean;
  label: string;
  message: string;
};

export type RequestStatus = {
  total: number;
  successful: number;
  failed: number;
  success_rate: number;
};

export type UsageAttribution = {
  live_totals: UsageTotals;
  attributed_tokens: number;
  unattributed_tokens: number;
  coverage_percent: number;
  deleted_usage_included: boolean;
};

export type UsageSummary = {
  range_from: number;
  range_to: number;
  period_days: number;
  bucket: Bucket;
  agents_selected: string[];
  agents_available: number;
  generated_at: string;
  timezone: string;
  totals: UsageTotals;
  agents: AgentUsage[];
  by_model: ModelUsage[];
  by_provider: ProviderUsage[];
  series: BucketUsage[];
  quota: QuotaOverlay;
  source: AnalyticsSource;
  request_status: RequestStatus;
  attribution: UsageAttribution;
};

export type UsageOverview = Omit<UsageSummary, 'by_model' | 'by_provider' | 'series'>;

export type UsageBreakdown = {
  range_from: number;
  range_to: number;
  totals: UsageTotals;
  by_model: ModelUsage[];
  by_provider: ProviderUsage[];
  source: AnalyticsSource;
};

export type UsageTimeseries = {
  range_from: number;
  range_to: number;
  bucket: Bucket;
  series: BucketUsage[];
  source: AnalyticsSource;
};

export type SelectableAgent = { agent_id: string; display_name: string };

export type BudgetPatch = {
  monthly_usd?: number | null;
  daily_usd?: number | null;
  warn_threshold_percent?: number;
  cost_basis?: CostBasis;
  currency?: string;
};

export type AnalyticsQuery = {
  agents: string[];
  days?: number;
  from?: string;
  to?: string;
  bucket: Bucket;
};

function queryString(query: AnalyticsQuery): string {
  const params = new URLSearchParams();
  if (query.agents.length) params.set('agents', query.agents.join(','));
  if (query.from || query.to) {
    if (query.from) params.set('from', query.from);
    if (query.to) params.set('to', query.to);
  } else if (query.days) {
    params.set('days', String(query.days));
  }
  params.set('bucket', query.bucket);
  return params.toString();
}

const ROOT = '/xnobrain/api/runtime/v1/analytics';

export const analyticsApi = {
  usage: (query: AnalyticsQuery, signal?: AbortSignal): Promise<UsageSummary> =>
    request<UsageSummary>(`${ROOT}/usage?${queryString(query)}`, { signal }),
  overview: (query: AnalyticsQuery): Promise<UsageOverview> =>
    request<UsageOverview>(`${ROOT}/overview?${queryString(query)}`),
  models: (query: AnalyticsQuery): Promise<UsageBreakdown> =>
    request<UsageBreakdown>(`${ROOT}/models?${queryString(query)}`),
  timeseries: (query: AnalyticsQuery): Promise<UsageTimeseries> =>
    request<UsageTimeseries>(`${ROOT}/timeseries?${queryString(query)}`),
  getBudget: (agentId: string): Promise<BudgetStatus> =>
    request<BudgetStatus>(`${ROOT}/agents/${encodeURIComponent(agentId)}/budget`),
  setBudget: (agentId: string, patch: BudgetPatch): Promise<BudgetStatus> =>
    request<BudgetStatus>(`${ROOT}/agents/${encodeURIComponent(agentId)}/budget`, {
      method: 'PUT',
      body: JSON.stringify(patch),
    }),
};
