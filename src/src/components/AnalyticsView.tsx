import { useId, useState } from 'react';
import {
  Activity,
  Bot,
  CheckCircle2,
  CircleDollarSign,
  Database,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';
import type { AnalyticsState, AnalyticsControls } from '../hooks/useAnalytics';
import type {
  Bucket,
  BudgetStatus,
  ModelUsage,
  ProviderUsage,
  UsageSummary,
} from '../api/analytics';

const PRESETS: Array<{ label: string; days: number }> = [
  { label: '24h', days: 1 },
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
];
const BUCKETS: Bucket[] = ['hour', 'day', 'week', 'month'];
const STATUS_COLOR: Record<string, string> = {
  ok: '#48a879',
  warning: '#d09a37',
  exceeded: '#d85d4a',
  unset: '#768078',
};

function fmtTokens(value: number): string {
  if (value >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(2)}B`;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}

function fmtUsd(value: number): string {
  if (value >= 100) return `$${value.toFixed(0)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

function pct(value: number, total: number): number {
  return total > 0 ? Math.max(0, Math.min(100, value / total * 100)) : 0;
}

export function AnalyticsView({ state, onClose }: { state: AnalyticsState; onClose: () => void }) {
  const { controls, setControls, available, summary, status, error, generatedAt } = state;
  const [pickerOpen, setPickerOpen] = useState(false);
  const [metric, setMetric] = useState<'tokens' | 'cost'>('tokens');
  const selectedCount = controls.agents.length;
  const agentLabel = selectedCount
    ? `${selectedCount} selected`
    : `All usage`;

  function patch(next: Partial<AnalyticsControls>) {
    setControls({ ...controls, ...next });
  }

  function toggleAgent(id: string) {
    const next = new Set(controls.agents);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    patch({ agents: [...next] });
  }

  return (
    <main className="analytics-page">
      <header className="analytics-header">
        <div>
          <div className="analytics-eyebrow"><Sparkles size={13} /> Usage intelligence</div>
          <h1>Understand every model request</h1>
          <p>Durable workspace spend from 9router, with live attribution to your current agents.</p>
        </div>
        <button className="analytics-icon-button" onClick={onClose} title="Close analytics" aria-label="Close analytics">
          <X size={17} />
        </button>
      </header>

      <section className="analytics-toolbar" aria-label="Analytics controls">
        <div className="analytics-agent-picker">
          <button
            className="analytics-control analytics-agent-trigger"
            onClick={() => setPickerOpen((value) => !value)}
            aria-expanded={pickerOpen}
          >
            <Bot size={14} />
            <span>{agentLabel}</span>
            <span className="analytics-chevron">⌄</span>
          </button>
          {pickerOpen && (
            <div className="analytics-picker-popover">
              <div className="analytics-picker-heading">
                <div>
                  <strong>Agent attribution</strong>
                  <span>Filters use current profiles only</span>
                </div>
                <button onClick={() => patch({ agents: [] })}>Clear</button>
              </div>
              {available.length === 0 && <div className="analytics-empty-small">No current agents</div>}
              {available.map((agent) => (
                <label key={agent.agent_id} className="analytics-picker-option">
                  <input
                    type="checkbox"
                    checked={controls.agents.includes(agent.agent_id)}
                    onChange={() => toggleAgent(agent.agent_id)}
                  />
                  <span className="analytics-agent-dot">{agent.display_name.slice(0, 1).toUpperCase()}</span>
                  <span>{agent.display_name}</span>
                </label>
              ))}
            </div>
          )}
        </div>

        <div className="analytics-presets">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              className={!controls.from && !controls.to && controls.days === preset.days ? 'active' : ''}
              onClick={() => patch({ days: preset.days, from: undefined, to: undefined })}
            >
              {preset.label}
            </button>
          ))}
        </div>

        <div className="analytics-dates">
          <input
            type="date"
            value={controls.from ?? ''}
            aria-label="From date"
            onChange={(event) => patch({ from: event.target.value || undefined })}
          />
          <span>to</span>
          <input
            type="date"
            value={controls.to ?? ''}
            aria-label="To date"
            onChange={(event) => patch({ to: event.target.value || undefined })}
          />
        </div>

        <select
          className="analytics-control analytics-bucket"
          value={controls.bucket}
          aria-label="Chart interval"
          onChange={(event) => patch({ bucket: event.target.value as Bucket })}
        >
          {BUCKETS.map((bucket) => <option key={bucket} value={bucket}>By {bucket}</option>)}
        </select>
        <button
          className={`analytics-icon-button ${status === 'loading' ? 'spinning' : ''}`}
          onClick={() => void state.refresh()}
          title="Refresh analytics"
          aria-label="Refresh analytics"
        >
          <RefreshCw size={15} />
        </button>
        {generatedAt && (
          <span className="analytics-as-of">Updated {new Date(generatedAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        )}
      </section>

      {status === 'error' && (
        <section className="analytics-error">
          <div><strong>Usage could not be loaded.</strong><span>{error}</span></div>
          <button onClick={() => void state.refresh()}>Retry</button>
        </section>
      )}
      {!summary && status === 'loading' && <AnalyticsSkeleton />}
      {summary && (
        <Dashboard
          summary={summary}
          metric={metric}
          setMetric={setMetric}
          setBudget={state.setBudget}
        />
      )}
    </main>
  );
}

function Dashboard({
  summary,
  metric,
  setMetric,
  setBudget,
}: {
  summary: UsageSummary;
  metric: 'tokens' | 'cost';
  setMetric: (metric: 'tokens' | 'cost') => void;
  setBudget: AnalyticsState['setBudget'];
}) {
  const totals = summary.totals;
  const source = summary.source ?? {
    kind: 'live_profiles' as const,
    durable: false,
    label: 'Current agent profiles',
    message: 'Restart the backend to enable durable 9router usage totals.',
  };
  const requestStatus = summary.request_status ?? {
    total: totals.api_calls,
    successful: totals.api_calls,
    failed: 0,
    success_rate: totals.api_calls ? 100 : 0,
  };
  const attribution = summary.attribution ?? {
    live_totals: totals,
    attributed_tokens: totals.total_tokens,
    unattributed_tokens: 0,
    coverage_percent: totals.total_tokens ? 100 : 0,
    deleted_usage_included: false,
  };
  const averageCost = totals.api_calls ? totals.cost_usd / totals.api_calls : 0;
  const durable = source.durable;

  return (
    <>
      <section className={`analytics-source-banner ${durable ? 'durable' : 'live'}`}>
        <div className="analytics-source-icon">{durable ? <Database size={17} /> : <Activity size={17} />}</div>
        <div>
          <strong>{source.label}</strong>
          <span>{source.message}</span>
        </div>
        <div className="analytics-source-badge">
          {durable ? <ShieldCheck size={13} /> : <Activity size={13} />}
          {durable ? 'Deletion-safe totals' : 'Live attribution'}
        </div>
      </section>

      <section className="analytics-kpis">
        <MetricCard
          icon={<Zap size={17} />}
          tone="mint"
          label="Total tokens"
          value={fmtTokens(totals.total_tokens)}
          detail={`${fmtTokens(totals.input_tokens)} input · ${fmtTokens(totals.output_tokens)} output`}
        />
        <MetricCard
          icon={<Activity size={17} />}
          tone="blue"
          label="Requests"
          value={totals.api_calls.toLocaleString()}
          detail={`${requestStatus.success_rate.toFixed(1)}% successful · ${requestStatus.failed} failed`}
        />
        <MetricCard
          icon={<CircleDollarSign size={17} />}
          tone="gold"
          label="Estimated cost"
          value={fmtUsd(totals.cost_usd)}
          detail={`${fmtUsd(averageCost)} average per request`}
        />
        <MetricCard
          icon={<Bot size={17} />}
          tone="violet"
          label="Live attribution"
          value={`${attribution.coverage_percent.toFixed(1)}%`}
          detail={`${fmtTokens(attribution.attributed_tokens)} across ${summary.agents.length} current agents`}
        />
      </section>

      <section className="analytics-primary-grid">
        <article className="analytics-panel analytics-trend-panel">
          <PanelHeader
            eyebrow="Usage trend"
            title={`Workspace activity by ${summary.bucket}`}
            trailing={(
              <div className="analytics-metric-toggle">
                <button className={metric === 'tokens' ? 'active' : ''} onClick={() => setMetric('tokens')}>Tokens</button>
                <button className={metric === 'cost' ? 'active' : ''} onClick={() => setMetric('cost')}>Cost</button>
              </div>
            )}
          />
          <TimeChart summary={summary} metric={metric} />
        </article>

        <article className="analytics-panel analytics-composition-panel">
          <PanelHeader eyebrow="Token mix" title="Input vs. output" />
          <div className="analytics-donut-wrap">
            <TokenDonut input={totals.input_tokens} output={totals.output_tokens} />
            <div className="analytics-donut-legend">
              <LegendRow color="#5eb69b" label="Input" value={totals.input_tokens} total={totals.total_tokens} />
              <LegendRow color="#7d8fdb" label="Output" value={totals.output_tokens} total={totals.total_tokens} />
              <LegendRow color="#d39a4a" label="Cache read" value={totals.cache_read_tokens} total={totals.input_tokens || 1} />
            </div>
          </div>
          <div className="analytics-composition-foot">
            <span>Reasoning <strong>{fmtTokens(totals.reasoning_tokens)}</strong></span>
            <span>Cache write <strong>{fmtTokens(totals.cache_write_tokens)}</strong></span>
          </div>
        </article>
      </section>

      <section className="analytics-breakdown-grid">
        <article className="analytics-panel">
          <PanelHeader eyebrow="Models" title="Usage by model" />
          <BreakdownRows rows={summary.by_model} empty="No model usage in this range." />
        </article>
        <article className="analytics-panel">
          <PanelHeader eyebrow="Providers" title="Traffic distribution" />
          <ProviderRows rows={summary.by_provider ?? []} />
          {summary.quota.available && summary.quota.quotas.length > 0 && (
            <div className="analytics-quota">
              <div><CheckCircle2 size={14} /><span>{summary.quota.provider} quota</span></div>
              <strong>{summary.quota.quotas[0].remaining_percent}% remaining</strong>
            </div>
          )}
        </article>
      </section>

      <article className="analytics-panel analytics-attribution-panel">
        <PanelHeader
          eyebrow="Current profiles"
          title="Agent attribution & budgets"
          trailing={<span className="analytics-live-pill"><span /> Live only</span>}
        />
        <div className="analytics-retention-note">
          <Database size={16} />
          <div>
            <strong>{fmtTokens(attribution.unattributed_tokens)} historical tokens are not assigned to a current agent.</strong>
            <span>Deleting a chat or agent no longer changes workspace totals. 9router does not store an agent ID, so deleted attribution cannot be reconstructed.</span>
          </div>
        </div>
        <div className="analytics-agent-table" role="table" aria-label="Current agent usage and budgets">
          <div className="analytics-agent-table-head" role="row">
            <span>Agent</span><span>Tokens</span><span>Requests</span><span>Share</span><span>Monthly budget</span>
          </div>
          {summary.agents.length === 0 && <div className="analytics-empty">No current agent usage in this range.</div>}
          {summary.agents.map((agent) => (
            <AgentRow
              key={agent.agent_id}
              agentId={agent.agent_id}
              name={agent.display_name}
              totals={agent.totals}
              budget={agent.budget}
              attributedTotal={attribution.live_totals.total_tokens}
              setBudget={setBudget}
            />
          ))}
        </div>
      </article>
    </>
  );
}

function MetricCard({ icon, tone, label, value, detail }: {
  icon: React.ReactNode;
  tone: string;
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article className={`analytics-kpi ${tone}`}>
      <div className="analytics-kpi-top"><span>{icon}</span><small>{label}</small></div>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function PanelHeader({ eyebrow, title, trailing }: {
  eyebrow: string;
  title: string;
  trailing?: React.ReactNode;
}) {
  return (
    <div className="analytics-panel-header">
      <div><span>{eyebrow}</span><h2>{title}</h2></div>
      {trailing}
    </div>
  );
}

function TimeChart({ summary, metric }: { summary: UsageSummary; metric: 'tokens' | 'cost' }) {
  const id = useId().replace(/:/g, '');
  const series = summary.series;
  const values = series.map((row) => metric === 'tokens' ? row.total_tokens : row.cost_usd);
  if (!series.length) return <div className="analytics-empty">No usage in this range.</div>;
  const width = 900;
  const height = 245;
  const left = 12;
  const top = 18;
  const bottom = 34;
  const plotHeight = height - top - bottom;
  const max = Math.max(1, ...values);
  const xFor = (index: number) => left + (series.length === 1 ? (width - left * 2) / 2 : index * (width - left * 2) / (series.length - 1));
  const yFor = (value: number) => top + plotHeight - value / max * plotHeight;
  const points = values.map((value, index) => `${xFor(index)},${yFor(value)}`).join(' ');
  const area = `${left},${top + plotHeight} ${points} ${width - left},${top + plotHeight}`;
  const labelEvery = Math.max(1, Math.ceil(series.length / 8));

  return (
    <div className="analytics-chart">
      <div className="analytics-chart-max">{metric === 'tokens' ? fmtTokens(max) : fmtUsd(max)}</div>
      <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${metric} over time`}>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#55a98f" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#55a98f" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 0.33, 0.66, 1].map((ratio) => (
          <line key={ratio} x1={left} x2={width - left} y1={top + plotHeight * ratio} y2={top + plotHeight * ratio} className="analytics-grid-line" />
        ))}
        <polygon points={area} fill={`url(#${id})`} />
        <polyline points={points} className="analytics-chart-line" />
        {series.map((row, index) => (
          <g key={row.bucket}>
            <circle cx={xFor(index)} cy={yFor(values[index])} r="3" className="analytics-chart-point">
              <title>{row.bucket}: {metric === 'tokens' ? `${fmtTokens(values[index])} tokens` : fmtUsd(values[index])}</title>
            </circle>
            {index % labelEvery === 0 && (
              <text x={xFor(index)} y={height - 10} textAnchor="middle" className="analytics-chart-label">
                {row.bucket.replace(/^\d{4}-/, '')}
              </text>
            )}
          </g>
        ))}
      </svg>
    </div>
  );
}

function TokenDonut({ input, output }: { input: number; output: number }) {
  const total = input + output;
  const inputShare = pct(input, total);
  return (
    <div
      className="analytics-donut"
      style={{ background: `conic-gradient(#5eb69b 0 ${inputShare}%, #7d8fdb ${inputShare}% 100%)` }}
    >
      <div><strong>{fmtTokens(total)}</strong><span>tokens</span></div>
    </div>
  );
}

function LegendRow({ color, label, value, total }: {
  color: string;
  label: string;
  value: number;
  total: number;
}) {
  return (
    <div className="analytics-legend-row">
      <span className="analytics-legend-dot" style={{ background: color }} />
      <span>{label}</span>
      <strong>{fmtTokens(value)}</strong>
      <small>{pct(value, total).toFixed(1)}%</small>
    </div>
  );
}

function BreakdownRows({ rows, empty }: { rows: ModelUsage[]; empty: string }) {
  const max = Math.max(1, ...rows.map((row) => row.input_tokens + row.output_tokens));
  if (!rows.length) return <div className="analytics-empty">{empty}</div>;
  return (
    <div className="analytics-breakdown-list">
      {rows.slice(0, 8).map((row) => {
        const total = row.input_tokens + row.output_tokens;
        return (
          <div className="analytics-breakdown-row" key={`${row.provider}:${row.model}`}>
            <div className="analytics-breakdown-name">
              <span>{row.model}</span><small>{row.provider || 'unknown'}</small>
            </div>
            <div className="analytics-breakdown-bar"><span style={{ width: `${pct(total, max)}%` }} /></div>
            <strong>{fmtTokens(total)}</strong>
            <small>{fmtUsd(row.cost_usd)}</small>
          </div>
        );
      })}
    </div>
  );
}

function ProviderRows({ rows }: { rows: ProviderUsage[] }) {
  const totalTokens = rows.reduce((sum, row) => sum + row.input_tokens + row.output_tokens, 0);
  if (!rows.length) return <div className="analytics-empty">No provider usage in this range.</div>;
  return (
    <div className="analytics-provider-list">
      {rows.map((row, index) => {
        const tokens = row.input_tokens + row.output_tokens;
        return (
          <div className="analytics-provider-row" key={row.provider}>
            <div className={`analytics-provider-mark tone-${index % 4}`}>{row.provider.slice(0, 2).toUpperCase()}</div>
            <div><strong>{row.provider || 'unknown'}</strong><span>{row.sessions.toLocaleString()} requests</span></div>
            <div className="analytics-provider-share">
              <strong>{pct(tokens, totalTokens).toFixed(1)}%</strong><span>{fmtTokens(tokens)} tokens</span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function AgentRow({
  agentId,
  name,
  totals,
  budget,
  attributedTotal,
  setBudget,
}: {
  agentId: string;
  name: string;
  totals: UsageSummary['totals'];
  budget: BudgetStatus | null;
  attributedTotal: number;
  setBudget: AnalyticsState['setBudget'];
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(budget?.monthly_usd == null ? '' : String(budget.monthly_usd));
  const [busy, setBusy] = useState(false);
  const budgetPercent = budget?.monthly_usd ? Math.min(100, budget.percent_used) : 0;
  const budgetColor = STATUS_COLOR[budget?.status ?? 'unset'];

  async function save(value: number | null) {
    setBusy(true);
    try {
      await setBudget(agentId, { monthly_usd: value });
      setEditing(false);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="analytics-agent-row" role="row">
      <div className="analytics-agent-name">
        <span>{name.slice(0, 1).toUpperCase()}</span>
        <div><strong>{name}</strong><small>{agentId}</small></div>
      </div>
      <strong>{fmtTokens(totals.total_tokens)}</strong>
      <span>{totals.api_calls.toLocaleString()}</span>
      <div className="analytics-share-cell">
        <div><span style={{ width: `${pct(totals.total_tokens, attributedTotal)}%` }} /></div>
        <small>{pct(totals.total_tokens, attributedTotal).toFixed(1)}%</small>
      </div>
      <div className="analytics-budget-cell">
        {editing ? (
          <div className="analytics-budget-edit">
            <span>$</span>
            <input
              type="number"
              min="0"
              step="0.01"
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              aria-label={`Monthly budget for ${name}`}
            />
            <button disabled={busy} onClick={() => void save(draft.trim() ? Number(draft) : null)}>Save</button>
            <button disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
          </div>
        ) : (
          <>
            <div className="analytics-budget-summary">
              <span>{budget?.monthly_usd == null ? 'No budget' : `${fmtUsd(budget.spend_usd)} / ${fmtUsd(budget.monthly_usd)}`}</span>
              <button onClick={() => {
                setDraft(budget?.monthly_usd == null ? '' : String(budget.monthly_usd));
                setEditing(true);
              }}>Edit</button>
            </div>
            <div className="analytics-budget-track"><span style={{ width: `${budgetPercent}%`, background: budgetColor }} /></div>
          </>
        )}
      </div>
    </div>
  );
}

function AnalyticsSkeleton() {
  return (
    <div className="analytics-skeleton" aria-label="Loading analytics">
      <div /><div /><div /><div />
      <section /><section />
    </div>
  );
}
