import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { CalendarClock, Check, Clock3, Pause, Play, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import type { CreateCronInput } from '../api/crons';
import type { Agent, CronDetail, CronJob } from '../types';

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

export function CronView({
  agents,
  crons,
  status,
  error,
  pendingId,
  detail = null,
  onCreate,
  onToggle,
  onRun,
  onDelete,
  onLoadDetail = async () => {},
  onCloseDetail = () => {},
}: {
  agents: Agent[];
  crons: CronJob[];
  status: 'idle' | 'loading' | 'ready' | 'error';
  error: string;
  pendingId: string;
  detail?: CronDetail | null;
  onCreate: (input: CreateCronInput) => Promise<void>;
  onToggle: (id: string) => Promise<void>;
  onRun: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onLoadDetail?: (id: string) => Promise<void>;
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

  useEffect(() => {
    if (!selectedId || detail?.job.id !== selectedId || detail.run?.state !== 'running') return;
    const timer = window.setInterval(() => void onLoadDetail(selectedId), 2500);
    return () => window.clearInterval(timer);
  }, [detail, onLoadDetail, selectedId]);

  const openDetail = (id: string) => {
    setSelectedId(id);
    void onLoadDetail(id);
  };

  const closeDetail = () => {
    setSelectedId('');
    onCloseDetail();
  };

  const agentNames = useMemo(() => new Map(agents.map((agent) => [agent.id, agent.title])), [agents]);
  const visible = crons.filter((job) => (filter === 'all' || job.state === filter) && (agentFilter === 'all' || job.agentId === agentFilter));
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

  return (
    <section className="cron-page">
      <header className="cron-page-header">
        <div>
          <p className="cron-eyebrow">{t('cron.eyebrow', { defaultValue: 'Automation' })}</p>
          <h1>{t('cron.globalTitle', { defaultValue: 'Scheduled tasks' })}</h1>
          <p>{t('cron.globalSubtitle', { defaultValue: 'Let your assistants take care of recurring work while you focus on what matters.' })}</p>
        </div>
        <button className="cron-new-button" onClick={() => setCreateOpen(true)}><Plus size={16} /> {t('cron.new')}</button>
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
          <span>{t('cron.agent', { defaultValue: 'Assistant' })}</span>
          <select value={agentFilter} onChange={(event) => setAgentFilter(event.target.value)}>
            <option value="all">{t('cron.allAssistants', { defaultValue: 'All assistants' })}</option>
            {agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.title}</option>)}
          </select>
        </label>
      </div>

      {error && <div className="cron-page-error" role="alert">{error}</div>}
      {status === 'loading' ? (
        <div className="cron-page-empty"><Clock3 size={22} /><strong>{t('cron.loading', { defaultValue: 'Loading scheduled tasks…' })}</strong></div>
      ) : visible.length === 0 ? (
        <div className="cron-page-empty"><CalendarClock size={26} /><strong>{t('cron.globalEmpty', { defaultValue: 'Nothing scheduled yet' })}</strong><span>{t('cron.globalEmptyHint', { defaultValue: 'Create a scheduled task and let an assistant handle it for you.' })}</span><button className="cron-new-button" onClick={() => setCreateOpen(true)}><Plus size={15} /> {t('cron.new')}</button></div>
      ) : (
        <div className="cron-job-grid">
          {visible.map((job) => {
            const busy = pendingId === job.id;
            const assistant = agentNames.get(job.agentId) ?? job.agentId;
            return (
              <article className="cron-job-card" key={job.id} role="button" tabIndex={0} onClick={() => openDetail(job.id)} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') openDetail(job.id); }}>
                <div className="cron-job-heading">
                  <div className="cron-job-icon"><CalendarClock size={18} /></div>
                  <div><h2>{job.name}</h2><p>{assistant}</p></div>
                  <span className={`cron-friendly-status ${job.state}`}><span />{t(`cron.${job.state}`, { defaultValue: job.state })}</span>
                </div>
                <p className="cron-job-prompt">{job.prompt}</p>
                <div className="cron-job-meta"><span><Clock3 size={14} />{scheduleCopy(job)}</span><span><CalendarClock size={14} />{nextRunCopy(job)}</span></div>
                <div className="cron-job-actions">
                  <button disabled={busy} onClick={(event) => { event.stopPropagation(); void onRun(job.id).then(() => openDetail(job.id)); }}><Play size={14} /> {t('cron.runNow', { defaultValue: 'Run now' })}</button>
                  <button disabled={busy} onClick={(event) => { event.stopPropagation(); void onToggle(job.id); }}>{job.state === 'stopped' ? <Play size={14} /> : <Pause size={14} />} {job.state === 'stopped' ? t('cron.start') : t('cron.stop')}</button>
                  <button className="danger" disabled={busy} onClick={(event) => { event.stopPropagation(); void onDelete(job.id); }}><Trash2 size={14} /> {t('cron.delete')}</button>
                </div>
              </article>
            );
          })}
        </div>
      )}

      {selectedId && (
        <div className="cron-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) closeDetail(); }}>
          <aside className="cron-detail-drawer" role="dialog" aria-modal="true" aria-label="Scheduled task details">
            <header>
              <div><p className="cron-eyebrow">Scheduled task</p><h2>{detail?.job.name ?? 'Loading…'}</h2></div>
              <button onClick={closeDetail} aria-label={t('common.close')}><X size={18} /></button>
            </header>
            {!detail || detail.job.id !== selectedId ? (
              <div className="cron-detail-loading"><Clock3 size={20} /> Loading details…</div>
            ) : (
              <div className="cron-detail-body">
                <section><span>Assistant</span><strong>{agentNames.get(detail.job.agentId) ?? detail.job.agentId}</strong></section>
                <section><span>What it does</span><p>{detail.job.prompt}</p></section>
                <div className="cron-detail-facts">
                  <section><span>Schedule</span><strong>{scheduleCopy(detail.job)}</strong></section>
                  <section><span>Next run</span><strong>{nextRunCopy(detail.job)}</strong></section>
                </div>
                <section className="cron-run-result">
                  <div className="cron-run-result-heading">
                    <div><span>Latest run</span><strong className={`cron-run-state ${detail.run?.state ?? 'empty'}`}>{detail.run?.state ?? 'Not run yet'}</strong></div>
                    <button onClick={() => void onLoadDetail(selectedId)}><RefreshCw size={14} /> Refresh</button>
                  </div>
                  {!detail.run ? <p className="cron-run-empty">Run this task to see its result here.</p> : detail.run.state === 'running' ? <p className="cron-run-empty">The assistant is working on this task…</p> : detail.run.error ? <pre className="cron-run-output error">{detail.run.error}</pre> : <pre className="cron-run-output">{detail.run.output || 'The run completed without text output.'}</pre>}
                  {detail.run?.completedAt && <small>Completed {new Date(detail.run.completedAt).toLocaleString()}</small>}
                </section>
              </div>
            )}
          </aside>
        </div>
      )}

      {createOpen && (
        <div className="cron-modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) resetForm(); }}>
          <form className="cron-modal" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
            <div className="cron-modal-heading"><div><p className="cron-eyebrow">{t('cron.eyebrow', { defaultValue: 'Automation' })}</p><h2>{t('cron.createTitle', { defaultValue: 'Schedule a task' })}</h2></div><button type="button" onClick={resetForm} aria-label={t('common.close')}>×</button></div>
            <label>{t('cron.agent', { defaultValue: 'Assistant' })}<select value={agentId} onChange={(event) => setAgentId(event.target.value)}>{agents.map((agent) => <option value={agent.id} key={agent.id}>{agent.title}</option>)}</select></label>
            <label>{t('cron.name')}<input value={name} onChange={(event) => setName(event.target.value)} placeholder={t('cron.namePlaceholder')} autoFocus /></label>
            <label>{t('cron.prompt')}<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={t('cron.promptPlaceholder')} rows={4} /></label>
            <label>{t('cron.intervalLabel')}<input type="number" min={1} value={interval} onChange={(event) => setInterval(event.target.value)} /></label>
            <div className="cron-modal-actions"><button type="button" onClick={resetForm}>{t('common.cancel')}</button><button className="primary" type="submit" disabled={pendingId === 'create' || !agentId || !name.trim() || !prompt.trim() || Number(interval) < 1}><Check size={15} />{pendingId === 'create' ? t('cron.creating', { defaultValue: 'Creating…' }) : t('cron.create')}</button></div>
          </form>
        </div>
      )}
    </section>
  );
}
