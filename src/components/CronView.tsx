import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Bot, CalendarClock, Check, ChevronDown, Clock3, Columns3, FileText, Mail, MessageCircle, MoreHorizontal, Pause, Play, Plus, RefreshCw, Send, Settings2, Trash2, X } from 'lucide-react';
import type { CreateCronInput } from '../api/crons';
import type { Agent, CronBlueprint, CronDeliveryOption, CronDeliveryTargetType, CronDetail, CronJob, CronJobRun } from '../types';

type Filter = 'all' | 'scheduled' | 'running' | 'stopped';

function scheduleCopy(job: CronJob): string {
  if (job.intervalMinutes > 0) {
    if (job.intervalMinutes % 60 === 0) return `Every ${job.intervalMinutes / 60} hour${job.intervalMinutes === 60 ? '' : 's'}`;
    return `Every ${job.intervalMinutes} minutes`;
  }
  return job.schedule || 'On a custom schedule';
}

function nextRunCopy(job: CronJob): string {
  if (!job.nextRun) return 'Not scheduled yet';
  const date = new Date(job.nextRun);
  return Number.isNaN(date.getTime()) ? 'Not scheduled yet' : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

function nextRunRelative(job: CronJob): string {
  if (!job.nextRun) return '';
  const target = new Date(job.nextRun).getTime();
  if (Number.isNaN(target)) return '';
  const diff = target - Date.now();
  if (diff <= 0) return 'Due now';
  const minutes = Math.round(diff / 60000);
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return `In ${days}d ${hours}h`;
  if (hours > 0) return `In ${hours}h ${mins}m`;
  return `In ${mins}m`;
}

function runDuration(run: { triggeredAt?: string; completedAt?: string } | null): string {
  if (!run?.triggeredAt || !run?.completedAt) return '';
  const start = new Date(run.triggeredAt).getTime();
  const end = new Date(run.completedAt).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '';
  const seconds = Math.round((end - start) / 1000);
  const mm = String(Math.floor(seconds / 60)).padStart(2, '0');
  const ss = String(seconds % 60).padStart(2, '0');
  return `${mm}:${ss}`;
}

function stateIcon(state: string | undefined, size = 13) {
  if (state === 'success' || state === 'delivered') return <Check size={size} />;
  if (state === 'failed') return <X size={size} />;
  if (state === 'running') return <Clock3 size={size} />;
  return <Clock3 size={size} />;
}

export function CronView({
  agents,
  crons,
  status,
  profileStates = {},
  error,
  pendingId,
  detail = null,
  blueprints = [],
  deliveryOptions = [],
  onCreate,
  onInstantiateBlueprint = async () => ({}) as CronJob,
  onAddTarget = async () => {},
  onRemoveTarget = async () => {},
  onLoadRuns = async () => [],
  onToggle,
  onRun,
  onDelete,
  onLoadDetail = async () => {},
  onCloseDetail = () => {},
}: {
  agents: Agent[];
  crons: CronJob[];
  status: 'idle' | 'loading' | 'ready' | 'error';
  profileStates?: Record<string, 'loading' | 'ready' | 'error'>;
  error: string;
  pendingId: string;
  detail?: CronDetail | null;
  blueprints?: CronBlueprint[];
  deliveryOptions?: CronDeliveryOption[];
  onCreate: (input: CreateCronInput) => Promise<void>;
  onInstantiateBlueprint?: (input: { blueprint: string; agentId: string; values: Record<string, unknown> }) => Promise<CronJob>;
  onAddTarget?: (id: string, target: { targetType: string; destination: string }, agentId?: string) => Promise<unknown>;
  onRemoveTarget?: (id: string, targetId: string, agentId?: string) => Promise<void>;
  onLoadRuns?: (id: string, agentId?: string) => Promise<CronJobRun[]>;
  onToggle: (id: string, agentId?: string) => Promise<void>;
  onRun: (id: string, agentId?: string) => Promise<void>;
  onDelete: (id: string, agentId?: string) => Promise<void>;
  onLoadDetail?: (id: string, agentId?: string) => Promise<void>;
  onCloseDetail?: () => void;
}) {
  const { t } = useTranslation();
  const [filter, setFilter] = useState<Filter>('all');
  const [agentFilter, setAgentFilter] = useState('all');
  const [createOpen, setCreateOpen] = useState(false);
  const [agentId, setAgentId] = useState(agents[0]?.id ?? '');
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [interval, setInterval] = useState('60');
  const [selectedId, setSelectedId] = useState('');
  const [selectedAgentId, setSelectedAgentId] = useState('');
  const [selectedRunId, setSelectedRunId] = useState('');
  const [blueprintOpen, setBlueprintOpen] = useState(false);
  const [selectedBlueprint, setSelectedBlueprint] = useState<CronBlueprint | null>(null);
  const [blueprintValues, setBlueprintValues] = useState<Record<string, unknown>>({});
  const [blueprintAgentId, setBlueprintAgentId] = useState(agents[0]?.id ?? '');
  const [targetType, setTargetType] = useState<CronDeliveryTargetType>('file');
  const [targetDestination, setTargetDestination] = useState('reports/automation.md');
  const [targetComposerOpen, setTargetComposerOpen] = useState(false);
  const [detailMenuOpen, setDetailMenuOpen] = useState(false);

  useEffect(() => {
    if (!selectedId || detail?.job.id !== selectedId || detail.run?.state !== 'running') return;
    const timer = window.setInterval(() => void onLoadDetail(selectedId, selectedAgentId), 2500);
    return () => window.clearInterval(timer);
  }, [detail, onLoadDetail, selectedAgentId, selectedId]);

  const openDetail = (id: string, agentId: string) => {
    setSelectedId(id);
    setSelectedAgentId(agentId);
    setSelectedRunId('');
    void onLoadDetail(id, agentId);
  };

  const closeDetail = () => {
    setSelectedId('');
    setSelectedAgentId('');
    setSelectedRunId('');
    setDetailMenuOpen(false);
    onCloseDetail();
  };

  const detailRuns = useMemo<CronJobRun[]>(() => {
    if (!detail) return [];
    if ((detail.runs ?? []).length > 0) {
      const runs = detail.runs ?? [];
      if (!detail.run) return runs;
      const matched = runs.some((run) => run.id === detail.run?.id);
      if (!matched) return [{ ...detail.run, deliveries: detail.run.deliveries ?? [] }, ...runs];
      return runs.map((run) => run.id === detail.run?.id ? {
        ...run,
        output: detail.run?.output ?? run.output,
        error: detail.run?.error ?? run.error,
      } : run);
    }
    return detail.run ? [{ ...detail.run, deliveries: detail.run.deliveries ?? [] }] : [];
  }, [detail]);

  const selectedRun = detailRuns.find((run) => run.id === selectedRunId) ?? detailRuns[0] ?? null;

  useEffect(() => {
    if (!selectedRunId && detailRuns[0]) setSelectedRunId(detailRuns[0].id);
  }, [detailRuns, selectedRunId]);

  const agentNames = useMemo(() => new Map(agents.map((agent) => [agent.id, agent.title])), [agents]);
  const visible = crons.filter((job) => (filter === 'all' || job.state === filter) && (agentFilter === 'all' || job.agentId === agentFilter));
  const profileGroups = agents
    .filter((agent) => agentFilter === 'all' || agent.id === agentFilter)
    .map((agent) => ({
      agent,
      jobs: visible.filter((job) => job.agentId === agent.id),
      state: profileStates[agent.id] ?? (status === 'loading' ? 'loading' : 'ready'),
    }));
  const counts = {
    all: crons.length,
    scheduled: crons.filter((job) => job.state === 'scheduled').length,
    running: crons.filter((job) => job.state === 'running').length,
    stopped: crons.filter((job) => job.state === 'stopped').length,
  };

  const resetForm = () => {
    setName('');
    setPrompt('');
    setInterval('60');
    setAgentId(agents[0]?.id ?? '');
    setCreateOpen(false);
  };

  const submit = async () => {
    const intervalMinutes = Number.parseInt(interval, 10);
    if (!agentId || !name.trim() || !prompt.trim() || !Number.isFinite(intervalMinutes) || intervalMinutes < 1) return;
    await onCreate({ agentId, name: name.trim(), prompt: prompt.trim(), intervalMinutes });
    resetForm();
  };

  const chooseBlueprint = (blueprint: CronBlueprint) => {
    setSelectedBlueprint(blueprint);
    setBlueprintValues(Object.fromEntries(blueprint.fields.map((field) => [field.name, field.default ?? ''])));
    setBlueprintAgentId(agents[0]?.id ?? '');
  };

  const createFromBlueprint = async () => {
    if (!selectedBlueprint || !blueprintAgentId) return;
    await onInstantiateBlueprint({ blueprint: selectedBlueprint.key, agentId: blueprintAgentId, values: blueprintValues });
    setBlueprintOpen(false);
    setSelectedBlueprint(null);
  };

  const addTarget = async () => {
    if (!selectedId || !targetDestination.trim()) return;
    await onAddTarget(selectedId, { targetType, destination: targetDestination.trim() }, selectedAgentId);
    setTargetComposerOpen(false);
  };

  const openTargetComposer = (type: CronDeliveryTargetType = 'file') => {
    setTargetType(type);
    const option = deliveryOptions.find((item) => item.targetType === type);
    setTargetDestination(type === 'file' ? 'reports/automation.md' : type === 'kanban' ? 'default' : option?.id ?? '');
    setTargetComposerOpen(true);
  };

  return (
    <section className="cron-page">
      <header className="cron-page-header">
        <div>
          <p className="cron-eyebrow">{t('cron.eyebrow', { defaultValue: 'Automation' })}</p>
          <h1>{t('cron.globalTitle', { defaultValue: 'Scheduled tasks' })}</h1>
          <p>{t('cron.globalSubtitle', { defaultValue: 'Let your agents take care of recurring work while you focus on what matters.' })}</p>
        </div>
        <div className="cron-header-actions">
          <button className="cron-secondary-button" onClick={() => setBlueprintOpen(true)}>Blueprints</button>
          <button className="cron-new-button" onClick={() => setCreateOpen(true)}><Plus size={16} /> {t('cron.new')}</button>
        </div>
      </header>

      <div className="cron-summary" aria-label="Task summary">
        <div><strong>{counts.all}</strong><span>{t('cron.all', { defaultValue: 'All tasks' })}</span></div>
        <div><strong>{counts.scheduled}</strong><span>{t('cron.scheduled')}</span></div>
        <div><strong>{counts.running}</strong><span>{t('cron.running')}</span></div>
        <div><strong>{counts.stopped}</strong><span>{t('cron.stopped')}</span></div>
      </div>

      <div className="cron-page-toolbar">
        <div className="cron-filters">
          {(['all', 'scheduled', 'running', 'stopped'] as Filter[]).map((key) => (
            <button key={key} className={filter === key ? 'active' : ''} onClick={() => setFilter(key)}>
              {t(`cron.${key}`, { defaultValue: key === 'all' ? 'All' : key })} <span>{counts[key]}</span>
            </button>
          ))}
        </div>
        <label className="cron-agent-filter">
          <span>{t('cron.agent', { defaultValue: 'Agent' })}</span>
          <select value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)}>
            <option value="all">{t('cron.allAssistants', { defaultValue: 'All agents' })}</option>
            {agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.title}</option>)}
          </select>
        </label>
      </div>

      {error && <div className="cron-page-error" role="alert">{error}</div>}
      {profileGroups.length === 0 ? (
        <div className="cron-page-empty"><CalendarClock size={26} /><strong>{t('cron.globalEmpty', { defaultValue: 'Nothing scheduled yet' })}</strong><span>{t('cron.globalEmptyHint', { defaultValue: 'Create a scheduled task and let an agent handle it for you.' })}</span><button className="cron-new-button" onClick={() => setCreateOpen(true)}><Plus size={15} /> {t('cron.new')}</button></div>
      ) : (
        <div className="cron-profile-groups">
          {profileGroups.map(({ agent, jobs, state }) => (
            <section className="cron-profile-group" key={agent.id}>
              <header className="cron-profile-heading">
                <div className="cron-profile-avatar"><Bot size={17} /></div>
                <div><h2>{agent.title}</h2><p>{agent.id}</p></div>
                {state === 'loading' ? <span className="cron-profile-loading"><RefreshCw size={13} /> Loading…</span> : state === 'error' ? <span className="cron-profile-error"><AlertTriangle size={13} /> Failed</span> : <span className="cron-profile-count">{jobs.length} task{jobs.length === 1 ? '' : 's'}</span>}
              </header>
              {state === 'loading' && jobs.length === 0 ? (
                <div className="cron-profile-placeholder"><Clock3 size={16} /> Loading this profile’s schedules…</div>
              ) : jobs.length === 0 ? (
                <div className="cron-profile-placeholder">No tasks match this filter.</div>
              ) : (
                <div className="cron-job-grid">
                  {jobs.map((job) => {
                    const busy = pendingId === job.id;
                    return (
                      <article className="cron-job-card" key={job.id} role="button" tabIndex={0} onClick={() => openDetail(job.id, job.agentId)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') openDetail(job.id, job.agentId); }}>
                        <div className="cron-job-heading">
                          <div className="cron-job-icon"><CalendarClock size={18} /></div>
                          <div><h2>{job.name}</h2><p>{agent.title}</p></div>
                          <span className={`cron-friendly-status ${job.state}`}><span />{t(`cron.${job.state}`, { defaultValue: job.state })}</span>
                        </div>
                        <p className="cron-job-prompt">{job.prompt}</p>
                        <div className="cron-job-meta"><span><Clock3 size={14} />{scheduleCopy(job)}</span><span><CalendarClock size={14} />{nextRunCopy(job)}</span></div>
                        <div className="cron-job-actions">
                          <button disabled={busy} onClick={(event) => { event.stopPropagation(); void onRun(job.id, job.agentId).then(() => openDetail(job.id, job.agentId)); }}><Play size={14} /> {t('cron.runNow', { defaultValue: 'Run now' })}</button>
                          <button disabled={busy} onClick={(event) => { event.stopPropagation(); void onToggle(job.id, job.agentId); }}>{job.state === 'stopped' ? <Play size={14} /> : <Pause size={14} />} {job.state === 'stopped' ? t('cron.start') : t('cron.stop')}</button>
                          <button className="danger" disabled={busy} onClick={(event) => { event.stopPropagation(); void onDelete(job.id, job.agentId); }}><Trash2 size={14} /> {t('cron.delete')}</button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>
          ))}
        </div>
      )}

      {selectedId && (
        <div className="cron-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDetail(); }}>
          <div className="cron-detail-drawer" role="dialog" aria-modal="true" aria-label="Scheduled task details">
            <header>
              <div className="cron-detail-titlebar">
                <button className="cron-detail-back" onClick={closeDetail} aria-label={t('common.close')}><Bot size={20} /></button>
                <div className="cron-detail-title">
                  <h2>{detail?.job.name ?? 'Loading…'}</h2>
                  {detail && <span className="cron-detail-sub"><Bot size={13} />{agentNames.get(detail.job.agentId) ?? detail.job.agentId}<i>•</i>{scheduleCopy(detail.job)}</span>}
                </div>
                {detail && <div className="cron-detail-header-actions">
                  <button className="run-now" onClick={() => void onRun(selectedId, selectedAgentId)}><Play size={14} /> Run now</button>
                  <span className={`cron-friendly-status ${detail.job.state}`}><span />{t(`cron.${detail.job.state}`, { defaultValue: detail.job.state })}</span>
                  <div className="cron-detail-menu-wrap">
                    <button className="cron-detail-menu-toggle" onClick={() => setDetailMenuOpen((open) => !open)} aria-label="More actions" aria-expanded={detailMenuOpen}><MoreHorizontal size={18} /></button>
                    {detailMenuOpen && <div className="cron-detail-menu" role="menu">
                      <button role="menuitem" onClick={() => { setDetailMenuOpen(false); void onToggle(selectedId, selectedAgentId); }}>{detail.job.state === 'stopped' ? <><Play size={14} /> Start schedule</> : <><Pause size={14} /> Pause schedule</>}</button>
                      <button role="menuitem" className="danger" onClick={() => { setDetailMenuOpen(false); void onDelete(selectedId, selectedAgentId).then(closeDetail); }}><Trash2 size={14} /> Delete automation</button>
                    </div>}
                  </div>
                </div>}
              </div>
            </header>
            {!detail || detail.job.id !== selectedId ? (
              <div className="cron-detail-loading"><Clock3 size={20} /> Loading details…</div>
            ) : (
              <div className="cron-detail-body cron-runbook-layout">
                <aside className="cron-run-list">
                  <div className="cron-run-list-heading"><span>Run history</span><button onClick={() => void onLoadRuns(selectedId, selectedAgentId)} aria-label="Refresh run history"><RefreshCw size={14} /></button></div>
                  {detailRuns.length === 0 ? <p className="cron-run-empty">No runs yet.</p> : detailRuns.slice(0, 5).map((run, index) => (
                    <button className={`cron-run-card ${run.state} ${selectedRun?.id === run.id ? 'active' : ''}`} onClick={() => setSelectedRunId(run.id)} key={run.id}>
                      <span className={`cron-run-badge ${run.state}`}>{stateIcon(run.state, 13)}</span>
                      <div className="cron-run-card-body">
                        <div className="cron-run-card-top"><strong>#{detailRuns.length - index}</strong><span className={`cron-run-state ${run.state}`}>{run.state}</span></div>
                        <small>{run.triggeredAt ? new Date(run.triggeredAt).toLocaleString() : run.id}</small>
                        <em>By system</em>
                      </div>
                    </button>
                  ))}
                  {detailRuns.length > 5 && <button className="cron-run-viewall" onClick={() => void onLoadRuns(selectedId, selectedAgentId)}>View all runs</button>}
                </aside>

                <main className="cron-run-canvas">
                  <div className="cron-run-canvas-heading"><span>Execution graph</span><div className="cron-canvas-actions"><button onClick={() => void onLoadDetail(selectedId, selectedAgentId)}><RefreshCw size={14} /> Refresh</button>{selectedRun && <strong className={`cron-run-state ${selectedRun.state}`}>{selectedRun.state === 'success' ? 'Completed' : selectedRun.state === 'running' ? 'Running' : selectedRun.state === 'failed' ? 'Failed' : selectedRun.state}</strong>}</div></div>
                  <div className="cron-run-graph">
                    <div className="cron-graph-node schedule"><CalendarClock size={17} /><div><span>Trigger</span><strong>{scheduleCopy(detail.job)}</strong><small>{selectedRun?.triggeredAt ? new Date(selectedRun.triggeredAt).toLocaleString() : nextRunCopy(detail.job)}</small></div></div>
                    <div className={`cron-graph-edge ${selectedRun?.state ?? 'idle'}`}>
                      <span>{selectedRun ? stateIcon(selectedRun.state, 11) : null}</span>
                    </div>
                    <div className={`cron-graph-node agent ${selectedRun?.state ?? 'idle'}`}><Bot size={17} /><div><span>Agent run</span><strong>{agentNames.get(detail.job.agentId) ?? detail.job.agentId}</strong><small>{selectedRun?.state ?? 'Not started'}</small></div>{selectedRun && <span className={`cron-node-status ${selectedRun.state}`}>{stateIcon(selectedRun.state, 14)}</span>}</div>
                    <div className="cron-graph-edge branch" aria-hidden="true"><span /></div>
                    <div className="cron-graph-target-heading"><span>Delivery targets</span><button onClick={() => openTargetComposer()} title="Add destination"><Plus size={14} /><span>Add target</span></button></div>
                    <div className="cron-graph-targets">
                      {(detail.targets ?? []).length === 0 ? <div className="cron-graph-empty"><Send size={17} /><strong>No delivery targets</strong><span>Attach the first destination to this automation.</span><button onClick={() => openTargetComposer()}><Plus size={14} /> Add destination</button></div> : (detail.targets ?? []).map((target) => {
                        const delivery = selectedRun?.deliveries.find((item) => item.targetId === target.id);
                        const state = delivery?.status ?? (selectedRun?.state === 'success' ? 'pending' : selectedRun?.state ?? 'pending');
                        const icon = target.targetType === 'file' ? <FileText size={16} /> : target.targetType === 'email' ? <Mail size={16} /> : target.targetType === 'kanban' ? <Columns3 size={16} /> : <MessageCircle size={16} />;
                        return <div className={`cron-graph-node target ${state}`} key={target.id}><div className="cron-node-icon">{icon}</div><div><span>{target.targetType}</span><strong>{target.destination}</strong><small className={`cron-node-state ${state}`}><i />{delivery?.reason || state}</small></div></div>;
                      })}
                    </div>
                    {targetComposerOpen && <div className="cron-inline-target-composer">
                      <div><span>Add delivery destination</span><button onClick={() => setTargetComposerOpen(false)} aria-label="Close destination form"><X size={14} /></button></div>
                      <select value={targetType} onChange={(event) => { const next = event.target.value as CronDeliveryTargetType; setTargetType(next); const option = deliveryOptions.find((item) => item.targetType === next); setTargetDestination(next === 'file' ? 'reports/automation.md' : next === 'kanban' ? 'default' : option?.id ?? ''); }}><option value="file">Workspace file</option><option value="kanban">Kanban board</option><option value="email">Email</option><option value="channel">Channel</option></select>
                      {targetType === 'channel' || targetType === 'email' ? <select value={targetDestination} onChange={(event) => setTargetDestination(event.target.value)}><option value="">Choose target</option>{deliveryOptions.filter((item) => item.targetType === targetType).map((item) => <option value={item.id} key={`${item.targetType}-${item.id}`} disabled={!item.available}>{item.name}{item.available ? '' : ' — not configured'}</option>)}</select> : <input value={targetDestination} onChange={(event) => setTargetDestination(event.target.value)} placeholder={targetType === 'file' ? 'reports/digest.md' : 'default'} />}
                      <button className="primary" disabled={!targetDestination.trim()} onClick={() => void addTarget()}><Plus size={14} /> Add destination</button>
                    </div>}
                  </div>

                  <section className="cron-run-inspector">
                    <span className="cron-inspector-label">Run output</span>
                    <div className="cron-inspector-status">
                      <div><span className={`cron-inspector-icon ${selectedRun?.state ?? 'empty'}`}>{selectedRun ? stateIcon(selectedRun.state, 14) : <Clock3 size={14} />}</span><strong className={`cron-run-state ${selectedRun?.state ?? 'empty'}`}>{selectedRun ? (selectedRun.state === 'success' ? 'Success' : selectedRun.state === 'running' ? 'Running' : selectedRun.state === 'failed' ? 'Failed' : selectedRun.state) : 'Not run yet'}</strong></div>
                      {selectedRun && runDuration(selectedRun) && <small>Finished in {runDuration(selectedRun)}</small>}
                    </div>
                    {!selectedRun ? <p className="cron-run-empty">Run this automation to inspect its output and deliveries.</p> : selectedRun.state === 'running' ? <p className="cron-run-empty">The agent is working on this run…</p> : selectedRun.error ? <pre className="cron-run-output error">{selectedRun.error}</pre> : <pre className="cron-run-output">{selectedRun.output || 'The run completed without text output.'}</pre>}
                    {selectedRun && (selectedRun.output || selectedRun.error) && <div className="cron-inspector-footer"><span><ChevronDown size={13} /> Output preview (Markdown)</span></div>}
                  </section>
                </main>

                <aside className="cron-run-config">
                  <div className="cron-config-heading"><span>Configuration</span><Settings2 size={15} /></div>
                  <section className="cron-config-prompt"><span>Prompt</span><p>{detail.job.prompt}</p></section>
                  <div className="cron-config-card">
                    <span className="cron-config-card-icon"><CalendarClock size={16} /></span>
                    <div><span>Next run</span><strong>{nextRunCopy(detail.job)}</strong>{nextRunRelative(detail.job) && <small className="accent">{nextRunRelative(detail.job)}</small>}</div>
                  </div>
                  <div className="cron-config-card">
                    <span className={`cron-config-card-icon ${detail.job.state}`}>{detail.job.state === 'running' ? <Clock3 size={16} /> : detail.job.state === 'stopped' ? <Pause size={16} /> : <Check size={16} />}</span>
                    <div><span>Status</span><strong>{t(`cron.${detail.job.state}`, { defaultValue: detail.job.state })}</strong><small>Automated run</small></div>
                  </div>
                  <section className="cron-delivery-section">
                    <div className="cron-config-subheading"><span>Deliver to</span><strong>{detail.targets?.length ?? 0} targets</strong></div>
                    <div className="cron-target-list">{(detail.targets ?? []).map((target) => {
                      const icon = target.targetType === 'file' ? <FileText size={14} /> : target.targetType === 'email' ? <Mail size={14} /> : target.targetType === 'kanban' ? <Columns3 size={14} /> : <MessageCircle size={14} />;
                      const label = target.targetType === 'file' ? 'Workspace file' : target.targetType === 'kanban' ? 'Kanban board' : target.targetType === 'email' ? 'Email' : 'Channel';
                      return <div className={`cron-target-row ${target.available ? 'available' : 'unavailable'}`} key={target.id}>
                        <div className="cron-target-row-icon">{icon}</div>
                        <div className="cron-target-row-copy"><strong>{label}</strong><span>{target.destination}</span><small>{target.available ? <><i />Pending</> : <><AlertTriangle size={11} />Delivery target is not configured</>}</small></div>
                        <button onClick={() => void onRemoveTarget(selectedId, target.id, selectedAgentId)} aria-label={`Remove ${target.targetType} target`}><Trash2 size={14} /></button>
                      </div>;
                    })}</div>
                    <button className="cron-manage-targets" onClick={() => openTargetComposer()}><Plus size={14} /> Manage delivery targets</button>
                  </section>
                </aside>
              </div>
            )}
          </div>
        </div>
      )}

      {createOpen && (
        <div className="cron-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) resetForm(); }}>
          <form className="cron-modal" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
            <div className="cron-modal-heading"><div><p className="cron-eyebrow">{t('cron.eyebrow', { defaultValue: 'Automation' })}</p><h2>{t('cron.createTitle', { defaultValue: 'Schedule a task' })}</h2></div><button type="button" onClick={resetForm} aria-label={t('common.close')}>×</button></div>
            <label>{t('cron.agent', { defaultValue: 'Agent' })}<select value={agentId} onChange={(event) => setAgentId(event.target.value)}>{agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.title}</option>)}</select></label>
            <label>{t('cron.name')}<input value={name} onChange={(event) => setName(event.target.value)} placeholder={t('cron.namePlaceholder')} autoFocus /></label>
            <label>{t('cron.prompt')}<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={t('cron.promptPlaceholder')} rows={4} /></label>
            <label>{t('cron.intervalLabel')}<input type="number" min={1} value={interval} onChange={(event) => setInterval(event.target.value)} /></label>
            <div className="cron-modal-actions"><button type="button" onClick={resetForm}>{t('common.cancel')}</button><button className="primary" type="submit" disabled={pendingId === 'create' || !agentId || !name.trim() || !prompt.trim() || Number(interval) < 1}><Check size={15} />{pendingId === 'create' ? t('cron.creating', { defaultValue: 'Creating…' }) : t('cron.create')}</button></div>
          </form>
        </div>
      )}

      {blueprintOpen && (
        <div className="cron-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) { setBlueprintOpen(false); setSelectedBlueprint(null); } }}>
          <div className="cron-blueprint-modal" role="dialog" aria-modal="true" aria-label="Automation blueprints">
            <div className="cron-modal-heading"><div><p className="cron-eyebrow">Automation library</p><h2>{selectedBlueprint?.title ?? 'Choose a blueprint'}</h2></div><button type="button" onClick={() => { setBlueprintOpen(false); setSelectedBlueprint(null); }}>×</button></div>
            {!selectedBlueprint ? (
              <div className="cron-blueprint-grid">{blueprints.map((blueprint) => <article key={blueprint.key}><span>{blueprint.category}</span><h3>{blueprint.title}</h3><p>{blueprint.description}</p><small>{blueprint.scheduleHuman}</small><button onClick={() => chooseBlueprint(blueprint)}>Use blueprint</button></article>)}</div>
            ) : (
              <div className="cron-blueprint-form">
                <label>Agent<select value={blueprintAgentId} onChange={(event) => setBlueprintAgentId(event.target.value)}>{agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.title}</option>)}</select></label>
                {selectedBlueprint.fields.map((field) => <label key={field.name}>{field.label}{field.type === 'enum' || field.type === 'weekdays' ? <select value={String(blueprintValues[field.name] ?? '')} onChange={(event) => setBlueprintValues((current) => ({ ...current, [field.name]: event.target.value }))}>{(field.options ?? []).map((option) => <option value={String(option)} key={String(option)}>{String(option)}</option>)}</select> : <input type={field.type === 'time' ? 'time' : 'text'} value={String(blueprintValues[field.name] ?? '')} onChange={(event) => setBlueprintValues((current) => ({ ...current, [field.name]: event.target.value }))} />}{field.help && <small>{field.help}</small>}</label>)}
                <div className="cron-modal-actions"><button onClick={() => setSelectedBlueprint(null)}>Back</button><button className="primary" disabled={!blueprintAgentId} onClick={() => void createFromBlueprint()}><Check size={15} /> Create automation</button></div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
