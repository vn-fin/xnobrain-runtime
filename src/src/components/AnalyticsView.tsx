import { useState, type CSSProperties } from 'react';
import { BarChart3, RefreshCw, X } from 'lucide-react';
import type { AnalyticsState, AnalyticsControls } from '../hooks/useAnalytics';
import type { Bucket, BudgetStatus, UsageSummary } from '../api/analytics';

const ACCENT = '#2f6f62';
const STATUS_COLOR: Record<string, string> = {
  ok: '#3f9d6d', warning: '#b8860b', exceeded: '#b4462f', unset: 'rgba(128,128,128,0.7)',
};
const PRESETS: Array<{ label: string; days: number }> = [
  { label: '24h', days: 1 }, { label: '7d', days: 7 },
  { label: '30d', days: 30 }, { label: '90d', days: 90 },
];
const BUCKETS: Bucket[] = ['hour', 'day', 'week', 'month'];

const S = {
  wrap: { display: 'flex', flexDirection: 'column', height: '100%', overflow: 'auto', padding: '16px 20px', gap: 16 } as CSSProperties,
  header: { display: 'flex', alignItems: 'center', gap: 10, justifyContent: 'space-between' } as CSSProperties,
  bar: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 10, padding: '10px 12px', border: '1px solid rgba(128,128,128,0.28)', borderRadius: 10, background: 'rgba(128,128,128,0.06)' } as CSSProperties,
  panel: { border: '1px solid rgba(128,128,128,0.24)', borderRadius: 10, background: 'rgba(128,128,128,0.05)', padding: 14 } as CSSProperties,
  tiles: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 } as CSSProperties,
  tile: { border: '1px solid rgba(128,128,128,0.24)', borderRadius: 10, padding: '12px 14px', background: 'rgba(128,128,128,0.05)' } as CSSProperties,
  tileLabel: { fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.06em', opacity: 0.6 } as CSSProperties,
  tileValue: { fontSize: 22, fontWeight: 600, marginTop: 4 } as CSSProperties,
  btn: { padding: '5px 10px', borderRadius: 8, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', cursor: 'pointer', fontSize: 12 } as CSSProperties,
  btnActive: { background: ACCENT, borderColor: ACCENT, color: '#fff' } as CSSProperties,
  input: { padding: '4px 6px', borderRadius: 6, border: '1px solid rgba(128,128,128,0.3)', background: 'transparent', color: 'inherit', fontSize: 12 } as CSSProperties,
  muted: { opacity: 0.65, fontSize: 12 } as CSSProperties,
  tag: { fontSize: 10, padding: '1px 6px', borderRadius: 6, border: '1px solid rgba(128,128,128,0.35)', opacity: 0.75, marginLeft: 6 } as CSSProperties,
};

function fmtTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(Math.round(value));
}
function fmtUsd(value: number): string {
  if (value >= 100) return `$${value.toFixed(0)}`;
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

export function AnalyticsView({ state, onClose }: { state: AnalyticsState; onClose: () => void }) {
  const { controls, setControls, available, summary, status, error, generatedAt } = state;
  const [pickerOpen, setPickerOpen] = useState(false);
  const [metric, setMetric] = useState<'tokens' | 'cost'>('tokens');

  const selectedCount = controls.agents.length;
  const agentLabel = selectedCount
    ? `${selectedCount} of ${available.length || selectedCount}`
    : `All${available.length ? ` ${available.length}` : ''} agents`;

  function patch(next: Partial<AnalyticsControls>) {
    setControls({ ...controls, ...next });
  }
  function toggleAgent(id: string) {
    const set = new Set(controls.agents);
    if (set.has(id)) set.delete(id); else set.add(id);
    patch({ agents: [...set] });
  }

  return (
    <div style={S.wrap}>
      <div style={S.header}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <BarChart3 size={18} />
          <h2 style={{ margin: 0, fontSize: 16 }}>Usage analytics</h2>
          <span style={S.tag}>advisory · read-only</span>
        </div>
        <button style={S.btn} onClick={onClose} title="Close" aria-label="Close">
          <X size={14} />
        </button>
      </div>

      {/* Grafana-style control bar */}
      <div style={S.bar}>
        <div style={{ position: 'relative' }}>
          <button style={S.btn} onClick={() => setPickerOpen((v) => !v)} aria-expanded={pickerOpen}>
            Agents: <strong>{agentLabel}</strong> ▾
          </button>
          {pickerOpen && (
            <div style={{ position: 'absolute', zIndex: 20, marginTop: 4, minWidth: 220, maxHeight: 280, overflow: 'auto', padding: 8, border: '1px solid rgba(128,128,128,0.35)', borderRadius: 10, background: 'var(--panel, #1c1c1c)', boxShadow: '0 10px 30px rgba(0,0,0,0.35)' }}>
              <div style={{ display: 'flex', gap: 6, marginBottom: 6 }}>
                <button style={S.btn} onClick={() => patch({ agents: [] })}>All</button>
                <button style={S.btn} onClick={() => patch({ agents: available.map((a) => a.agent_id) })}>Select all</button>
              </div>
              {available.length === 0 && <div style={S.muted}>No agents</div>}
              {available.map((agent) => {
                const checked = selectedCount === 0 || controls.agents.includes(agent.agent_id);
                return (
                  <label key={agent.agent_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 2px', cursor: 'pointer' }}>
                    <input
                      type="checkbox"
                      checked={selectedCount === 0 ? false : checked}
                      onChange={() => toggleAgent(agent.agent_id)}
                    />
                    <span style={{ fontSize: 13 }}>{agent.display_name}</span>
                  </label>
                );
              })}
            </div>
          )}
        </div>

        <span style={{ opacity: 0.4 }}>|</span>
        {PRESETS.map((preset) => (
          <button
            key={preset.label}
            style={{ ...S.btn, ...(!controls.from && !controls.to && controls.days === preset.days ? S.btnActive : {}) }}
            onClick={() => patch({ days: preset.days, from: undefined, to: undefined })}
          >
            {preset.label}
          </button>
        ))}
        <input
          type="date" style={S.input} value={controls.from ?? ''} aria-label="From date"
          onChange={(e) => patch({ from: e.target.value || undefined })}
        />
        <span style={S.muted}>→</span>
        <input
          type="date" style={S.input} value={controls.to ?? ''} aria-label="To date"
          onChange={(e) => patch({ to: e.target.value || undefined })}
        />

        <span style={{ opacity: 0.4 }}>|</span>
        <select
          style={S.input} value={controls.bucket} aria-label="Bucket"
          onChange={(e) => patch({ bucket: e.target.value as Bucket })}
        >
          {BUCKETS.map((b) => <option key={b} value={b}>{b}</option>)}
        </select>

        <button style={S.btn} onClick={() => void state.refresh()} title="Refresh">
          <RefreshCw size={13} />
        </button>
        {generatedAt && <span style={S.muted}>as of {new Date(generatedAt).toLocaleTimeString()}</span>}
      </div>

      {status === 'error' && (
        <div style={{ ...S.panel, borderColor: 'rgba(180,70,47,0.5)' }}>
          <div style={{ marginBottom: 8 }}>Could not load usage: {error}</div>
          <button style={S.btn} onClick={() => void state.refresh()}>Retry</button>
        </div>
      )}

      {!summary && status === 'loading' && <div style={S.muted}>Loading usage…</div>}

      {summary && <Dashboard summary={summary} metric={metric} setMetric={setMetric} state={state} />}
    </div>
  );
}

function Dashboard({
  summary, metric, setMetric, state,
}: {
  summary: UsageSummary;
  metric: 'tokens' | 'cost';
  setMetric: (m: 'tokens' | 'cost') => void;
  state: AnalyticsState;
}) {
  const t = summary.totals;
  const estimated = t.cost_basis === 'estimated';
  return (
    <>
      <div style={S.tiles}>
        <Tile label="Total tokens" value={fmtTokens(t.total_tokens)} sub={`${fmtTokens(t.input_tokens)} in · ${fmtTokens(t.output_tokens)} out`} />
        <Tile label="Cost" value={fmtUsd(t.cost_usd)} sub={estimated ? 'estimated' : 'actual'} />
        <Tile label="Sessions" value={String(t.sessions)} sub={`${t.api_calls} API calls`} />
        <Tile label="Agents" value={String(summary.agents.length)} sub={`of ${summary.agents_available} total`} />
        {summary.quota.available && summary.quota.quotas[0] && (
          <Tile label={`Quota (${summary.quota.provider})`} value={`${summary.quota.quotas[0].remaining_percent}%`} sub={summary.quota.quotas[0].name} />
        )}
      </div>

      <div style={S.panel}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
          <strong style={{ fontSize: 13 }}>Over time ({summary.bucket})</strong>
          <div style={{ display: 'flex', gap: 6 }}>
            <button style={{ ...S.btn, ...(metric === 'tokens' ? S.btnActive : {}) }} onClick={() => setMetric('tokens')}>Tokens</button>
            <button style={{ ...S.btn, ...(metric === 'cost' ? S.btnActive : {}) }} onClick={() => setMetric('cost')}>Cost</button>
          </div>
        </div>
        <TimeChart summary={summary} metric={metric} />
      </div>

      <div style={S.panel}>
        <strong style={{ fontSize: 13 }}>By model</strong>
        <ModelBars summary={summary} />
      </div>

      <div style={S.panel}>
        <strong style={{ fontSize: 13 }}>Per-agent budgets</strong>
        <div style={{ ...S.muted, marginBottom: 8 }}>Soft warnings only — spend is never blocked locally.</div>
        {summary.agents.map((agent) => (
          <BudgetRow key={agent.agent_id} agentId={agent.agent_id} name={agent.display_name} budget={agent.budget} setBudget={state.setBudget} />
        ))}
      </div>
    </>
  );
}

function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div style={S.tile}>
      <div style={S.tileLabel}>{label}</div>
      <div style={S.tileValue}>{value}</div>
      {sub && <div style={S.muted}>{sub}</div>}
    </div>
  );
}

function TimeChart({ summary, metric }: { summary: UsageSummary; metric: 'tokens' | 'cost' }) {
  const series = summary.series;
  const values = series.map((row) => (metric === 'tokens' ? row.total_tokens : row.cost_usd));
  const max = Math.max(1, ...values);
  const width = 800, height = 180, pad = 24;
  const barW = series.length ? (width - pad * 2) / series.length : 0;
  const labelEvery = Math.max(1, Math.ceil(series.length / 12));
  if (!series.length) return <div style={S.muted}>No usage in this range.</div>;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label={`Usage ${metric} over time`}>
      <line x1={pad} y1={height - pad} x2={width - pad} y2={height - pad} stroke="rgba(128,128,128,0.4)" strokeWidth={1} />
      {series.map((row, i) => {
        const value = values[i];
        const h = Math.round((value / max) * (height - pad * 2));
        const x = pad + i * barW;
        const y = height - pad - h;
        return (
          <g key={row.bucket}>
            <rect x={x + 1} y={y} width={Math.max(1, barW - 2)} height={h} fill={ACCENT} rx={1}>
              <title>{`${row.bucket}: ${metric === 'tokens' ? fmtTokens(value) + ' tokens' : fmtUsd(value)}`}</title>
            </rect>
            {i % labelEvery === 0 && (
              <text x={x + barW / 2} y={height - pad + 12} textAnchor="middle" fontSize={8} fill="currentColor" opacity={0.55}>
                {row.bucket.replace(/^\d{4}-/, '')}
              </text>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function ModelBars({ summary }: { summary: UsageSummary }) {
  const rows = summary.by_model;
  const max = Math.max(1, ...rows.map((r) => r.input_tokens + r.output_tokens));
  if (!rows.length) return <div style={S.muted}>No model usage in this range.</div>;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 8 }}>
      {rows.map((row) => {
        const total = row.input_tokens + row.output_tokens;
        return (
          <div key={`${row.model}:${row.provider}`} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <div style={{ width: 160, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={`${row.model} (${row.provider || 'n/a'})`}>
              {row.model}
            </div>
            <div style={{ flex: 1, height: 14, background: 'rgba(128,128,128,0.15)', borderRadius: 4, overflow: 'hidden' }}>
              <div style={{ width: `${Math.round((total / max) * 100)}%`, height: '100%', background: ACCENT }} />
            </div>
            <div style={{ width: 96, textAlign: 'right', fontSize: 12 }}>{fmtTokens(total)} · {fmtUsd(row.cost_usd)}</div>
          </div>
        );
      })}
    </div>
  );
}

function BudgetRow({
  agentId, name, budget, setBudget,
}: {
  agentId: string;
  name: string;
  budget: BudgetStatus | null;
  setBudget: (agentId: string, patch: { monthly_usd: number | null }) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(budget?.monthly_usd != null ? String(budget.monthly_usd) : '');
  const [busy, setBusy] = useState(false);
  const status = budget?.status ?? 'unset';
  const color = STATUS_COLOR[status] ?? STATUS_COLOR.unset;
  const percent = budget && budget.monthly_usd ? Math.min(100, budget.percent_used) : 0;

  async function save(value: number | null) {
    setBusy(true);
    try { await setBudget(agentId, { monthly_usd: value }); setEditing(false); }
    finally { setBusy(false); }
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '6px 0', borderTop: '1px solid rgba(128,128,128,0.14)' }}>
      <div style={{ width: 160, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={name}>{name}</div>
      <div style={{ flex: 1, height: 14, background: 'rgba(128,128,128,0.15)', borderRadius: 4, overflow: 'hidden' }}>
        <div style={{ width: `${percent}%`, height: '100%', background: color }} />
      </div>
      <div style={{ width: 150, textAlign: 'right', fontSize: 12 }}>
        {budget && budget.monthly_usd != null
          ? `${fmtUsd(budget.spend_usd)} / ${fmtUsd(budget.monthly_usd)} (${budget.percent_used}%)`
          : <span style={S.muted}>no cap</span>}
      </div>
      {editing ? (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={S.muted}>$</span>
          <input style={{ ...S.input, width: 70 }} type="number" min={0} step="0.01" value={draft} onChange={(e) => setDraft(e.target.value)} aria-label="Monthly budget USD" />
          <button style={S.btn} disabled={busy} onClick={() => void save(draft.trim() === '' ? null : Number(draft))}>Save</button>
          <button style={S.btn} disabled={busy} onClick={() => void save(null)}>Clear</button>
          <button style={S.btn} disabled={busy} onClick={() => setEditing(false)}>Cancel</button>
        </div>
      ) : (
        <button style={S.btn} onClick={() => { setDraft(budget?.monthly_usd != null ? String(budget.monthly_usd) : ''); setEditing(true); }}>Edit</button>
      )}
    </div>
  );
}
