import { useEffect, useMemo, useState } from 'react';
import { Activity, Bot, Clock3, Coins, Network, RefreshCw, Sparkles, TriangleAlert, X } from 'lucide-react';
import { dashboardApi, type DashboardDependencies, type DashboardOverview, type DashboardWindow } from './api';

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });
const money = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

function Sparkline({ values, errors }: { values: number[]; errors?: number[] }) {
  const max = Math.max(1, ...values);
  return <div className="dashboard-bars" aria-label="Aggregated run timeline">{values.map((value, index) => (
    <span key={index} className={errors?.[index] ? 'has-error' : ''} style={{ height: `${Math.max(5, value / max * 100)}%` }} title={`${value} runs`} />
  ))}</div>;
}

function RankedList({ title, rows }: { title: string; rows: Array<{ name: string; calls: number; errors: number }> }) {
  const max = Math.max(1, ...rows.map((row) => row.calls));
  return <section className="dashboard-card ranked-card"><h3>{title}</h3>{rows.map((row) => <div className="rank-row" key={row.name}>
    <div><strong>{row.name}</strong><small>{compact.format(row.calls)} calls{row.errors ? ` · ${row.errors} errors` : ''}</small></div>
    <span><i style={{ width: `${row.calls / max * 100}%` }} /></span>
  </div>)}</section>;
}

function DependencyGraph({ graph }: { graph: DashboardDependencies }) {
  const columns = ['run', 'agent', 'skill', 'tool'];
  const byKind = new Map(columns.map((kind) => [kind, graph.nodes.filter((node) => node.kind === kind)]));
  const positions = new Map<string, { x: number; y: number }>();
  columns.forEach((kind, column) => {
    const nodes = byKind.get(kind) ?? [];
    nodes.forEach((node, index) => positions.set(node.id, { x: 95 + column * 235, y: 52 + index * Math.max(54, 230 / Math.max(1, nodes.length)) }));
  });
  return <section className="dashboard-card dependency-card">
    <div className="card-heading"><div><h3>Agent dependency map</h3><p>Team → agents → skills → tools, aggregated from parent/child spans</p></div><Network size={20} /></div>
    <div className="dependency-scroll"><svg viewBox="0 0 920 330" role="img" aria-label="Agent dependency graph">
      <defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" /></marker></defs>
      {graph.edges.map((edge) => { const source = positions.get(edge.source); const target = positions.get(edge.target); if (!source || !target) return null; return <g key={`${edge.source}-${edge.target}`}><path className={edge.error_rate ? 'graph-edge error' : 'graph-edge'} d={`M ${source.x + 55} ${source.y} C ${source.x + 120} ${source.y}, ${target.x - 120} ${target.y}, ${target.x - 55} ${target.y}`} markerEnd="url(#arrow)" /><title>{edge.calls} calls · p95 {Math.round(edge.p95_latency_ms)} ms</title></g>; })}
      {graph.nodes.map((node) => { const point = positions.get(node.id); if (!point) return null; return <g key={node.id} transform={`translate(${point.x},${point.y})`}><rect className={`graph-node ${node.kind}`} x="-55" y="-19" width="110" height="38" rx="9" /><text textAnchor="middle" y="-2">{node.label.length > 16 ? `${node.label.slice(0, 15)}…` : node.label}</text><text className="graph-meta" textAnchor="middle" y="12">{node.calls} calls</text><title>{node.label} · {node.calls} calls · {node.errors} errors</title></g>; })}
    </svg></div>
  </section>;
}

export function DashboardView({ onClose }: { onClose?: () => void }) {
  const [window, setWindow] = useState<DashboardWindow>('24h');
  const [overview, setOverview] = useState<DashboardOverview>();
  const [dependencies, setDependencies] = useState<DashboardDependencies>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = async () => {
    setLoading(true); setError('');
    try { const [nextOverview, nextDependencies] = await Promise.all([dashboardApi.overview(window), dashboardApi.dependencies(window)]); setOverview(nextOverview); setDependencies(nextDependencies); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Dashboard request failed'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [window]); // eslint-disable-line react-hooks/exhaustive-deps
  const resourceCPU = useMemo(() => overview?.resources.map((point) => point.cpu_percent) ?? [], [overview]);
  if (!overview && loading) return <div className="dashboard-view dashboard-state"><RefreshCw className="spin" /> Loading aggregate telemetry…</div>;
  if (!overview) return <div className="dashboard-view dashboard-state error"><TriangleAlert /> <div><strong>Dashboard unavailable</strong><p>{error}</p><button onClick={load}>Try again</button></div></div>;
  const summary = overview.summary;
  return <div className="dashboard-view">
    <header className="dashboard-header"><div><span className="eyebrow">OBSERVABILITY</span><h1>Agent operations</h1><p>Runs, usage, dependencies, and runtime health from ClickHouse aggregates.</p></div><div className="dashboard-actions">
      {overview.demo_data && <span className="demo-badge"><Sparkles size={14} /> Demo data</span>}
      <div className="window-picker">{(['24h','7d','30d'] as DashboardWindow[]).map((item) => <button className={window === item ? 'active' : ''} key={item} onClick={() => setWindow(item)}>{item}</button>)}</div>
      <button className="icon-button" title="Refresh" onClick={load}><RefreshCw className={loading ? 'spin' : ''} size={17} /></button>
      {onClose && <button className="icon-button" title="Close" onClick={onClose}><X size={18} /></button>}
    </div></header>
    {error && <div className="dashboard-warning"><TriangleAlert size={16} /> Showing the last aggregate response. Refresh failed: {error}</div>}
    <div className="summary-grid">
      <article><Activity /><span>Agent team runs</span><strong>{compact.format(summary.runs)}</strong><small>{summary.success_rate}% successful</small></article>
      <article><Bot /><span>Active agents</span><strong>{summary.active_agents}</strong><small>{summary.teams} team{summary.teams === 1 ? '' : 's'}</small></article>
      <article><Sparkles /><span>Total tokens</span><strong>{compact.format(summary.input_tokens + summary.output_tokens)}</strong><small>{compact.format(summary.cached_tokens)} cached</small></article>
      <article><Coins /><span>Estimated cost</span><strong>{money.format(summary.cost_usd)}</strong><small>selected window</small></article>
      <article><Clock3 /><span>p95 latency</span><strong>{(summary.p95_latency_ms / 1000).toFixed(1)}s</strong><small>end-to-end runs</small></article>
      <article className={summary.errors ? 'error-metric' : ''}><TriangleAlert /><span>Errors</span><strong>{summary.errors}</strong><small>across spans</small></article>
    </div>
    <div className="dashboard-grid wide-left">
      <section className="dashboard-card timeline-card"><div className="card-heading"><div><h3>Runs and token volume</h3><p>Server-side time buckets; raw spans stay in ClickHouse</p></div><strong>{compact.format(summary.input_tokens + summary.output_tokens)} tokens</strong></div><Sparkline values={overview.timeline.map((point) => point.runs)} errors={overview.timeline.map((point) => point.errors)} /><div className="timeline-legend"><span><i /> Successful volume</span><span><i className="error" /> Bucket with errors</span></div></section>
      <section className="dashboard-card resource-card"><div className="card-heading"><div><h3>Runtime resources</h3><p>Average CPU · memory</p></div><strong>{resourceCPU.at(-1)?.toFixed(0) ?? 0}% CPU</strong></div><Sparkline values={resourceCPU} /><div className="resource-stat"><span>Latest memory</span><strong>{overview.resources.at(-1)?.memory_mb.toFixed(0) ?? 0} MB</strong></div></section>
    </div>
    {dependencies && <DependencyGraph graph={dependencies} />}
    <div className="dashboard-grid thirds"><RankedList title="Top skills" rows={overview.skills} /><RankedList title="Tool activity" rows={overview.tools} /><RankedList title="Model usage" rows={overview.models} /></div>
    <section className="dashboard-card agents-card"><div className="card-heading"><div><h3>Agent usage</h3><p>Aggregated token, cost, error, and latency totals</p></div></div><div className="agent-usage-table"><div className="usage-row usage-head"><span>Agent</span><span>Runs</span><span>Tokens</span><span>Cost</span><span>Error rate</span><span>p95</span></div>{overview.agents.map((agent) => <div className="usage-row" key={agent.id}><span><i /> <strong>{agent.name}</strong></span><span>{agent.runs}</span><span>{compact.format(agent.tokens)}</span><span>{money.format(agent.cost_usd)}</span><span className={agent.error_rate ? 'danger' : ''}>{agent.error_rate}%</span><span>{Math.round(agent.p95_latency_ms)} ms</span></div>)}</div></section>
    <footer>Aggregate generated {new Date(overview.generated_at).toLocaleString()} · retention and tenant isolation are enforced by the gateway.</footer>
  </div>;
}
