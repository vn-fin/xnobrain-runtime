import { useEffect, useMemo, useState } from 'react';
import { Activity, Bot, Braces, Clock3, Coins, Network, RefreshCw, Sparkles, TriangleAlert, X } from 'lucide-react';
import { dashboardApi, type DashboardDependencies, type DashboardOverview, type DashboardWindow, type TraceDetail, type TraceList } from './api';

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });
const money = new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 });

function Sparkline({ values, errors }: { values: number[]; errors?: number[] }) {
  const max = Math.max(1, ...values);
  return <div className="dashboard-bars" aria-label="Aggregated run timeline">{values.map((value, index) => (
    <span key={index} className={errors?.[index] ? 'has-error' : ''} style={{ height: `${Math.max(5, value / max * 100)}%` }} title={`${value} runs`} />
  ))}</div>;
}

function RankedList({ title, rows }: { title: string; rows: Array<{ name: string; calls: number; errors: number; response_chars: number }> }) {
  const max = Math.max(1, ...rows.map((row) => row.calls));
  return <section className="dashboard-card ranked-card"><h3>{title}</h3>{rows.map((row) => <div className="rank-row" key={row.name}>
    <div><strong>{row.name}</strong><small>{compact.format(row.calls)} calls · {compact.format(row.response_chars)} response chars{row.errors ? ` · ${row.errors} errors` : ''}</small></div>
    <span><i style={{ width: `${row.calls / max * 100}%` }} /></span>
  </div>)}</section>;
}

function TraceExplorer({ list, detail, selected, onSelect }: { list?: TraceList; detail?: TraceDetail; selected: string; onSelect: (id: string) => void }) {
  const spans = detail?.spans ?? [];
  const selectedSummary = list?.traces.find((item) => item.trace_id === selected);
  const started = spans.length ? Math.min(...spans.map((span) => Date.parse(span.started_at))) : 0;
  const ended = spans.length ? Math.max(...spans.map((span) => Date.parse(span.ended_at))) : 1;
  const range = Math.max(1, ended - started);
  return <section className="dashboard-card trace-explorer">
    <div className="card-heading"><div><h3>Latest traces</h3><p>Chat, agent, model, skill, and tool execution with response character sizes</p></div>{selectedSummary?.agent_ref && selectedSummary.conversation_ref ? <a href={`/agents/${encodeURIComponent(selectedSummary.agent_ref)}/conversations/${encodeURIComponent(selectedSummary.conversation_ref)}`}>Open chat</a> : <Braces size={20} />}</div>
    <div className="trace-layout">
      <div className="trace-list">{list?.traces.map((trace) => <button key={trace.trace_id} className={selected === trace.trace_id ? 'active' : ''} onClick={() => onSelect(trace.trace_id)}>
        <span><i className={trace.status} /> <strong>{trace.root_operation || 'Agent run'}</strong><small>{new Date(trace.started_at).toLocaleTimeString()}</small></span>
        <span><b>{trace.duration_ms >= 1000 ? `${(trace.duration_ms / 1000).toFixed(1)}s` : `${Math.round(trace.duration_ms)}ms`}</b><small>{trace.spans} spans · {compact.format(trace.response_chars)} chars</small></span>
      </button>)}</div>
      <div className="trace-waterfall">{selected && !detail && <div className="trace-empty"><RefreshCw className="spin" size={16} /> Loading trace…</div>}{!selected && <div className="trace-empty">Select a trace to inspect its execution.</div>}{spans.map((span) => {
        const left = (Date.parse(span.started_at) - started) / range * 100;
        const width = Math.max(1, span.duration_ms / range * 100);
        return <div className="waterfall-row" key={span.span_id} title={`${span.operation} · ${Math.round(span.duration_ms)} ms · ${span.response_chars} response chars`}>
          <span className="waterfall-name"><i className={span.status} /> <strong>{span.node_label || span.operation}</strong><small>{span.node_kind || span.service}</small></span>
          <span className="waterfall-track"><i className={span.status} style={{ left: `${left}%`, width: `${width}%` }} /></span>
          <span className="waterfall-size">{compact.format(span.response_chars)} chars<small>{compact.format(span.input_tokens + span.output_tokens)} tok</small></span>
        </div>;
      })}</div>
    </div>
  </section>;
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
  const [traces, setTraces] = useState<TraceList>();
  const [trace, setTrace] = useState<TraceDetail>();
  const [selectedTrace, setSelectedTrace] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const load = async () => {
    setLoading(true); setError('');
    try { const [nextOverview, nextDependencies, nextTraces] = await Promise.all([dashboardApi.overview(window), dashboardApi.dependencies(window), dashboardApi.traces(window)]); setOverview(nextOverview); setDependencies(nextDependencies); setTraces(nextTraces); setSelectedTrace((current) => current && nextTraces.traces.some((item) => item.trace_id === current) ? current : ''); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Dashboard request failed'); }
    finally { setLoading(false); }
  };
  useEffect(() => { void load(); }, [window]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setTrace(undefined); if (selectedTrace) void dashboardApi.trace(selectedTrace).then(setTrace).catch((cause) => setError(cause instanceof Error ? cause.message : 'Trace request failed')); }, [selectedTrace]);
  const resourceCPU = useMemo(() => overview?.resources.map((point) => point.cpu_percent) ?? [], [overview]);
  if (!overview && loading) return <div className="dashboard-view dashboard-state"><RefreshCw className="spin" /> Loading aggregate telemetry…</div>;
  if (!overview) return <div className="dashboard-view dashboard-state error"><TriangleAlert /> <div><strong>Dashboard unavailable</strong><p>{error || 'Sign in and claim this device to enable private usage telemetry.'}</p><button onClick={load}>Try again</button></div></div>;
  const summary = overview.summary;
  return <div className="dashboard-view">
    <header className="dashboard-header"><div><span className="eyebrow">OBSERVABILITY</span><h1>Agent operations</h1><p>Runs, usage, dependencies, and runtime health from ClickHouse aggregates.</p></div><div className="dashboard-actions">
      {overview.demo_data && <span className="demo-badge"><Sparkles size={14} /> Demo data</span>}
      <div className="window-picker">{(['24h','7d','30d','90d','365d'] as DashboardWindow[]).map((item) => <button className={window === item ? 'active' : ''} key={item} onClick={() => setWindow(item)}>{item}</button>)}</div>
      <button className="icon-button" title="Refresh" onClick={load}><RefreshCw className={loading ? 'spin' : ''} size={17} /></button>
      {onClose && <button className="icon-button" title="Close" onClick={onClose}><X size={18} /></button>}
    </div></header>
    {error && <div className="dashboard-warning"><TriangleAlert size={16} /> Showing the last aggregate response. Refresh failed: {error}</div>}
    <div className="summary-grid">
      <article><Activity /><span>Agent team runs</span><strong>{compact.format(summary.runs)}</strong><small>{summary.success_rate}% successful</small></article>
      <article><Bot /><span>Active agents</span><strong>{summary.active_agents}</strong><small>{summary.teams} team{summary.teams === 1 ? '' : 's'}</small></article>
      <article><Sparkles /><span>Total tokens</span><strong>{compact.format(summary.input_tokens + summary.output_tokens)}</strong><small>{compact.format(summary.cached_tokens)} cached</small></article>
      <article><Braces /><span>Response size</span><strong>{compact.format(summary.response_chars)}</strong><small>tool and skill chars</small></article>
      <article><Coins /><span>Estimated cost</span><strong>{money.format(summary.cost_usd)}</strong><small>selected window</small></article>
      <article><Clock3 /><span>p95 latency</span><strong>{(summary.p95_latency_ms / 1000).toFixed(1)}s</strong><small>end-to-end runs</small></article>
      <article className={summary.errors ? 'error-metric' : ''}><TriangleAlert /><span>Errors</span><strong>{summary.errors}</strong><small>across spans</small></article>
    </div>
    <div className="dashboard-grid wide-left">
      <section className="dashboard-card timeline-card"><div className="card-heading"><div><h3>Runs and token volume</h3><p>Server-side time buckets; raw spans stay in ClickHouse</p></div><strong>{compact.format(summary.input_tokens + summary.output_tokens)} tokens</strong></div><Sparkline values={overview.timeline.map((point) => point.runs)} errors={overview.timeline.map((point) => point.errors)} /><div className="timeline-legend"><span><i /> Successful volume</span><span><i className="error" /> Bucket with errors</span></div></section>
      <section className="dashboard-card resource-card"><div className="card-heading"><div><h3>Runtime resources</h3><p>CPU · memory · disk · network</p></div><strong>{resourceCPU.at(-1)?.toFixed(0) ?? 0}% CPU</strong></div><Sparkline values={resourceCPU} /><div className="resource-stat"><span>Memory</span><strong>{overview.resources.at(-1)?.memory_mb.toFixed(0) ?? 0} MB</strong></div><div className="resource-stat"><span>Disk / block I/O</span><strong>{overview.resources.at(-1)?.disk_gb.toFixed(2) ?? 0} GB</strong></div><div className="resource-stat"><span>Network RX / TX</span><strong>{overview.resources.at(-1)?.network_rx_mb.toFixed(1) ?? 0} / {overview.resources.at(-1)?.network_tx_mb.toFixed(1) ?? 0} MB</strong></div></section>
    </div>
    {dependencies && <DependencyGraph graph={dependencies} />}
    <TraceExplorer list={traces} detail={trace} selected={selectedTrace} onSelect={setSelectedTrace} />
    <div className="dashboard-grid thirds"><RankedList title="Top skills" rows={overview.skills} /><RankedList title="Tool activity" rows={overview.tools} /><RankedList title="Model usage" rows={overview.models} /></div>
    <section className="dashboard-card agents-card"><div className="card-heading"><div><h3>Agent usage</h3><p>Aggregated token, cost, error, and latency totals</p></div></div><div className="agent-usage-table"><div className="usage-row usage-head"><span>Agent</span><span>Runs</span><span>Tokens</span><span>Cost</span><span>Error rate</span><span>p95</span></div>{overview.agents.map((agent) => <div className="usage-row" key={agent.id}><span><i /> <strong>{agent.name}</strong></span><span>{agent.runs}</span><span>{compact.format(agent.tokens)}</span><span>{money.format(agent.cost_usd)}</span><span className={agent.error_rate ? 'danger' : ''}>{agent.error_rate}%</span><span>{Math.round(agent.p95_latency_ms)} ms</span></div>)}</div></section>
    <footer>Aggregate generated {new Date(overview.generated_at).toLocaleString()} · retention and tenant isolation are enforced by the gateway.</footer>
  </div>;
}
