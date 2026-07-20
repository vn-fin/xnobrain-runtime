import { useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Check,
  ChevronDown,
  Clock,
  Code2,
  Play,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  Wrench,
  X,
  ArrowUpDown,
} from 'lucide-react';
import { WorkspacePanel, type WorkspaceController } from './WorkspacePanel';
import type { ResponsePagination } from '../api/client';
import type { Agent, AgentSkill, AgentSkillMap, CronJob, GlobalRuntimeConfig, ProviderConnector, RightView } from '../types';

const AGENT_ACTIONS = ['Create', 'Metadata', 'Runtime', 'Memory', 'Test', 'Delete'];
type WriteApprovalPatch = Partial<Pick<GlobalRuntimeConfig, 'skillsWriteApproval' | 'memoryWriteApproval'>>;

export function RightPanel({
  rightView,
  onRightView,
  agent,
  library,
  agentSkills,
  providers,
  defaultConfig,
  onToggleSkill,
  onSetSkillEnabled,
  onUpdateWriteApprovals,
  skillsPagination,
  onLoadSkillsPage,
  crons,
  onCreateCron,
  onToggleCron,
  onDeleteCron,
  onCreateAgent,
  onOpenSettings,
  onDeleteAgent,
  workspaceOpenRequest,
  workspace,
  width,
  onResize,
}: {
  rightView: RightView;
  onRightView: (v: RightView) => void;
  agent: Agent;
  library: AgentSkill[];
  agentSkills: AgentSkillMap;
  providers: ProviderConnector[];
  defaultConfig: GlobalRuntimeConfig | null;
  onToggleSkill: (skillId: string) => void;
  onSetSkillEnabled: (skillId: string, enabled: boolean) => void;
  onUpdateWriteApprovals: (updates: WriteApprovalPatch) => Promise<void>;
  skillsPagination?: ResponsePagination;
  onLoadSkillsPage: (page: number) => void;
  crons: CronJob[];
  onCreateCron: (input: { name: string; prompt: string; intervalMinutes: number; forever: boolean }) => void;
  onToggleCron: (id: string) => void;
  onDeleteCron: (id: string) => void;
  onCreateAgent: () => void;
  onOpenSettings: () => void;
  onDeleteAgent: () => void;
  workspaceOpenRequest?: { path: string; token: number };
  workspace: WorkspaceController;
  width: number;
  onResize: (width: number) => void;
}) {
  const { t } = useTranslation();

  // Agent-skills search (local UI state)
  const [skillSearchOpen, setSkillSearchOpen] = useState(false);
  const [skillSearch, setSkillSearch] = useState('');

  // Installed-skills list controls (search / filter / sort)
  const [listSearch, setListSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | 'on' | 'off'>('all');
  const [sortBy, setSortBy] = useState<'name' | 'category' | 'status'>('name');

  // Cron form (local UI state)
  const [cronFormOpen, setCronFormOpen] = useState(false);
  const [cronName, setCronName] = useState('');
  const [cronPrompt, setCronPrompt] = useState('');
  const [cronInterval, setCronInterval] = useState('120');
  const [cronForever, setCronForever] = useState(true);
  const [expandedCronId, setExpandedCronId] = useState<string | null>(null);
  const [approvalSaving, setApprovalSaving] = useState<'skills' | 'memory' | null>(null);
  const [approvalError, setApprovalError] = useState('');

  const enabledMap = agentSkills[agent.id] ?? {};
  const approvalsAvailable = defaultConfig !== null;

  const submitCron = () => {
    const interval = Math.max(1, parseInt(cronInterval, 10) || 0);
    if (!cronName.trim() || !cronPrompt.trim() || !interval) return;
    onCreateCron({ name: cronName.trim(), prompt: cronPrompt.trim(), intervalMinutes: interval, forever: cronForever });
    setCronName('');
    setCronPrompt('');
    setCronInterval('120');
    setCronForever(true);
    setCronFormOpen(false);
  };

  const startResize = (event: ReactPointerEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const onMove = (moveEvent: PointerEvent) => {
      // Panel is anchored to the right edge: dragging left widens it.
      onResize(startWidth + (startX - moveEvent.clientX));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      document.body.classList.remove('col-resizing');
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    document.body.classList.add('col-resizing');
  };

  const updateWriteApproval = async (target: 'skills' | 'memory', enabled: boolean) => {
    setApprovalSaving(target);
    setApprovalError('');
    try {
      await onUpdateWriteApprovals(target === 'skills'
        ? { skillsWriteApproval: enabled }
        : { memoryWriteApproval: enabled });
    } catch (value) {
      setApprovalError(value instanceof Error ? value.message : 'Could not update write approvals.');
    } finally {
      setApprovalSaving(null);
    }
  };

  return (
    <aside className="right-panel">
      <div
        className="right-resizer"
        role="separator"
        aria-orientation="vertical"
        aria-label={t('common.resizePanel', 'Resize panel')}
        title={t('common.resizePanel', 'Resize panel')}
        onPointerDown={startResize}
        onDoubleClick={() => onResize(330)}
      />
      <div className="right-head">
        <strong>{t('controls.title')}</strong>
        <button className="icon-button" title={t('common.close')}>
          <X size={17} />
        </button>
      </div>

      <div className="right-tabs">
        {(['workspace', 'skills', 'cron', 'runtime'] as const).map((tab) => (
          <button key={tab} className={rightView === tab ? 'active' : ''} onClick={() => onRightView(tab)}>
            {t(`controls.${tab}`)}
          </button>
        ))}
      </div>

      {rightView === 'workspace' && (
        <WorkspacePanel workspace={workspace} openRequest={workspaceOpenRequest} />
      )}

      {rightView === 'skills' && (
        <section className="panel-section">
          <div className="skills-toolbar">
            <span className="skills-toolbar-title">{t('agentSkills.title')}</span>
            <button
              className={skillSearchOpen ? 'add-skill-btn open' : 'add-skill-btn'}
              onClick={() => {
                setSkillSearchOpen((v) => !v);
                setSkillSearch('');
              }}
            >
              <Plus size={15} />
              {t('agentSkills.add')}
            </button>
          </div>

          {skillSearchOpen && (() => {
            const q = skillSearch.trim().toLowerCase();
            const results = library.filter(
              (s) => !q || s.name.toLowerCase().includes(q) || s.category.toLowerCase().includes(q) || s.description.toLowerCase().includes(q),
            );
            return (
              <div className="skill-search">
                <div className="skill-search-input">
                  <Search size={14} />
                  <input value={skillSearch} onChange={(e) => setSkillSearch(e.target.value)} placeholder={t('agentSkills.searchPlaceholder')} autoFocus />
                </div>
                <div className="skill-search-results">
                  {results.map((skill) => {
                    const added = !!enabledMap[skill.skill_id];
                    return (
                      <button
                        key={skill.skill_id}
                        className={added ? 'skill-search-row added' : 'skill-search-row'}
                        title={skill.description}
                        onClick={() => onToggleSkill(skill.skill_id)}
                      >
                        <span className={`cat-dot ${skill.category}`} />
                        <div className="skill-search-main">
                          <strong>{skill.name}</strong>
                          <small>{skill.description}</small>
                        </div>
                        {added ? <Check size={16} className="search-added" /> : <Plus size={16} />}
                      </button>
                    );
                  })}
                  {results.length === 0 && <div className="skill-search-empty">{t('agentSkills.noMatch', { query: skillSearch })}</div>}
                </div>
              </div>
            );
          })()}

          {(() => {
            const installedSkills = agent.skills;
            if (installedSkills.length === 0) {
              return (
                <div className="skills-empty">
                  <Sparkles size={18} />
                  <p>{t('agentSkills.empty')}</p>
                </div>
              );
            }
            const total = skillsPagination?.total_items ?? installedSkills.length;
            const totalPages = skillsPagination?.total_pages ?? 1;
            const currentPage = skillsPagination?.page ?? 0;

            const q = listSearch.trim().toLowerCase();
            const filtered = installedSkills
              .filter((s) => statusFilter === 'all' || (statusFilter === 'on' ? s.enabled : !s.enabled))
              .filter((s) => !q
                || s.name.toLowerCase().includes(q)
                || s.description.toLowerCase().includes(q)
                || s.path.toLowerCase().includes(q)
                || s.category.toLowerCase().includes(q));

            const sorted = [...filtered].sort((a, b) => {
              if (sortBy === 'status') return Number(b.enabled) - Number(a.enabled) || a.name.localeCompare(b.name);
              if (sortBy === 'category') return a.category.localeCompare(b.category) || a.name.localeCompare(b.name);
              return a.name.localeCompare(b.name);
            });

            return (
              <>
                <p className="skills-hint">{t('agentSkills.totalSkills', { count: total })}</p>

                <div className="skill-list-controls">
                  <div className="skill-list-search">
                    <Search size={14} />
                    <input
                      value={listSearch}
                      onChange={(e) => setListSearch(e.target.value)}
                      placeholder={t('agentSkills.filterPlaceholder')}
                    />
                    {listSearch && (
                      <button className="skill-list-clear" title={t('common.clear')} onClick={() => setListSearch('')}>
                        <X size={13} />
                      </button>
                    )}
                  </div>
                  <div className="skill-list-filters">
                    <div className="skill-seg">
                      {(['all', 'on', 'off'] as const).map((k) => (
                        <button
                          key={k}
                          className={statusFilter === k ? 'active' : ''}
                          onClick={() => setStatusFilter(k)}
                        >
                          {t(`agentSkills.filter.${k}`)}
                        </button>
                      ))}
                    </div>
                    <label className="skill-sort" title={t('agentSkills.sortBy')}>
                      <ArrowUpDown size={13} />
                      <select value={sortBy} onChange={(e) => setSortBy(e.target.value as typeof sortBy)}>
                        <option value="name">{t('agentSkills.sort.name')}</option>
                        <option value="category">{t('agentSkills.sort.category')}</option>
                        <option value="status">{t('agentSkills.sort.status')}</option>
                      </select>
                    </label>
                  </div>
                </div>

                {sorted.length === 0 ? (
                  <div className="skill-search-empty">{t('agentSkills.noMatch', { query: listSearch })}</div>
                ) : (
                  <div className="skill-list">
                    {sorted.map((skill) => (
                      <div className={skill.enabled ? 'skill-item on' : 'skill-item'} key={skill.skill_id} title={skill.description}>
                        <span className={`cat-dot ${skill.category}`} />
                        <div className="skill-item-main">
                          <strong>{skill.name}</strong>
                          <em>{skill.path || skill.category}</em>
                          <span className="skill-tooltip">{skill.description || skill.name}</span>
                        </div>
                        <label className="toggle" title={t(skill.enabled ? 'agentSkills.disableHint' : 'agentSkills.enableHint', { name: agent.title })}>
                          <input
                            type="checkbox"
                            checked={skill.enabled}
                            onChange={(e) => onSetSkillEnabled(skill.skill_id, e.target.checked)}
                          />
                          <span />
                        </label>
                      </div>
                    ))}
                  </div>
                )}

                {totalPages > 1 && (
                  <div className="skill-pager">
                    <button disabled={currentPage <= 0} onClick={() => onLoadSkillsPage(currentPage - 1)}>‹</button>
                    {Array.from({ length: totalPages }).map((_, i) => (
                      <button key={i} className={i === currentPage ? 'active' : ''} onClick={() => onLoadSkillsPage(i)}>
                        {i + 1}
                      </button>
                    ))}
                    <button disabled={currentPage >= totalPages - 1} onClick={() => onLoadSkillsPage(currentPage + 1)}>›</button>
                  </div>
                )}
              </>
            );
          })()}
        </section>
      )}

      {rightView === 'cron' && (
        <section className="panel-section">
          <div className="skills-toolbar">
            <span className="skills-toolbar-title">{t('cron.title')} · Demo data</span>
            <button className={cronFormOpen ? 'add-skill-btn open' : 'add-skill-btn'} onClick={() => setCronFormOpen((v) => !v)}>
              <Plus size={15} />
              {t('cron.new')}
            </button>
          </div>

          {cronFormOpen && (
            <div className="cron-form">
              <label>
                {t('cron.name')}
                <input value={cronName} onChange={(e) => setCronName(e.target.value)} placeholder={t('cron.namePlaceholder')} autoFocus />
              </label>
              <label>
                {t('cron.prompt')}
                <textarea value={cronPrompt} onChange={(e) => setCronPrompt(e.target.value)} placeholder={t('cron.promptPlaceholder')} rows={2} />
              </label>
              <div className="cron-form-row">
                <label>
                  {t('cron.intervalLabel')}
                  <input type="number" min={1} value={cronInterval} onChange={(e) => setCronInterval(e.target.value)} />
                </label>
                <label className="cron-forever">
                  <input type="checkbox" checked={cronForever} onChange={(e) => setCronForever(e.target.checked)} />
                  {t('cron.forever')}
                </label>
              </div>
              <div className="cron-form-actions">
                <button className="conn-btn ghost" onClick={() => setCronFormOpen(false)}>{t('common.cancel')}</button>
                <button className="conn-btn primary" disabled={!cronName.trim() || !cronPrompt.trim()} onClick={submitCron}>
                  <Check size={15} />
                  {t('cron.create')}
                </button>
              </div>
            </div>
          )}

          {crons.length === 0 ? (
            <div className="skills-empty">
              <Clock size={18} />
              <p>{t('cron.empty')}</p>
            </div>
          ) : (
            <div className="cron-list">
              {crons.map((job) => {
                const expanded = expandedCronId === job.id;
                const schedule = `${t('cron.every', { n: job.intervalMinutes })} (${job.forever ? t('cron.foreverSuffix') : t('cron.times', { n: job.repeatCount ?? 0 })})`;
                return (
                  <div className={expanded ? 'cron-card expanded' : 'cron-card'} key={job.id}>
                    <button className="cron-head" onClick={() => setExpandedCronId(expanded ? null : job.id)}>
                      <ChevronDown size={14} className={expanded ? 'cron-caret open' : 'cron-caret'} />
                      <div className="cron-head-main">
                        <strong>{job.name}</strong>
                        <small>{schedule}</small>
                      </div>
                      <span className={`cron-badge ${job.state}`}>{t(`cron.${job.state}`)}</span>
                    </button>

                    <div className="cron-sub">
                      <Clock size={12} />
                      <span>{t('cron.nextRun')}: {new Date(job.nextRun).toLocaleString()}</span>
                    </div>

                    {expanded && (
                      <div className="cron-detail">
                        <dl>
                          <div><dt>{t('cron.id')}</dt><dd>{job.id}</dd></div>
                          <div><dt>{t('cron.name')}</dt><dd>{job.name}</dd></div>
                          <div><dt>{t('cron.state')}</dt><dd>{t(`cron.${job.state}`)}</dd></div>
                          <div><dt>{t('cron.schedule')}</dt><dd>{schedule}</dd></div>
                          <div><dt>{t('cron.nextRun')}</dt><dd>{job.nextRun}</dd></div>
                          <div><dt>{t('cron.prompt')}</dt><dd>{job.prompt}</dd></div>
                        </dl>
                      </div>
                    )}

                    <div className="cron-actions">
                      <button className="conn-btn ghost" onClick={() => onToggleCron(job.id)}>
                        {job.state === 'stopped' ? <Play size={13} /> : <Square size={13} />}
                        {job.state === 'stopped' ? t('cron.start') : t('cron.stop')}
                      </button>
                      <button className="conn-btn danger" onClick={() => onDeleteCron(job.id)}>
                        <Trash2 size={13} />
                        {t('cron.delete')}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {rightView === 'runtime' && (
        <section className="panel-section">
          <div className="form-grid">
            <label>
              Name
              <input value={agent.title} readOnly />
            </label>
            <label>
              Description
              <textarea value={agent.description} readOnly />
            </label>
            <label>
              Provider
              <select value={agent.provider} disabled>
                {providers.map((item) => (
                  <option value={item.id} key={item.id}>{item.display_name}</option>
                ))}
              </select>
            </label>
            <label>
              Model
              <input value={agent.model} readOnly />
            </label>
            <label>
              Reasoning effort
              <select value={agent.reasoningEffort} disabled>
                <option>low</option>
                <option>medium</option>
                <option>high</option>
              </select>
            </label>
            <label className="inline-check">
              <input type="checkbox" checked={agent.approvalMode === 'auto'} readOnly />
              Auto approval
            </label>
          </div>
          <div className="runtime-approval-panel">
            <div className="runtime-approval-head">
              <span>
                <ShieldCheck size={15} />
                Write approvals
              </span>
              <em>{approvalsAvailable ? 'Agent profile file' : 'Unavailable'}</em>
            </div>
            <label className={approvalsAvailable ? 'runtime-approval-row' : 'runtime-approval-row disabled'}>
              <div className="runtime-approval-copy">
                <strong>New skill writes</strong>
                <small>{defaultConfig?.skillsWriteApproval ? 'Approval required' : 'Direct writes'}</small>
              </div>
              <input
                type="checkbox"
                checked={defaultConfig?.skillsWriteApproval ?? false}
                disabled={!approvalsAvailable || approvalSaving !== null}
                onChange={(e) => void updateWriteApproval('skills', e.target.checked)}
              />
              <span className="runtime-switch" />
            </label>
            <label className={approvalsAvailable ? 'runtime-approval-row' : 'runtime-approval-row disabled'}>
              <div className="runtime-approval-copy">
                <strong>Memory writes</strong>
                <small>{defaultConfig?.memoryWriteApproval ? 'Approval required' : 'Direct writes'}</small>
              </div>
              <input
                type="checkbox"
                checked={defaultConfig?.memoryWriteApproval ?? false}
                disabled={!approvalsAvailable || approvalSaving !== null}
                onChange={(e) => void updateWriteApproval('memory', e.target.checked)}
              />
              <span className="runtime-switch" />
            </label>
            {approvalError && <p className="runtime-approval-error">{approvalError}</p>}
          </div>
          <div className="button-grid">
            {AGENT_ACTIONS.map((label) => (
              <button
                key={label}
                onClick={() => {
                  if (label === 'Create') onCreateAgent();
                  else if (label === 'Metadata') onOpenSettings();
                  else if (label === 'Delete') onDeleteAgent();
                }}
              >
                <Wrench size={15} />
                {label}
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="api-note">
        <Code2 size={15} />
        <span>Mapped from agents, providers, conversations, workspace files, and sandbox APIs.</span>
      </div>
    </aside>
  );
}
