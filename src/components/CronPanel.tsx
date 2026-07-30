import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, Clock3, Play, Plus, Square, Trash2 } from 'lucide-react';
import type { CronJob } from '../types';

type CronFormInput = { name: string; prompt: string; intervalMinutes: number };

function scheduleLabel(job: CronJob): string {
  return job.name || job.schedule;
}

export function CronPanel({
  crons,
  status,
  error,
  pendingId,
  onCreate,
  onToggle,
  onRun,
  onDelete,
}: {
  crons: CronJob[];
  status: 'idle' | 'loading' | 'ready' | 'error';
  error: string;
  pendingId: string;
  onCreate: (input: CronFormInput) => Promise<void>;
  onToggle: (id: string) => Promise<void>;
  onRun: (id: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [formOpen, setFormOpen] = useState(false);
  const [name, setName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [interval, setInterval] = useState('120');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const submit = async () => {
    const intervalMinutes = Number.parseInt(interval, 10);
    if (!name.trim() || !prompt.trim() || !Number.isFinite(intervalMinutes) || intervalMinutes < 1) return;
    await onCreate({ name: name.trim(), prompt: prompt.trim(), intervalMinutes });
    setName('');
    setPrompt('');
    setInterval('120');
    setFormOpen(false);
  };

  return (
    <section className="panel-section cron-panel">
      <div className="skills-toolbar">
        <span className="skills-toolbar-title">{t('cron.title')}</span>
        <button className={formOpen ? 'add-skill-btn open' : 'add-skill-btn'} onClick={() => setFormOpen((value) => !value)}>
          <Plus size={15} />
          {t('cron.new')}
        </button>
      </div>

      {formOpen && (
        <div className="cron-form">
          <label>
            {t('cron.name')}
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder={t('cron.namePlaceholder')} autoFocus />
          </label>
          <label>
            {t('cron.prompt')}
            <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={t('cron.promptPlaceholder')} rows={3} />
          </label>
          <label>
            {t('cron.intervalLabel')}
            <input type="number" min={1} value={interval} onChange={(event) => setInterval(event.target.value)} />
          </label>
          <p className="cron-form-hint">{t('cron.providerHint', { defaultValue: "The job uses this assistant's current provider and model." })}</p>
          <div className="cron-form-actions">
            <button className="conn-btn ghost" onClick={() => setFormOpen(false)}>{t('common.cancel')}</button>
            <button
              className="conn-btn primary"
              disabled={pendingId === 'create' || !name.trim() || !prompt.trim() || Number(interval) < 1}
              onClick={() => void submit()}
            >
              <Check size={15} />
              {pendingId === 'create' ? t('cron.creating', { defaultValue: 'Creating…' }) : t('cron.create')}
            </button>
          </div>
        </div>
      )}

      {error && <div className="cron-error" role="alert">{error}</div>}
      {status === 'loading' ? (
        <div className="skills-empty"><Clock3 size={18} /><p>{t('cron.loading', { defaultValue: 'Loading cron jobs…' })}</p></div>
      ) : crons.length === 0 ? (
        <div className="skills-empty"><Clock3 size={18} /><p>{t('cron.empty')}</p></div>
      ) : (
        <div className="cron-list">
          {crons.map((job) => {
            const expanded = expandedId === job.id;
            const busy = pendingId === job.id;
            const canToggle = job.state === 'scheduled' || job.state === 'stopped';
            return (
              <article className={expanded ? 'cron-card expanded' : 'cron-card'} key={job.id}>
                <button className="cron-head" onClick={() => setExpandedId(expanded ? null : job.id)}>
                  <ChevronDown size={14} className={expanded ? 'cron-caret open' : 'cron-caret'} />
                  <div className="cron-head-main">
                    <strong>{scheduleLabel(job)}</strong>
                    <small>{job.prompt || job.schedule}</small>
                  </div>
                  <span className={`cron-badge ${job.state}`}>{t(`cron.${job.state}`, { defaultValue: job.state })}</span>
                </button>

                <div className="cron-sub">
                  <Clock3 size={12} />
                  <span>{job.nextRun ? `${t('cron.nextRun')}: ${new Date(job.nextRun).toLocaleString()}` : t('cron.noNextRun', { defaultValue: 'No next run scheduled' })}</span>
                </div>

                {expanded && (
                  <div className="cron-detail">
                    <dl>
                      <div><dt>{t('cron.agent', { defaultValue: 'Agent' })}</dt><dd>{job.agentId}</dd></div>
                      <div><dt>{t('cron.state')}</dt><dd>{t(`cron.${job.state}`, { defaultValue: job.state })}</dd></div>
                      <div><dt>{t('cron.schedule')}</dt><dd>{job.schedule}</dd></div>
                      <div><dt>{t('cron.nextRun')}</dt><dd>{job.nextRun ?? '—'}</dd></div>
                    </dl>
                  </div>
                )}

                <div className="cron-actions">
                  <button className="conn-btn ghost" disabled={busy} onClick={() => void onRun(job.id)}>
                    <Play size={13} /> {t('cron.runNow', { defaultValue: 'Run now' })}
                  </button>
                  <button className="conn-btn ghost" disabled={busy || !canToggle} onClick={() => void onToggle(job.id)}>
                    {job.state === 'stopped' ? <Play size={13} /> : <Square size={13} />}
                    {job.state === 'stopped' ? t('cron.start') : t('cron.stop')}
                  </button>
                  <button className="conn-btn danger" disabled={busy} onClick={() => void onDelete(job.id)}>
                    <Trash2 size={13} /> {t('cron.delete')}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
