import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, Check, ChevronDown, Loader2, Plus, RefreshCw, Search, X } from 'lucide-react';
import { groupSkillsByCategory, skillCategoryOrder } from '../utils/skills';
import type { Agent, AgentSkill, AgentSkillMap } from '../types';
import type { SkillSyncPreview, SkillSyncResult } from '../api/skills';

const INITIAL_VISIBLE = 24;
const VIEW_MORE_STEP = 24;

type Stat = { label: string; value: string };

function StatsStrip({ stats }: { stats: Stat[] }) {
  return (
    <div className="skv-stats" role="group">
      {stats.map((stat) => (
        <div className="skv-stat" key={stat.label}>
          <span className="skv-stat-value">{stat.value}</span>
          <span className="skv-stat-label">{stat.label}</span>
        </div>
      ))}
    </div>
  );
}

function SkillsSkeleton() {
  return (
    <div className="skv-loading" role="status" aria-label="Loading skills">
      <div className="skv-stats skv-loading-stats">
        {Array.from({ length: 4 }, (_, index) => (
          <div className="skv-stat" key={index}>
            <span className="skv-skel skv-skel-stat-value" />
            <span className="skv-skel skv-skel-stat-label" />
          </div>
        ))}
      </div>
      <div className="skills-view-controls skv-loading-controls">
        <span className="skv-skel skv-skel-search" />
        <span className="skv-skel skv-skel-filter" />
      </div>
      <div className="skills-view-scroll">
        <div className="skv-grid">
          {Array.from({ length: 8 }, (_, index) => (
            <article className="skv-card skv-card-skeleton" key={index}>
              <span className="skv-skel skv-skel-title" />
              <span className="skv-skel skv-skel-line" />
              <span className="skv-skel skv-skel-line short" />
              <span className="skv-skel skv-skel-foot" />
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}

export function SkillsView({
  library,
  agents,
  agentSkills,
  loading = false,
  search,
  onSearch,
  groupFilter,
  onGroupFilter,
  onInstall,
  onSetDefaultEnabled,
  installPending,
  installError,
  onInstallExisting,
  onApply,
  onPreviewSync,
  onSync,
  onClose,
}: {
  library: AgentSkill[];
  agents: Agent[];
  agentSkills: AgentSkillMap;
  loading?: boolean;
  search: string;
  onSearch: (value: string) => void;
  groupFilter: string;
  onGroupFilter: (value: string) => void;
  onInstall: (source: string, force?: boolean) => Promise<boolean>;
  onSetDefaultEnabled: (skillId: string, enabled: boolean) => Promise<boolean>;
  installPending: boolean;
  installError: string;
  onInstallExisting: (id: string, agentIds: string[]) => void;
  onApply: (skillIds: string[], agentIds: string[]) => void;
  onPreviewSync?: (agentIds: string[]) => Promise<SkillSyncPreview>;
  onSync?: (agentIds: string[], sourceRevision: string) => Promise<SkillSyncResult>;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [installOpen, setInstallOpen] = useState(false);
  const [installName, setInstallName] = useState('');
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [applyAgentIds, setApplyAgentIds] = useState<string[]>([]);
  const [visible, setVisible] = useState(INITIAL_VISIBLE);
  const [togglePendingId, setTogglePendingId] = useState('');
  const [syncOpen, setSyncOpen] = useState(false);
  const [syncAgentIds, setSyncAgentIds] = useState<string[]>([]);
  const [syncSearch, setSyncSearch] = useState('');
  const [syncPreview, setSyncPreview] = useState<SkillSyncPreview | null>(null);
  const [syncResult, setSyncResult] = useState<SkillSyncResult | null>(null);
  const [syncPending, setSyncPending] = useState(false);
  const [syncError, setSyncError] = useState('');
  const query = search.trim().toLowerCase();
  const eligibleAgents = useMemo(
    () => agents.filter((agent) => selectedSkillIds.some(
      (skillId) => !agentSkills[agent.id]?.[skillId],
    )),
    [agentSkills, agents, selectedSkillIds],
  );
  const syncEligibleAgents = useMemo(
    () => agents.filter((agent) => agent.id !== 'big-brother'),
    [agents],
  );
  const visibleSyncAgents = useMemo(() => {
    const value = syncSearch.trim().toLowerCase();
    return value
      ? syncEligibleAgents.filter((agent) => `${agent.title} ${agent.id}`.toLowerCase().includes(value))
      : syncEligibleAgents;
  }, [syncEligibleAgents, syncSearch]);

  const filteredLibrary = useMemo(() => library.filter((skill) => {
    if (groupFilter !== 'all' && skill.category !== groupFilter) return false;
    return !query || [skill.name, skill.category, skill.description].some((field) => field.toLowerCase().includes(query));
  }), [library, groupFilter, query]);

  const categories = useMemo(() => {
    const present = [...new Set(library.map((skill) => skill.category).filter(Boolean))];
    present.sort((left, right) => {
      const leftIndex = skillCategoryOrder.indexOf(left);
      const rightIndex = skillCategoryOrder.indexOf(right);
      return (leftIndex < 0 ? Number.MAX_SAFE_INTEGER : leftIndex) - (rightIndex < 0 ? Number.MAX_SAFE_INTEGER : rightIndex) || left.localeCompare(right);
    });
    return ['all', ...present];
  }, [library]);

  useEffect(() => setVisible(INITIAL_VISIBLE), [groupFilter, query]);

  const stats = useMemo<Stat[]>(() => {
    const installed = library.filter((skill) => skill.installed).length;
    const inUse = agents.filter((agent) => Object.values(agentSkills[agent.id] ?? {}).some(Boolean)).length;
    return [
      { label: t('skillsView.statSkills', { defaultValue: 'Skills' }), value: String(library.length) },
      { label: t('skillsView.statInstalled', { defaultValue: 'Installed' }), value: String(installed) },
      { label: t('skillsView.statCategories', { defaultValue: 'Categories' }), value: String(new Set(library.map((skill) => skill.category)).size) },
      { label: t('skillsView.statInUse', { defaultValue: 'In use' }), value: `${inUse}/${agents.length}` },
    ];
  }, [library, agents, agentSkills, t]);

  const shownSkills = filteredLibrary.slice(0, visible);
  const groups = groupSkillsByCategory(shownSkills);
  const toggleSkill = (id: string) => setSelectedSkillIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const toggleAgent = (id: string) => setApplyAgentIds((current) => current.includes(id) ? current.filter((item) => item !== id) : [...current, id]);
  const confirmInstall = async () => {
    if (!installName.trim() || installPending) return;
    if (await onInstall(installName.trim())) {
      setInstallName('');
      setInstallOpen(false);
    }
  };
  const apply = () => {
    onApply(selectedSkillIds, applyAgentIds);
    setSelectedSkillIds([]);
    setApplyAgentIds([]);
  };
  useEffect(() => {
    const eligibleIds = new Set(eligibleAgents.map((agent) => agent.id));
    setApplyAgentIds((current) => {
      const next = current.filter((agentId) => eligibleIds.has(agentId));
      return next.length === current.length ? current : next;
    });
  }, [eligibleAgents]);
  const setDefaultEnabled = async (skill: AgentSkill) => {
    if (togglePendingId) return;
    setTogglePendingId(skill.skill_id);
    await onSetDefaultEnabled(skill.skill_id, !skill.enabled);
    setTogglePendingId('');
  };
  const closeSync = () => {
    if (syncPending) return;
    setSyncOpen(false);
    setSyncAgentIds([]);
    setSyncSearch('');
    setSyncPreview(null);
    setSyncResult(null);
    setSyncError('');
  };
  const openSync = () => {
    setSyncAgentIds([]);
    setSyncPreview(null);
    setSyncResult(null);
    setSyncError('');
    setSyncOpen(true);
  };
  const toggleSyncAgent = (agentId: string) => setSyncAgentIds((current) =>
    current.includes(agentId) ? current.filter((id) => id !== agentId) : [...current, agentId]);
  const reviewSync = async () => {
    if (!onPreviewSync || syncAgentIds.length === 0) return;
    setSyncPending(true);
    setSyncError('');
    try {
      setSyncPreview(await onPreviewSync(syncAgentIds));
    } catch (value) {
      setSyncError(value instanceof Error ? value.message : 'Could not preview the skill sync.');
    } finally {
      setSyncPending(false);
    }
  };
  const confirmSync = async () => {
    if (!onSync || !syncPreview) return;
    setSyncPending(true);
    setSyncError('');
    try {
      setSyncResult(await onSync(syncAgentIds, syncPreview.source_revision));
    } catch (value) {
      setSyncError(value instanceof Error ? value.message : 'Could not synchronize skills.');
    } finally {
      setSyncPending(false);
    }
  };
  const retryFailed = () => {
    const failed = syncResult?.agents.filter((item) => item.status === 'failed').map((item) => item.agent_id) ?? [];
    setSyncAgentIds(failed);
    setSyncResult(null);
    setSyncPreview(null);
    setSyncError('');
    void reviewSyncFor(failed);
  };
  const reviewSyncFor = async (agentIds: string[]) => {
    if (!onPreviewSync || agentIds.length === 0) return;
    setSyncPending(true);
    try {
      setSyncPreview(await onPreviewSync(agentIds));
    } catch (value) {
      setSyncError(value instanceof Error ? value.message : 'Could not preview the skill sync.');
    } finally {
      setSyncPending(false);
    }
  };
  const initialLoading = loading && library.length === 0;

  return (
    <div className="skills-view" aria-busy={loading}>
      <header className="skills-view-top">
        <div><h1>{t('skillsView.title')}</h1><p>{t('skillsView.subtitle')}</p></div>
        <div className="skills-view-actions">
          {onPreviewSync && onSync && <button className="skv-install-btn" disabled={syncEligibleAgents.length === 0} onClick={openSync}><RefreshCw size={15} />Sync agents</button>}
          <button className={installOpen ? 'skv-install-btn open' : 'skv-install-btn'} onClick={() => setInstallOpen((current) => !current)}><Plus size={15} />{t('skillsView.installNew')}</button>
          <button className="icon-button" title={t('common.close')} onClick={onClose}><X size={17} /></button>
        </div>
      </header>

      {installOpen && (
        <div className="skv-install-panel">
          <div className="skv-install-row">
            <input value={installName} onChange={(event) => setInstallName(event.target.value)} placeholder={t('skillsView.installPlaceholder')} onKeyDown={(event) => event.key === 'Enter' && void confirmInstall()} autoFocus />
            <button className="skv-install-confirm" disabled={!installName.trim() || installPending} onClick={() => void confirmInstall()}><Check size={15} />{installPending ? t('common.loading', { defaultValue: 'Installing…' }) : t('common.install')}</button>
          </div>
          <p className="skv-install-hint">{t('skillsView.installHint')}</p>
          {installError && <p className="skv-install-error" role="alert">{installError}</p>}
        </div>
      )}

      {initialLoading ? <SkillsSkeleton /> : <>
      <StatsStrip stats={stats} />
      <div className="skills-view-controls">
        <div className="skv-search"><Search size={15} /><input value={search} onChange={(event) => onSearch(event.target.value)} placeholder={t('skillsView.searchPlaceholder')} /></div>
        <div className="skv-filter-select">
          <select value={groupFilter} onChange={(event) => onGroupFilter(event.target.value)} aria-label={t('skillsView.categoryLabel', { defaultValue: 'Filter by category' })}>
            {categories.map((category) => <option key={category} value={category}>{category === 'all' ? t('skillsView.allCategories', { defaultValue: 'All categories' }) : category}</option>)}
          </select>
          <ChevronDown size={15} />
        </div>
      </div>

      <div className="skills-view-scroll">
        {groups.map(([category, skills]) => (
          <div className="skv-group" key={category}>
            <div className="skv-group-title"><span className={`cat-dot ${category}`} />{category}<em>{skills.length}</em></div>
            <div className="skv-grid">
              {skills.map((skill) => {
                const selected = selectedSkillIds.includes(skill.skill_id);
                const usedBy = agents.filter((agent) => agentSkills[agent.id]?.[skill.skill_id]).length;
                return (
                  <article className={`${selected ? 'skv-card selected' : 'skv-card'}${skill.enabled ? '' : ' disabled'}`} key={skill.skill_id} onClick={() => skill.installed && toggleSkill(skill.skill_id)}>
                    <div className="skv-card-head">
                      {skill.installed && <input type="checkbox" checked={selected} onChange={() => toggleSkill(skill.skill_id)} onClick={(event) => event.stopPropagation()} />}
                      <strong>{skill.name}</strong>
                      {skill.installed && (
                        <label
                          className="toggle skv-default-toggle"
                          title={t(skill.enabled ? 'skillsView.disableDefault' : 'skillsView.enableDefault', { name: skill.name })}
                          onClick={(event) => event.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={skill.enabled}
                            disabled={togglePendingId === skill.skill_id}
                            aria-label={t('skillsView.defaultEnabledLabel', { name: skill.name })}
                            onChange={() => void setDefaultEnabled(skill)}
                          />
                          <span />
                        </label>
                      )}
                      <span className={skill.enabled ? 'skv-badge installed' : 'skv-badge'}>{skill.enabled ? t('skillsView.enabled', { defaultValue: 'enabled' }) : t('skillsView.disabled', { defaultValue: 'disabled' })}</span>
                    </div>
                    <p className="skv-desc">{skill.description}</p>
                    <span className="skill-tooltip" role="tooltip">{skill.description || skill.name}</span>
                    <div className="skv-card-foot">
                      {skill.installed ? <span className="skv-used">{usedBy}/{agents.length} agents</span> : <button className="skv-install-mini" disabled={agents.length === 0} onClick={(event) => { event.stopPropagation(); onInstallExisting(skill.skill_id, agents.map((agent) => agent.id)); }}><Plus size={13} />{t('common.install')}</button>}
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        ))}
        {filteredLibrary.length === 0 && <div className="skv-empty">{t('skillsView.empty')}</div>}
        {visible < filteredLibrary.length && <div className="skv-view-more"><button onClick={() => setVisible((current) => current + VIEW_MORE_STEP)}>{t('skillsView.viewMore')}<em>{filteredLibrary.length - visible}</em></button></div>}
      </div>

      {selectedSkillIds.length > 0 && (
        <footer className="skv-apply-bar">
          <span className="skv-apply-count">{t('skillsView.selected', { count: selectedSkillIds.length })}</span>
          <div className="skv-apply-agents">{eligibleAgents.map((agent) => <label key={agent.id} className={applyAgentIds.includes(agent.id) ? 'skv-agent-chip on' : 'skv-agent-chip'}><input type="checkbox" checked={applyAgentIds.includes(agent.id)} onChange={() => toggleAgent(agent.id)} />{agent.title}</label>)}</div>
          <button className="skv-apply-btn" disabled={applyAgentIds.length === 0} onClick={apply}><Check size={15} />{t('skillsView.applyToAgents', { count: applyAgentIds.length })}</button>
        </footer>
      )}
      </>}

      {syncOpen && (
        <div className="skv-sync-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && closeSync()}>
          <section className="skv-sync-dialog" role="dialog" aria-modal="true" aria-labelledby="skill-sync-title">
            <header className="skv-sync-header">
              <div>
                <h2 id="skill-sync-title">{syncResult ? 'Sync complete' : syncPreview ? 'Confirm skill sync' : 'Sync common skills'}</h2>
                <p>{syncResult ? 'Review the result for each agent.' : syncPreview ? 'Common skills replace matching agent copies. Private skills are preserved.' : 'Choose agents that should receive the enabled common skills.'}</p>
              </div>
              <button className="icon-button" aria-label="Close" disabled={syncPending} onClick={closeSync}><X size={17} /></button>
            </header>

            {!syncPreview && !syncResult && (
              <div className="skv-sync-body">
                <div className="skv-sync-note"><Check size={16} /><span><strong>{library.filter((skill) => skill.enabled).length} enabled common skills</strong> will be synchronized. Disabled common skills are removed from selected agents.</span></div>
                <div className="skv-sync-toolbar">
                  <div className="skv-search"><Search size={15} /><input value={syncSearch} onChange={(event) => setSyncSearch(event.target.value)} placeholder="Search agents" autoFocus /></div>
                  <button onClick={() => setSyncAgentIds(syncAgentIds.length === syncEligibleAgents.length ? [] : syncEligibleAgents.map((agent) => agent.id))}>{syncAgentIds.length === syncEligibleAgents.length ? 'Clear all' : 'Select all'}</button>
                </div>
                <div className="skv-sync-agent-list">
                  {visibleSyncAgents.map((agent) => {
                    const installed = Object.keys(agentSkills[agent.id] ?? {}).length;
                    const enabled = Object.values(agentSkills[agent.id] ?? {}).filter(Boolean).length;
                    return <label className={syncAgentIds.includes(agent.id) ? 'selected' : ''} key={agent.id}>
                      <input type="checkbox" checked={syncAgentIds.includes(agent.id)} onChange={() => toggleSyncAgent(agent.id)} />
                      <span><strong>{agent.title}</strong><small>{agent.id}</small></span>
                      <em>{enabled}/{installed} enabled</em>
                    </label>;
                  })}
                </div>
              </div>
            )}

            {syncPreview && !syncResult && (
              <div className="skv-sync-body">
                <div className="skv-sync-summary">
                  <span className="add">+{syncPreview.totals.added} added</span>
                  <span className="update">{syncPreview.totals.updated} updated</span>
                  <span className="remove">−{syncPreview.totals.removed} removed</span>
                  <span>{syncPreview.totals.preserved} private preserved</span>
                </div>
                {syncPreview.totals.removed > 0 && <div className="skv-sync-warning"><AlertTriangle size={16} />Disabled common skill copies listed below will be removed.</div>}
                <div className="skv-sync-plans">
                  {syncPreview.agents.map((plan) => <details key={plan.agent_id} open={syncPreview.agents.length <= 3}>
                    <summary><strong>{agents.find((agent) => agent.id === plan.agent_id)?.title ?? plan.agent_id}</strong><span>+{plan.added.length} · {plan.updated.length} updated · −{plan.removed.length}</span></summary>
                    <div>
                      <SkillChangeList label="Add" values={plan.added} />
                      <SkillChangeList label="Update" values={plan.updated} />
                      <SkillChangeList label="Remove" values={plan.removed} danger />
                      <SkillChangeList label="Preserve private" values={plan.preserved} />
                      {!plan.added.length && !plan.updated.length && !plan.removed.length && <p>No common skill changes.</p>}
                    </div>
                  </details>)}
                </div>
              </div>
            )}

            {syncResult && (
              <div className="skv-sync-body">
                <div className={syncResult.failed ? 'skv-sync-warning' : 'skv-sync-note'}>{syncResult.failed ? <AlertTriangle size={16} /> : <Check size={16} />}<span><strong>{syncResult.completed} agents synchronized.</strong>{syncResult.failed ? ` ${syncResult.failed} failed.` : ' All selected agents are up to date.'}</span></div>
                <div className="skv-sync-results">{syncResult.agents.map((result) => <div className={result.status} key={result.agent_id}><Check size={15} /><span><strong>{agents.find((agent) => agent.id === result.agent_id)?.title ?? result.agent_id}</strong>{result.error && <small>{result.error}</small>}</span><em>{result.status}</em></div>)}</div>
              </div>
            )}

            {syncError && <p className="skv-sync-error" role="alert">{syncError}</p>}
            <footer className="skv-sync-footer">
              {syncPreview && !syncResult && <button disabled={syncPending} onClick={() => { setSyncPreview(null); setSyncError(''); }}>Back</button>}
              <span />
              {!syncPreview && !syncResult && <button className="primary" disabled={syncPending || syncAgentIds.length === 0} onClick={() => void reviewSync()}>{syncPending && <Loader2 className="spin" size={15} />}Review changes ({syncAgentIds.length})</button>}
              {syncPreview && !syncResult && <button className="primary" disabled={syncPending} onClick={() => void confirmSync()}>{syncPending && <Loader2 className="spin" size={15} />}Confirm sync</button>}
              {syncResult?.failed ? <button className="primary" onClick={retryFailed}>Retry failed</button> : syncResult ? <button className="primary" onClick={closeSync}>Done</button> : null}
            </footer>
          </section>
        </div>
      )}
    </div>
  );
}

function SkillChangeList({ label, values, danger = false }: { label: string; values: string[]; danger?: boolean }) {
  if (!values.length) return null;
  return <p className={danger ? 'danger' : ''}><strong>{label}:</strong> {values.join(', ')}</p>;
}
