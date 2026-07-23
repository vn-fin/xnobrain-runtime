import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, ChevronDown, Plus, Search, X } from 'lucide-react';
import { groupSkillsByCategory, skillCategoryOrder } from '../utils/skills';
import type { Agent, AgentSkill, AgentSkillMap } from '../types';

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

export function SkillsView({
  library,
  agents,
  agentSkills,
  search,
  onSearch,
  groupFilter,
  onGroupFilter,
  onInstall,
  installPending,
  installError,
  onInstallExisting,
  onApply,
  onClose,
}: {
  library: AgentSkill[];
  agents: Agent[];
  agentSkills: AgentSkillMap;
  search: string;
  onSearch: (value: string) => void;
  groupFilter: string;
  onGroupFilter: (value: string) => void;
  onInstall: (source: string, force?: boolean) => Promise<boolean>;
  installPending: boolean;
  installError: string;
  onInstallExisting: (id: string, agentIds: string[]) => void;
  onApply: (skillIds: string[], agentIds: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [installOpen, setInstallOpen] = useState(false);
  const [installName, setInstallName] = useState('');
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [applyAgentIds, setApplyAgentIds] = useState<string[]>([]);
  const [visible, setVisible] = useState(INITIAL_VISIBLE);
  const query = search.trim().toLowerCase();

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

  return (
    <div className="skills-view">
      <header className="skills-view-top">
        <div><h1>{t('skillsView.title')}</h1><p>{t('skillsView.subtitle')}</p></div>
        <div className="skills-view-actions">
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
                  <article className={selected ? 'skv-card selected' : 'skv-card'} key={skill.skill_id} onClick={() => skill.installed && toggleSkill(skill.skill_id)}>
                    <div className="skv-card-head">
                      {skill.installed && <input type="checkbox" checked={selected} onChange={() => toggleSkill(skill.skill_id)} onClick={(event) => event.stopPropagation()} />}
                      <strong>{skill.name}</strong><span className={skill.installed ? 'skv-badge installed' : 'skv-badge'}>{skill.installed ? t('skillsView.installed') : t('skillsView.available')}</span>
                    </div>
                    <p className="skv-desc">{skill.description}</p>
                    <div className="skv-card-foot">
                      <span className="skv-path">{skill.path}</span>
                      {skill.installed ? usedBy > 0 && <span className="skv-used">{usedBy} agent{usedBy > 1 ? 's' : ''}</span> : <button className="skv-install-mini" disabled={agents.length === 0} onClick={(event) => { event.stopPropagation(); onInstallExisting(skill.skill_id, agents.map((agent) => agent.id)); }}><Plus size={13} />{t('common.install')}</button>}
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
          <div className="skv-apply-agents">{agents.map((agent) => <label key={agent.id} className={applyAgentIds.includes(agent.id) ? 'skv-agent-chip on' : 'skv-agent-chip'}><input type="checkbox" checked={applyAgentIds.includes(agent.id)} onChange={() => toggleAgent(agent.id)} />{agent.title}</label>)}</div>
          <button className="skv-apply-btn" disabled={applyAgentIds.length === 0} onClick={apply}><Check size={15} />{t('skillsView.applyToAgents', { count: applyAgentIds.length })}</button>
        </footer>
      )}
    </div>
  );
}
