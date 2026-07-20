import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Plus, Search, X } from 'lucide-react';
import { groupSkillsByCategory, skillCategoryOrder } from '../utils/skills';
import type { Agent, AgentSkill, AgentSkillMap } from '../types';

export function SkillsView({
  library,
  agents,
  agentSkills,
  search,
  onSearch,
  groupFilter,
  onGroupFilter,
  page,
  onPage,
  onInstall,
  onInstallExisting,
  onApply,
  onClose,
}: {
  library: AgentSkill[];
  agents: Agent[];
  agentSkills: AgentSkillMap;
  search: string;
  onSearch: (v: string) => void;
  groupFilter: string;
  onGroupFilter: (v: string) => void;
  page: number;
  onPage: (v: number) => void;
  onInstall: (name: string, agentIds: string[]) => void;
  onInstallExisting: (id: string, agentIds: string[]) => void;
  onApply: (skillIds: string[], agentIds: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [installOpen, setInstallOpen] = useState(false);
  const [installName, setInstallName] = useState('');
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [applyAgentIds, setApplyAgentIds] = useState<string[]>([]);
  const [targetAgentIds, setTargetAgentIds] = useState<string[]>(() => agents[0] ? [agents[0].id] : []);

  const pageSize = 8;
  const categories = ['all', ...skillCategoryOrder.filter((c) => library.some((s) => s.category === c))];

  const q = search.trim().toLowerCase();
  const filtered = library.filter((s) => {
    if (groupFilter !== 'all' && s.category !== groupFilter) return false;
    if (!q) return true;
    return s.name.toLowerCase().includes(q) || s.category.toLowerCase().includes(q) || s.description.toLowerCase().includes(q);
  });

  const pageCount = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(page, pageCount - 1);
  const pageItems = filtered.slice(current * pageSize, current * pageSize + pageSize);
  const groups = groupSkillsByCategory(pageItems);

  const toggleSelect = (id: string) =>
    setSelectedSkillIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
  const toggleAgent = (id: string) =>
    setApplyAgentIds((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));

  const confirmInstall = () => {
    onInstall(installName.trim(), targetAgentIds);
    setInstallName('');
    setInstallOpen(false);
  };

  const apply = () => {
    onApply(selectedSkillIds, applyAgentIds);
    setSelectedSkillIds([]);
    setApplyAgentIds([]);
  };

  return (
    <div className="skills-view">
      <header className="skills-view-top">
        <div>
          <h1>{t('skillsView.title')}</h1>
          <p>{t('skillsView.subtitle')}</p>
        </div>
        <div className="skills-view-actions">
          <button className={installOpen ? 'skv-install-btn open' : 'skv-install-btn'} onClick={() => setInstallOpen((v) => !v)}>
            <Plus size={15} />
            {t('skillsView.installNew')}
          </button>
          <button className="icon-button" title={t('common.close')} onClick={onClose}>
            <X size={17} />
          </button>
        </div>
      </header>

      {installOpen && (
        <div className="skv-install-panel">
          <div className="skv-install-row">
            <input
              value={installName}
              onChange={(e) => setInstallName(e.target.value)}
              placeholder={t('skillsView.installPlaceholder')}
              onKeyDown={(e) => e.key === 'Enter' && confirmInstall()}
              autoFocus
            />
            <button className="skv-install-confirm" disabled={!installName.trim() || targetAgentIds.length === 0} onClick={confirmInstall}>
              <Check size={15} />
              {t('common.install')}
            </button>
          </div>
          <div className="skv-targets">
            <span>Install for:</span>
            {agents.map((agent) => (
              <label key={agent.id} className={targetAgentIds.includes(agent.id) ? 'skv-agent-chip on' : 'skv-agent-chip'}>
                <input type="checkbox" checked={targetAgentIds.includes(agent.id)} onChange={() => setTargetAgentIds((ids) => ids.includes(agent.id) ? ids.filter((id) => id !== agent.id) : [...ids, agent.id])} />
                {agent.title}
              </label>
            ))}
          </div>
        </div>
      )}

      <div className="skills-view-controls">
        <div className="skv-search">
          <Search size={15} />
          <input
            value={search}
            onChange={(e) => {
              onSearch(e.target.value);
              onPage(0);
            }}
            placeholder={t('skillsView.searchPlaceholder')}
          />
        </div>
        <div className="skv-filters">
          {categories.map((cat) => (
            <button
              key={cat}
              className={groupFilter === cat ? 'skv-chip active' : 'skv-chip'}
              onClick={() => {
                onGroupFilter(cat);
                onPage(0);
              }}
            >
              {cat === 'all' ? t('skillsView.all') : cat}
            </button>
          ))}
        </div>
      </div>

      <div className="skills-view-scroll">
        {groups.map(([category, skills]) => (
          <div className="skv-group" key={category}>
            <div className="skv-group-title">
              <span className={`cat-dot ${category}`} />
              {category}
              <em>{skills.length}</em>
            </div>
            <div className="skv-grid">
              {skills.map((skill) => {
                const selected = selectedSkillIds.includes(skill.skill_id);
                const usedBy = agents.filter((a) => agentSkills[a.id]?.[skill.skill_id]).length;
                return (
                  <article
                    className={selected ? 'skv-card selected' : 'skv-card'}
                    key={skill.skill_id}
                    onClick={() => skill.installed && toggleSelect(skill.skill_id)}
                  >
                    <div className="skv-card-head">
                      {skill.installed && (
                        <input type="checkbox" checked={selected} onChange={() => toggleSelect(skill.skill_id)} onClick={(e) => e.stopPropagation()} />
                      )}
                      <strong>{skill.name}</strong>
                      <span className={skill.installed ? 'skv-badge installed' : 'skv-badge'}>
                        {skill.installed ? t('skillsView.installed') : t('skillsView.available')}
                      </span>
                    </div>
                    <p className="skv-desc">{skill.description}</p>
                    <div className="skv-card-foot">
                      <span className="skv-path">{skill.path}</span>
                      {skill.installed ? (
                        usedBy > 0 && <span className="skv-used">{usedBy} agent{usedBy > 1 ? 's' : ''}</span>
                      ) : (
                        <button
                          className="skv-install-mini"
                          disabled={targetAgentIds.length === 0}
                          onClick={(e) => {
                            e.stopPropagation();
                            onInstallExisting(skill.skill_id, targetAgentIds);
                          }}
                        >
                          <Plus size={13} />
                          {t('common.install')}
                        </button>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        ))}

        {filtered.length === 0 && <div className="skv-empty">{t('skillsView.empty')}</div>}

        {pageCount > 1 && (
          <div className="skill-pager skv-pager">
            <button disabled={current === 0} onClick={() => onPage(current - 1)}>‹</button>
            {Array.from({ length: pageCount }).map((_, i) => (
              <button key={i} className={i === current ? 'active' : ''} onClick={() => onPage(i)}>
                {i + 1}
              </button>
            ))}
            <button disabled={current === pageCount - 1} onClick={() => onPage(current + 1)}>›</button>
          </div>
        )}
      </div>

      {selectedSkillIds.length > 0 && (
        <footer className="skv-apply-bar">
          <span className="skv-apply-count">{t('skillsView.selected', { count: selectedSkillIds.length })}</span>
          <div className="skv-apply-agents">
            {agents.map((agent) => (
              <label key={agent.id} className={applyAgentIds.includes(agent.id) ? 'skv-agent-chip on' : 'skv-agent-chip'}>
                <input type="checkbox" checked={applyAgentIds.includes(agent.id)} onChange={() => toggleAgent(agent.id)} />
                {agent.title}
              </label>
            ))}
          </div>
          <button className="skv-apply-btn" disabled={applyAgentIds.length === 0} onClick={apply}>
            <Check size={15} />
            {t('skillsView.applyToAgents', { count: applyAgentIds.length })}
          </button>
        </footer>
      )}
    </div>
  );
}
