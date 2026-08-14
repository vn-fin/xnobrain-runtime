import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BarChart3,
  BookOpen,
  ChevronDown,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Columns3,
  Download,
  Languages,
  LogIn,
  LogOut,
  Monitor,
  Moon,
  MoreHorizontal,
  Network,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Puzzle,
  Search,
  Settings,
  SlidersHorizontal,
  Sun,
  Trash2,
  X,
} from 'lucide-react';
import type { ActiveUser } from '../auth';
import { productName } from '../config/product';
import type { XNOBrainEdition } from '../runtime';
import { SUPPORTED_LANGUAGES } from '../i18n';
import { useTheme } from '../theme';
import { useDismissibleLayer } from '../hooks/useDismissibleLayer';
import type { Agent, CenterView } from '../types';

const PINNED_KEY = 'xnobrain.pinnedAssistants';
const ALWAYS_PINNED_AGENT_ID = 'big-brother';

const navItems = [
  { id: 'skills', label: 'skills', icon: Puzzle },
  { id: 'kanban', label: 'kanban', icon: Columns3 },
  { id: 'cron', label: 'cron', icon: Clock3 },
  { id: 'teams', label: 'teams', icon: Network },
  { id: 'analytics', label: 'analytics', icon: BarChart3 },
  { id: 'data', label: 'settings', icon: Settings },
] as const;

export function MobileManageDrawer({
  open,
  centerView,
  onOpen,
  onClose,
  onNavigate,
  edition,
  loginEnabled,
  sessionActive,
  onOpenLogin,
  onOpenAccount,
}: {
  open: boolean;
  centerView: CenterView;
  onOpen: () => void;
  onClose: () => void;
  onNavigate: (view: CenterView) => void;
  edition?: XNOBrainEdition;
  loginEnabled?: boolean;
  sessionActive?: boolean;
  onOpenLogin?: () => void;
  onOpenAccount?: () => void;
}) {
  const { t } = useTranslation();
  const gestureStart = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (open && event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [onClose, open]);

  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (open || event.clientX > 28 || !window.matchMedia('(max-width: 760px)').matches) return;
      gestureStart.current = { x: event.clientX, y: event.clientY };
    };
    const onPointerMove = (event: PointerEvent) => {
      const start = gestureStart.current;
      if (!start) return;
      const x = event.clientX - start.x;
      const y = Math.abs(event.clientY - start.y);
      if (x > 64 && x > y * 1.4) {
        gestureStart.current = null;
        onOpen();
      } else if (x < -8 || y > 56) {
        gestureStart.current = null;
      }
    };
    const resetGesture = () => { gestureStart.current = null; };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', resetGesture);
    document.addEventListener('pointercancel', resetGesture);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', resetGesture);
      document.removeEventListener('pointercancel', resetGesture);
    };
  }, [onOpen, open]);

  if (!open) return null;

  const navigate = (view: CenterView) => {
    onNavigate(view);
    onClose();
  };

  return (
    <div className="mobile-manage-layer">
      <button className="mobile-manage-backdrop" aria-label={t('common.close')} onClick={onClose} />
      <aside className="mobile-manage-drawer" role="dialog" aria-modal="true" aria-label={t('nav.manage', { defaultValue: 'Manage' })}>
        <header className="mobile-manage-header">
          <strong>{productName}</strong>
          <button className="icon-button" aria-label={t('common.close')} onClick={onClose}><X size={22} /></button>
        </header>
        <div className="mobile-manage-title">{t('nav.manage', { defaultValue: 'Manage' })}</div>
        <nav className="mobile-manage-nav">
          {loginEnabled && (
            <button onClick={() => { onClose(); sessionActive ? onOpenAccount?.() : onOpenLogin?.(); }}>
              <CircleUserRound size={22} />
              <span>Account</span>
              {sessionActive && <small className={`sidebar-edition ${edition ?? 'opensource'}`}>{edition ?? 'opensource'}</small>}
            </button>
          )}
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button key={item.id} className={item.id === centerView ? 'active' : ''} onClick={() => navigate(item.id as CenterView)}>
                <Icon size={22} />
                <span>{t(`nav.${item.label}`, { defaultValue: item.label[0].toUpperCase() + item.label.slice(1) })}</span>
              </button>
            );
          })}
        </nav>
      </aside>
    </div>
  );
}

function readIds(key: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '[]');
    const stored = Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
    return [ALWAYS_PINNED_AGENT_ID, ...stored.filter((id) => id !== ALWAYS_PINNED_AGENT_ID)];
  } catch {
    return [ALWAYS_PINNED_AGENT_ID];
  }
}

export function Sidebar({
  agents,
  activeAgent,
  centerView,
  agentSearch,
  onAgentSearch,
  onNavigate,
  onSelectAgent,
  onRenameAgent,
  onExportAgent,
  onRequestDeleteAgent,
  onNewAgent,
  user,
  edition,
  loginEnabled,
  sessionActive,
  onOpenLogin,
  onOpenAccount,
  onSignOut,
  collapsed = false,
  onToggleCollapsed,
}: {
  agents: Agent[];
  activeAgent: Agent;
  centerView: CenterView;
  agentSearch: string;
  onAgentSearch: (v: string) => void;
  onNavigate: (view: CenterView) => void;
  onSelectAgent: (agent: Agent) => void;
  onRenameAgent: (agentId: string, displayName: string) => void | Promise<void>;
  onExportAgent: (agentId: string) => Promise<void>;
  onRequestDeleteAgent: (agentId: string) => void;
  onNewAgent: () => void;
  user?: ActiveUser | null;
  edition?: XNOBrainEdition;
  loginEnabled?: boolean;
  sessionActive?: boolean;
  onOpenLogin?: () => void;
  onOpenAccount?: () => void;
  onSignOut?: () => Promise<void>;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { preference: themePref, setPreference: setThemePref } = useTheme();
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [preferencesOpen, setPreferencesOpen] = useState(false);
  const preferencesRoot = useDismissibleLayer<HTMLDivElement>(preferencesOpen, () => setPreferencesOpen(false));
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [librarySearch, setLibrarySearch] = useState('');
  const [agentMenuId, setAgentMenuId] = useState<string | null>(null);
  const [exportingAgentId, setExportingAgentId] = useState<string | null>(null);
  const [agentActionError, setAgentActionError] = useState('');
  const [renamingAgentId, setRenamingAgentId] = useState<string | null>(null);
  const [agentRenameValue, setAgentRenameValue] = useState('');
  const [pinnedIds, setPinnedIds] = useState(() => readIds(PINNED_KEY));
  const agentSearchEngaged = useRef(false);

  useEffect(() => {
    localStorage.setItem(PINNED_KEY, JSON.stringify(pinnedIds));
  }, [pinnedIds]);

  useEffect(() => {
    if (!agentMenuId) return undefined;
    const closeOutside = (event: PointerEvent) => {
      const row = event.target instanceof Element ? event.target.closest('[data-agent-row-id]') : null;
      if (row?.getAttribute('data-agent-row-id') !== agentMenuId) setAgentMenuId(null);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setAgentMenuId(null);
    };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [agentMenuId]);

  const q = agentSearch.trim().toLowerCase();
  const matches = (agent: Agent, query: string) => !query
    || agent.title.toLowerCase().includes(query)
    || agent.name.toLowerCase().includes(query)
    || agent.description.toLowerCase().includes(query)
    || agent.model.toLowerCase().includes(query);
  const orderedAgents = useMemo(
    () => agents.map((agent, index) => ({ agent, index }))
      .sort((left, right) => (
        Number(right.agent.id === ALWAYS_PINNED_AGENT_ID) - Number(left.agent.id === ALWAYS_PINNED_AGENT_ID)
        || Number(pinnedIds.includes(right.agent.id)) - Number(pinnedIds.includes(left.agent.id))
        || left.index - right.index
      ))
      .map(({ agent }) => agent),
    [agents, pinnedIds],
  );
  const focusedAgents = q ? orderedAgents.filter((agent) => matches(agent, q)) : orderedAgents;
  const libraryAgents = useMemo(() => {
    const query = librarySearch.trim().toLowerCase();
    return orderedAgents.filter((agent) => matches(agent, query));
  }, [orderedAgents, librarySearch]);

  const startAgentRename = (agent: Agent) => {
    setAgentMenuId(null);
    setRenamingAgentId(agent.id);
    setAgentRenameValue(agent.title);
  };

  const commitAgentRename = (agent: Agent) => {
    const value = agentRenameValue.trim();
    if (value && value !== agent.title) void onRenameAgent(agent.id, value);
    setRenamingAgentId(null);
  };

  const exportAgent = async (agent: Agent) => {
    setExportingAgentId(agent.id);
    setAgentActionError('');
    try {
      await onExportAgent(agent.id);
      setAgentMenuId(null);
    } catch (cause) {
      setAgentActionError(cause instanceof Error ? cause.message : 'Could not export profile.');
    } finally {
      setExportingAgentId(null);
    }
  };

  const togglePinned = (agentId: string) => {
    if (agentId === ALWAYS_PINNED_AGENT_ID) {
      setAgentMenuId(null);
      return;
    }
    setPinnedIds((ids) => ids.includes(agentId) ? ids.filter((id) => id !== agentId) : [...ids, agentId]);
    setAgentMenuId(null);
  };

  const selectAgent = (agent: Agent) => {
    setLibraryOpen(false);
    setLibrarySearch('');
    onSelectAgent(agent);
  };

  const renderAgent = (agent: Agent, library = false) => {
    const active = agent.id === activeAgent.id && centerView === 'chat';
    const running = agent.runtimeStatus === 'running';
    const activityLabel = running
      ? t('agents.running', { defaultValue: 'Running' })
      : t('agents.idle', { defaultValue: 'Idle' });
    const alwaysPinned = agent.id === ALWAYS_PINNED_AGENT_ID;
    const pinned = alwaysPinned || pinnedIds.includes(agent.id);
    if (renamingAgentId === agent.id && !library) {
      return (
        <div key={agent.id} className={active ? 'agent-row active renaming' : 'agent-row renaming'}>
          <input
            className="history-rename-input"
            value={agentRenameValue}
            autoFocus
            aria-label={t('agents.renameLabel')}
            onFocus={(event) => event.currentTarget.select()}
            onChange={(event) => setAgentRenameValue(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') { event.preventDefault(); commitAgentRename(agent); }
              if (event.key === 'Escape') { event.preventDefault(); setRenamingAgentId(null); }
            }}
            onBlur={() => commitAgentRename(agent)}
          />
        </div>
      );
    }
    return (
      <div data-agent-row-id={agent.id} key={agent.id} className={`${active ? 'agent-row active' : 'agent-row'}${library ? ' library-agent-row' : ''}`}>
        <button className="agent-row-main" onClick={() => selectAgent(agent)} onDoubleClick={() => !library && startAgentRename(agent)} title={agent.title}>
          <span
            className={`status-dot ${running ? 'running' : 'idle'}`}
            role="status"
            aria-label={`${agent.title}: ${activityLabel}`}
            title={activityLabel}
          />
          <span>
            <strong>{agent.title}</strong>
            <small>{agent.description || t('agents.noDescription', { defaultValue: 'No description' })}</small>
          </span>
        </button>
        {!library && (
          <>
            {pinned && <Pin className="agent-pin" size={12} aria-label={t('agents.pinned')} />}
            <button
              className="agent-row-more"
              title={t('agents.menu', { defaultValue: 'Agent options' })}
              aria-label={t('agents.menu', { defaultValue: 'Agent options' })}
              aria-expanded={agentMenuId === agent.id}
              onClick={(event) => {
                event.stopPropagation();
                setAgentActionError('');
                setAgentMenuId((id) => id === agent.id ? null : agent.id);
              }}
            >
              <MoreHorizontal size={16} />
            </button>
            {agentMenuId === agent.id && (
              <div className="row-menu" role="menu">
                <button
                  className="row-menu-item"
                  role="menuitem"
                  disabled={alwaysPinned}
                  onClick={() => togglePinned(agent.id)}
                >
                  {pinned && !alwaysPinned ? <PinOff size={14} /> : <Pin size={14} />}
                  {t(alwaysPinned ? 'agents.pinned' : pinned ? 'agents.unpin' : 'agents.pin')}
                </button>
                <button className="row-menu-item" role="menuitem" disabled={exportingAgentId === agent.id} onClick={() => startAgentRename(agent)}>
                  <Pencil size={14} /> {t('agents.renameAction', { defaultValue: 'Rename' })}
                </button>
                <button className="row-menu-item" role="menuitem" disabled={exportingAgentId === agent.id} onClick={() => void exportAgent(agent)}>
                  <Download size={14} /> {exportingAgentId === agent.id ? 'Exporting…' : t('agents.exportAction', { defaultValue: 'Export' })}
                </button>
                <button className="row-menu-item danger" role="menuitem" onClick={() => { setAgentMenuId(null); onRequestDeleteAgent(agent.id); }}>
                  <Trash2 size={14} /> {t('common.delete')}
                </button>
              </div>
            )}
          </>
        )}
      </div>
    );
  };

  return (
    <aside className={collapsed ? 'left-panel collapsed' : 'left-panel'}>
      <div className="brand">
        <span>{productName}</span>
        <button
          className="icon-button"
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          aria-expanded={!collapsed}
          onClick={onToggleCollapsed}
        >
          {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
        </button>
      </div>

      <div className="history-section assistant-launcher">
        <div className="section-title agents-title">
          <span>{t('agents.title')}</span>
          <button className="icon-button" title={t('agents.new')} onClick={onNewAgent}><Plus size={16} /></button>
        </div>
        <div className="agent-search">
          <Search size={14} />
          <input
            value={agentSearch}
            onFocus={() => { agentSearchEngaged.current = true; }}
            onChange={(event) => {
              if (agentSearchEngaged.current) onAgentSearch(event.target.value);
            }}
            placeholder={t('agents.searchPlaceholder')}
            type="search"
            name="agent-filter-query"
            autoComplete="off"
          />
        </div>
        {q && focusedAgents.length > 0 && <div className="assistant-list-label">{t('agents.searchResults')}</div>}
        {focusedAgents.map((agent) => renderAgent(agent))}
        {focusedAgents.length === 0 && <div className="assistant-list-empty">{t('agents.noMatch')}</div>}
        {agentActionError && <div className="agent-action-error" role="alert">{agentActionError}</div>}
      </div>

      <div className="library-nav">
        <button className="open-library-button" onClick={() => setLibraryOpen(true)}>
          <BookOpen size={15} /> {t('agents.openLibrary')}
        </button>
      </div>

      <div className="workspace-nav">
        <div className="workspace-nav-heading">
          <button className="workspace-nav-toggle" aria-expanded={workspaceOpen} onClick={() => setWorkspaceOpen((open) => !open)}>
            {workspaceOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
            <span>{t('nav.manage', { defaultValue: 'Manage' })}</span>
          </button>
        </div>
        {workspaceOpen && (
          <div className="main-nav">
            {loginEnabled && (
              <button className="nav-row" onClick={sessionActive ? onOpenAccount : onOpenLogin}>
                <CircleUserRound size={18} />
                <span>Account</span>
                {sessionActive && <small className={`sidebar-edition ${edition ?? 'opensource'}`}>{edition ?? 'opensource'}</small>}
              </button>
            )}
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.id} className={item.id === centerView ? 'nav-row active' : 'nav-row'} onClick={() => onNavigate(item.id as CenterView)}>
                  <Icon size={18} />
                  <span>{t(`nav.${item.label}`, { defaultValue: item.label[0].toUpperCase() + item.label.slice(1) })}</span>
                </button>
              );
            })}
          </div>
        )}
      </div>

      <div className="left-footer">
        <div className="preferences-menu" ref={preferencesRoot}>
          {preferencesOpen && (
            <section className="preferences-popover" role="region" aria-label="Preferences">
              <div className="preferences-row appearance-row">
                <span className="preferences-label"><SlidersHorizontal size={17} /> Appearance</span>
                <div className="theme-switcher" role="group" aria-label={t('theme.label')}>
                  <button className={themePref === 'light' ? 'active' : ''} title={t('theme.light')} onClick={() => setThemePref('light')}><Sun size={16} /></button>
                  <button className={themePref === 'dark' ? 'active' : ''} title={t('theme.dark')} onClick={() => setThemePref('dark')}><Moon size={16} /></button>
                  <button className={themePref === 'auto' ? 'active' : ''} title={t('theme.auto')} onClick={() => setThemePref('auto')}><Monitor size={16} /></button>
                </div>
              </div>
              <label className="preferences-row language-row">
                <span className="preferences-label"><Languages size={17} /> {t('language.label')}</span>
                <span className="preferences-select">
                  <select value={i18n.resolvedLanguage} onChange={(event) => i18n.changeLanguage(event.target.value)} aria-label={t('language.label')}>
                    {SUPPORTED_LANGUAGES.map((language) => <option key={language} value={language}>{t(`language.${language}`)}</option>)}
                  </select>
                  <ChevronDown size={15} />
                </span>
              </label>
              {loginEnabled && (
                <button
                  className={sessionActive ? 'preferences-auth danger' : 'preferences-auth'}
                  aria-label={sessionActive ? 'Sign out' : 'Sign in'}
                  onClick={() => {
                    setPreferencesOpen(false);
                    if (sessionActive) void onSignOut?.();
                    else onOpenLogin?.();
                  }}
                >
                  {sessionActive ? <LogOut size={17} /> : <LogIn size={17} />}
                  {sessionActive ? 'Log out' : 'Sign in'}
                </button>
              )}
            </section>
          )}
          <button
            className="preferences-trigger"
            aria-label={sessionActive && user ? `Open preferences for ${user.displayName || user.email}` : 'Open preferences'}
            aria-expanded={preferencesOpen}
            onClick={() => setPreferencesOpen((open) => !open)}
          >
            <span className="user-avatar">
              {sessionActive && user
                ? (user.displayName || user.email).charAt(0).toUpperCase()
                : <SlidersHorizontal size={15} />}
            </span>
            <span className="preferences-trigger-copy">
              <strong>{sessionActive && user ? user.displayName || user.email : 'Preferences'}</strong>
              {sessionActive && user && <small>{user.email}</small>}
              {!sessionActive && <small>Appearance and language</small>}
            </span>
            <ChevronDown size={15} className={preferencesOpen ? 'open' : ''} />
          </button>
        </div>
      </div>

      {agentMenuId && <div className="row-menu-catcher" onClick={() => setAgentMenuId(null)} />}
      {libraryOpen && (
        <div className="assistant-library-overlay" onClick={() => setLibraryOpen(false)}>
          <section className="assistant-library" role="dialog" aria-modal="true" aria-label={t('agents.libraryTitle')} onClick={(event) => event.stopPropagation()}>
            <header>
              <div><strong>{t('agents.libraryTitle')}</strong><small>{t('agents.libraryCount', { count: agents.length })}</small></div>
              <button className="icon-button" title={t('common.close')} onClick={() => setLibraryOpen(false)}><X size={17} /></button>
            </header>
            <div className="agent-search library-search">
              <Search size={15} />
              <input value={librarySearch} onChange={(event) => setLibrarySearch(event.target.value)} placeholder={t('agents.searchPlaceholder')} type="search" name="agent-library-filter-query" autoComplete="off" autoFocus />
            </div>
            <div className="assistant-library-list">
              {libraryAgents.map((agent) => renderAgent(agent, true))}
              {libraryAgents.length === 0 && <div className="assistant-list-empty">{t('agents.noMatch')}</div>}
            </div>
          </section>
        </div>
      )}
    </aside>
  );
}
