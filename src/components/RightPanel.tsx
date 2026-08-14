import { useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Check,
  Clock3,
  Code2,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  Wrench,
  X,
  ArrowUpDown,
} from 'lucide-react';
import { WorkspacePanel, type WorkspaceController } from './WorkspacePanel';
import { CronPanel } from './CronPanel';
import type { ResponsePagination } from '../api/client';
import type { Agent, AgentSkill, AgentSkillMap, CronJob, GlobalRuntimeConfig, RightView } from '../types';

const AGENT_ACTIONS = [
  { id: 'create', label: 'Create' },
  { id: 'settings', label: 'Agent settings' },
  { id: 'delete', label: 'Delete' },
] as const;
type WriteApprovalPatch = Partial<Pick<GlobalRuntimeConfig, 'skillsWriteApproval' | 'memoryWriteApproval'>>;

export function RightPanel({
  open,
  onOpen,
  onClose,
  rightView,
  onRightView,
  agent,
  library,
  agentSkills,
  defaultConfig,
  onToggleSkill,
  onSetSkillEnabled,
  onUpdateWriteApprovals,
  skillsPagination,
  onLoadSkillsPage,
  crons,
  cronStatus,
  cronError,
  cronPendingId,
  onCreateCron,
  onToggleCron,
  onRunCron,
  onDeleteCron,
  onCreateAgent,
  onOpenSettings,
  onDeleteAgent,
  workspaceOpenRequest,
  workspace,
  width,
  onResize,
}: {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  rightView: RightView;
  onRightView: (v: RightView) => void;
  agent: Agent;
  library: AgentSkill[];
  agentSkills: AgentSkillMap;
  defaultConfig: GlobalRuntimeConfig | null;
  onToggleSkill: (skillId: string) => void;
  onSetSkillEnabled: (skillId: string, enabled: boolean) => void;
  onUpdateWriteApprovals: (updates: WriteApprovalPatch) => Promise<void>;
  skillsPagination?: ResponsePagination;
  onLoadSkillsPage: (page: number) => void;
  crons: CronJob[];
  cronStatus: 'idle' | 'loading' | 'ready' | 'error';
  cronError: string;
  cronPendingId: string;
  onCreateCron: (input: { name: string; prompt: string; intervalMinutes: number }) => Promise<void>;
  onToggleCron: (id: string) => Promise<void>;
  onRunCron: (id: string) => Promise<void>;
  onDeleteCron: (id: string) => Promise<void>;
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

  const [approvalSaving, setApprovalSaving] = useState<'skills' | 'memory' | null>(null);
  const [approvalError, setApprovalError] = useState('');

  const enabledMap = agentSkills[agent.id] ?? {};
  const approvalsAvailable = defaultConfig !== null;


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
    <aside className={open ? 'right-panel open' : 'right-panel collapsed'}>
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
        <button className="icon-button" title={t('common.close')} onClick={onClose}>
          <X size={17} />
        </button>
      </div>

      <div className="right-tabs">
        {(['workspace', 'skills', 'cron', 'runtime'] as const).map((tab) => (
          <button
            key={tab}
            className={rightView === tab ? 'active' : ''}
            title={t(`controls.${tab}`)}
            aria-label={t(`controls.${tab}`)}
            onClick={() => {
              onRightView(tab);
              onOpen();
            }}
          >
            {tab === 'workspace'
              ? <Code2 size={17} />
              : tab === 'skills'
                ? <Sparkles size={17} />
                : tab === 'cron'
                  ? <Clock3 size={17} />
                  : <Wrench size={17} />}
            <span>{t(`controls.${tab}`, { defaultValue: tab })}</span>
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
                          <span className="skill-item-description">{skill.description || t('agentSkills.noDescription', { defaultValue: 'No description available.' })}</span>
                          <span className="skill-tooltip" role="tooltip">{skill.description || skill.name}</span>
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
        <CronPanel
          crons={crons}
          status={cronStatus}
          error={cronError}
          pendingId={cronPendingId}
          onCreate={onCreateCron}
          onToggle={onToggleCron}
          onRun={onRunCron}
          onDelete={onDeleteCron}
        />
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
                checked={defaultConfig?.skillsWriteApproval ?? true}
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
                checked={defaultConfig?.memoryWriteApproval ?? true}
                disabled={!approvalsAvailable || approvalSaving !== null}
                onChange={(e) => void updateWriteApproval('memory', e.target.checked)}
              />
              <span className="runtime-switch" />
            </label>
            {approvalError && <p className="runtime-approval-error">{approvalError}</p>}
          </div>
          <div className="button-grid">
            {AGENT_ACTIONS.map((action) => (
              <button
                key={action.id}
                onClick={() => {
                  if (action.id === 'create') onCreateAgent();
                  else if (action.id === 'settings') onOpenSettings();
                  else if (action.id === 'delete') onDeleteAgent();
                }}
              >
                <Wrench size={15} />
                {action.label}
              </button>
            ))}
          </div>
        </section>
      )}

      <div className="api-note">
        <Code2 size={15} />
        <span>Mapped from agents, providers, sessions, workspace files, and sandbox APIs.</span>
      </div>
    </aside>
  );
}
