import { request } from '../../api/client';

export type DashboardWindow = '24h' | '7d' | '30d';

export type TimelinePoint = { bucket: string; runs: number; input_tokens: number; output_tokens: number; cost_usd: number; errors: number };
export type EntityUsage = { id: string; name: string; runs: number; tokens: number; cost_usd: number; error_rate: number; p95_latency_ms: number };
export type NamedUsage = { name: string; calls: number; errors: number; cost_usd: number; response_chars: number };
export type ResourcePoint = { bucket: string; cpu_percent: number; memory_mb: number };

export type DashboardOverview = {
  generated_at: string;
  window: DashboardWindow;
  demo_data: boolean;
  summary: {
    runs: number; success_rate: number; active_agents: number; teams: number;
    input_tokens: number; output_tokens: number; cached_tokens: number;
    cost_usd: number; p95_latency_ms: number; errors: number; response_chars: number;
  };
  timeline: TimelinePoint[];
  agents: EntityUsage[];
  skills: NamedUsage[];
  tools: NamedUsage[];
  models: NamedUsage[];
  resources: ResourcePoint[];
};

export type TraceSummary = {
  trace_id: string; started_at: string; ended_at: string; root_operation: string; status: string;
  duration_ms: number; spans: number; agent_id: string; run_id: string; conversation_id: string;
  provider: string; model: string; input_tokens: number; output_tokens: number;
  response_chars: number; cost_usd: number; demo: boolean;
};

export type TraceSpan = {
  trace_id: string; span_id: string; parent_span_id?: string; started_at: string; ended_at: string;
  operation: string; service: string; status: string; duration_ms: number; node_id: string;
  parent_node_id?: string; node_kind: string; node_label: string; provider: string; model: string;
  input_tokens: number; output_tokens: number; prompt_chars: number; response_chars: number; cost_usd: number;
};

export type TraceList = { generated_at: string; window: DashboardWindow; traces: TraceSummary[] };
export type TraceDetail = { trace_id: string; spans: TraceSpan[] };

export type DashboardDependencies = {
  generated_at: string;
  window: DashboardWindow;
  demo_data: boolean;
  nodes: Array<{ id: string; label: string; kind: string; calls: number; errors: number }>;
  edges: Array<{ source: string; target: string; calls: number; error_rate: number; p95_latency_ms: number }>;
};

export const dashboardApi = {
  overview: (window: DashboardWindow) => request<DashboardOverview>(`/api/v1/dashboard/overview?window=${window}`),
  dependencies: (window: DashboardWindow) => request<DashboardDependencies>(`/api/v1/dashboard/dependencies?window=${window}`),
  traces: (window: DashboardWindow) => request<TraceList>(`/api/v1/observability/traces?window=${window}&limit=50`),
  trace: (traceId: string) => request<TraceDetail>(`/api/v1/observability/traces/${encodeURIComponent(traceId)}`),
};
