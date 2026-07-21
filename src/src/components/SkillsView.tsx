import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Check, Clock, Download, Plus, Search, ShieldCheck, Star, Users, X } from 'lucide-react';
import { groupSkillsByCategory, skillCategoryOrder } from '../utils/skills';
import type {
  Agent,
  AgentSkill,
  AgentSkillMap,
  AsyncStatus,
  CommunitySkill,
  CommunityStats,
} from '../types';

type SkillsTab = 'installed' | 'community';

// Initial number of cards shown before "View more". Larger than the old page
// size so most catalogs fit on screen without any interaction.
const INITIAL_VISIBLE = 24;
const VIEW_MORE_STEP = 24;

const compact = new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 });

/** Coarse "x ago" formatter for catalog freshness; falls back to a date. */
function formatRelative(iso: string | undefined): string {
  if (!iso) return '';
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return '';
  const diffMs = Date.now() - then;
  const day = 86_400_000;
  const days = Math.floor(diffMs / day);
  if (days <= 0) return 'today';
  if (days === 1) return '1 day ago';
  if (days < 30) return `${days} days ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} mo ago`;
  const years = Math.floor(days / 365);
  return `${years} yr ago`;
}

type Stat = { label: string; value: string };

function StatsStrip({ stats }: { stats: Stat[] }) {
  return (
    <div className="skv-stats" role="group">
      {stats.map((s) => (
        <div className="skv-stat" key={s.label}>
          <span className="skv-stat-value">{s.value}</span>
          <span className="skv-stat-label">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

export function SkillsView({
  library,
  agents,
  agentSkills,
  community,
  communityStats,
  communityStatus,
  communityError,
  onRetryCommunity,
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
  community: CommunitySkill[];
  communityStats: CommunityStats;
  communityStatus: AsyncStatus;
  communityError: string;
  onRetryCommunity: () => void;
  search: string;
  onSearch: (v: string) => void;
  groupFilter: string;
  onGroupFilter: (v: string) => void;
  onInstall: (source: string) => Promise<boolean>;
  installPending: boolean;
  installError: string;
  onInstallExisting: (id: string, agentIds: string[]) => void;
  onApply: (skillIds: string[], agentIds: string[]) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<SkillsTab>('installed');
  const [installOpen, setInstallOpen] = useState(false);
  const [installName, setInstallName] = useState('');
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [applyAgentIds, setApplyAgentIds] = useState<string[]>([]);
  const [visible, setVisible] = useState(INITIAL_VISIBLE);
  const [installingSource, setInstallingSource] = useState('');

  const q = search.trim().toLowerCase();

  const matches = (fields: string[]) => !q || fields.some((f) => f.toLowerCase().includes(q));

  const filteredLibrary = useMemo(
    () =>
      library.filter((s) => {
        if (groupFilter !== 'all' && s.category !== groupFilter) return false;
        return matches([s.name, s.category, s.description]);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [library, groupFilter, q],
  );

  const filteredCommunity = useMemo(
    () =>
      community.filter((s) => {
        if (groupFilter !== 'all' && s.category !== groupFilter) return false;
        return matches([s.name, s.category, s.description, s.author, ...(s.tags ?? [])]);
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [community, groupFilter, q],
  );

  // Categories offered by the active tab's data set.
  const categories = useMemo(() => {
    const source: { category: string }[] = tab === 'installed' ? library : community;
    return ['all', ...skillCategoryOrder.filter((c) => source.some((s) => s.category === c))];
  }, [tab, library, community]);

  // Reset the view-more window whenever the visible result set changes.
  useEffect(() => {
    setVisible(INITIAL_VISIBLE);
  }, [tab, groupFilter, q]);

  const installedSkillIds = useMemo(() => new Set(library.map((s) => s.skill_id)), [library]);
  const installedNames = useMemo(
    () => new Set(library.filter((s) => s.installed).map((s) => s.name.toLowerCase())),
    [library],
  );

  // Installed-tab summary figures.
  const installedStats = useMemo<Stat[]>(() => {
    const installedCount = library.filter((s) => s.installed).length;
    const categoryCount = new Set(library.map((s) => s.category)).size;
    const inUse = agents.filter((a) => Object.values(agentSkills[a.id] ?? {}).some(Boolean)).length;
    return [
      { label: t('skillsView.statSkills', { defaultValue: 'Skills' }), value: String(library.length) },
      { label: t('skillsView.statInstalled', { defaultValue: 'Installed' }), value: String(installedCount) },
      { label: t('skillsView.statCategories', { defaultValue: 'Categories' }), value: String(categoryCount) },
      { label: t('skillsView.statInUse', { defaultValue: 'In use' }), value: `${inUse}/${agents.length}` },
    ];
  }, [library, agents, agentSkills, t]);

  const communityStatsStrip = useMemo<Stat[]>(
    () => [
      { label: t('skillsView.statSkills', { defaultValue: 'Skills' }), value: String(communityStats.totalSkills) },
      { label: t('skillsView.statAuthors', { defaultValue: 'Authors' }), value: String(communityStats.totalAuthors) },
      {
        label: t('skillsView.statInstalls', { defaultValue: 'Installs' }),
        value: compact.format(communityStats.totalInstalls),
      },
    ],
    [communityStats, t],
  );

  const toggleSelect = (id: string) =>
    setSelectedSkillIds((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));
  const toggleAgent = (id: string) =>
    setApplyAgentIds((prev) => (prev.includes(id) ? prev.filter((a) => a !== id) : [...prev, id]));

  const confirmInstall = async () => {
    if (!installName.trim() || installPending) return;
    if (await onInstall(installName.trim())) {
      setInstallName('');
      setInstallOpen(false);
    }
  };

  const installCommunity = async (skill: CommunitySkill) => {
    if (installPending) return;
    setInstallingSource(skill.source);
    try {
      await onInstall(skill.source);
    } finally {
      setInstallingSource('');
    }
  };

  const apply = () => {
    onApply(selectedSkillIds, applyAgentIds);
    setSelectedSkillIds([]);
    setApplyAgentIds([]);
  };

  const switchTab = (next: SkillsTab) => {
    if (next === tab) return;
    setTab(next);
    setSelectedSkillIds([]);
    setApplyAgentIds([]);
    if (groupFilter !== 'all') onGroupFilter('all');
  };

  const total = tab === 'installed' ? filteredLibrary.length : filteredCommunity.length;
  const shownLibrary = filteredLibrary.slice(0, visible);
  const shownCommunity = filteredCommunity.slice(0, visible);
  const libraryGroups = groupSkillsByCategory(shownLibrary);
  const communityLoading = tab === 'community' && communityStatus === 'loading';
  const communityFailed = tab === 'community' && communityStatus === 'error';
  const canViewMore = visible < total && !communityFailed;

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
              onKeyDown={(e) => e.key === 'Enter' && void confirmInstall()}
              autoFocus
            />
            <button className="skv-install-confirm" disabled={!installName.trim() || installPending} onClick={() => void confirmInstall()}>
              <Check size={15} />
              {installPending ? t('common.loading', { defaultValue: 'Installing…' }) : t('common.install')}
            </button>
          </div>
          <p className="skv-install-hint">{t('skillsView.installHint')}</p>
          {installError && <p className="skv-install-error" role="alert">{installError}</p>}
        </div>
      )}

      <div className="skv-tabs" role="tablist">
        <button
          role="tab"
          aria-selected={tab === 'installed'}
          className={tab === 'installed' ? 'skv-tab active' : 'skv-tab'}
          onClick={() => switchTab('installed')}
        >
          <Check size={15} />
          {t('skillsView.tabInstalled')}
          <em>{library.length}</em>
        </button>
        <button
          role="tab"
          aria-selected={tab === 'community'}
          className={tab === 'community' ? 'skv-tab active' : 'skv-tab'}
          onClick={() => switchTab('community')}
        >
          <Users size={15} />
          {t('skillsView.tabCommunity')}
          <em>{communityStats.totalSkills || community.length}</em>
        </button>
      </div>

      <StatsStrip stats={tab === 'installed' ? installedStats : communityStatsStrip} />

      <div className="skills-view-controls">
        <div className="skv-search">
          <Search size={15} />
          <input
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder={t('skillsView.searchPlaceholder')}
          />
        </div>
        <div className="skv-filters">
          {categories.map((cat) => (
            <button
              key={cat}
              className={groupFilter === cat ? 'skv-chip active' : 'skv-chip'}
              onClick={() => onGroupFilter(cat)}
            >
              {cat === 'all' ? t('skillsView.all') : cat}
            </button>
          ))}
        </div>
      </div>

      <div className="skills-view-scroll">
        {tab === 'installed' &&
          libraryGroups.map(([category, skills]) => (
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
                            disabled={agents.length === 0}
                            onClick={(e) => {
                              e.stopPropagation();
                              onInstallExisting(skill.skill_id, agents.map((agent) => agent.id));
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

        {tab === 'community' && (
          <>
            {communityLoading && (
              <div className="skv-grid skv-grid-community" aria-hidden>
                {Array.from({ length: 6 }).map((_, i) => (
                  <div className="skv-card skv-card-skeleton" key={i}>
                    <span className="skv-skel skv-skel-title" />
                    <span className="skv-skel skv-skel-line" />
                    <span className="skv-skel skv-skel-line short" />
                    <span className="skv-skel skv-skel-foot" />
                  </div>
                ))}
              </div>
            )}

            {communityFailed && (
              <div className="skv-empty skv-error-block" role="alert">
                <p>{communityError || t('skillsView.communityError', { defaultValue: 'Unable to load community skills.' })}</p>
                <button className="skv-retry" onClick={onRetryCommunity}>
                  {t('common.retry', { defaultValue: 'Retry' })}
                </button>
              </div>
            )}

            {!communityLoading && !communityFailed && (
              <div className="skv-grid skv-grid-community">
                {shownCommunity.map((skill) => {
                  const alreadyInstalled =
                    installedSkillIds.has(skill.skill_id) || installedNames.has(skill.name.toLowerCase());
                  const busy = installingSource === skill.source;
                  const updated = formatRelative(skill.updatedAt);
                  return (
                    <article className="skv-card skv-card-community" key={skill.skill_id}>
                      <div className="skv-card-head">
                        <span className={`cat-dot ${skill.category}`} />
                        <strong>{skill.name}</strong>
                        {skill.verified && (
                          <span className="skv-verified" title={t('skillsView.verified', { defaultValue: 'Verified' })}>
                            <ShieldCheck size={13} />
                          </span>
                        )}
                        {skill.version && <span className="skv-version">v{skill.version}</span>}
                      </div>
                      <p className="skv-desc">{skill.description}</p>
                      {skill.tags && skill.tags.length > 0 && (
                        <div className="skv-tags">
                          {skill.tags.slice(0, 4).map((tag) => (
                            <span className="skv-tag" key={tag}>{tag}</span>
                          ))}
                        </div>
                      )}
                      <div className="skv-community-meta">
                        <span className="skv-author">@{skill.author}</span>
                        {skill.rating != null && (
                          <span className="skv-rating" title={t('skillsView.ratingTitle', { defaultValue: '{{count}} ratings', count: skill.ratingCount ?? 0 })}>
                            <Star size={12} fill="currentColor" />
                            {skill.rating.toFixed(1)}
                          </span>
                        )}
                        <span className="skv-installs">
                          <Download size={12} />
                          {compact.format(skill.installs)}
                        </span>
                        {updated && (
                          <span className="skv-updated">
                            <Clock size={12} />
                            {updated}
                          </span>
                        )}
                      </div>
                      <div className="skv-card-foot">
                        <span className="skv-path">{skill.source}</span>
                        {alreadyInstalled ? (
                          <span className="skv-badge installed">{t('skillsView.installed')}</span>
                        ) : (
                          <button
                            className="skv-install-mini primary"
                            disabled={installPending}
                            onClick={() => void installCommunity(skill)}
                          >
                            <Download size={13} />
                            {busy ? t('common.loading', { defaultValue: 'Installing…' }) : t('common.install')}
                          </button>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
            {tab === 'community' && !communityFailed && installError && (
              <p className="skv-install-error" role="alert">{installError}</p>
            )}
          </>
        )}

        {!communityLoading && !communityFailed && total === 0 && (
          <div className="skv-empty">
            {tab === 'community' ? t('skillsView.communityEmpty') : t('skillsView.empty')}
          </div>
        )}

        {canViewMore && (
          <div className="skv-view-more">
            <button onClick={() => setVisible((v) => v + VIEW_MORE_STEP)}>
              {t('skillsView.viewMore')}
              <em>{total - visible}</em>
            </button>
          </div>
        )}
      </div>

      {tab === 'installed' && selectedSkillIds.length > 0 && (
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
