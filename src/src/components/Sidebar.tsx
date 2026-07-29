import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  BarChart3,
  BookOpen,
  ChevronDown,
  ChevronRight,
  CircleUserRound,
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
  Pencil,
  Pin,
  PinOff,
  Plus,
  Puzzle,
  Search,
  Settings,
  Sun,
  Trash2,
  X,
} from 'lucide-react';
import type { ActiveUser } from '../auth';
import type { Brain4AllEdition } from '../runtime';
import { SUPPORTED_LANGUAGES } from '../i18n';
import { useTheme } from '../theme';
import type { Agent, CenterView } from '../types';

const RECENT_LIMIT = 4;
const RECENT_KEY = 'brain4all.recentAssistants';
const PINNED_KEY = 'brain4all.pinnedAssistants';

const navItems = [
  { id: 'skills', label: 'skills', icon: Puzzle },
  { id: 'kanban', label: 'kanban', icon: Columns3 },
  { id: 'teams', label: 'teams', icon: Network },
  { id: 'analytics', label: 'analytics', icon: BarChart3 },
  { id: 'data', label: 'settings', icon: Settings },
] as const;

function readIds(key: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) ?? '[]');
    return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : [];
  } catch {
    return [];
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
  edition?: Brain4AllEdition;
  loginEnabled?: boolean;
  sessionActive?: boolean;
  onOpenLogin?: () => void;
  onOpenAccount?: () => void;
  onSignOut?: () => Promise<void>;
}) {
  const { t, i18n } = useTranslation();
  const { preference: themePref, setPreference: setThemePref } = useTheme();
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [librarySearch, setLibrarySearch] = useState('');
  const [agentMenuId, setAgentMenuId] = useState<string | null>(null);
  const [exportingAgentId, setExportingAgentId] = useState<string | null>(null);
  const [agentActionError, setAgentActionError] = useState('');
  const [renamingAgentId, setRenamingAgentId] = useState<string | null>(null);
  const [agentRenameValue, setAgentRenameValue] = useState('');
  const [recentIds, setRecentIds] = useState(() => readIds(RECENT_KEY));
  const [pinnedIds, setPinnedIds] = useState(() => readIds(PINNED_KEY));

  useEffect(() => {
    setRecentIds((current) => {
      const next = [activeAgent.id, ...current.filter((id) => id !== activeAgent.id)].slice(0, RECENT_LIMIT);
      localStorage.setItem(RECENT_KEY, JSON.stringify(next));
      return next;
    });
  }, [activeAgent.id]);

  useEffect(() => {
    localStorage.setItem(PINNED_KEY, JSON.stringify(pinnedIds));
  }, [pinnedIds]);

  const q = agentSearch.trim().toLowerCase();
  const matches = (agent: Agent, query: string) => !query
    || agent.title.toLowerCase().includes(query)
    || agent.name.toLowerCase().includes(query)
    || agent.model.toLowerCase().includes(query);
  const pinnedAgents = pinnedIds.map((id) => agents.find((agent) => agent.id === id)).filter((agent): agent is Agent => !!agent);
  const recentAgents = recentIds
    .filter((id) => !pinnedIds.includes(id))
    .map((id) => agents.find((agent) => agent.id === id))
    .filter((agent): agent is Agent => !!agent);
  const focusedAgents = q ? agents.filter((agent) => matches(agent, q)) : [...pinnedAgents, ...recentAgents];
  const libraryAgents = useMemo(() => {
    const query = librarySearch.trim().toLowerCase();
    return agents.filter((agent) => matches(agent, query));
  }, [agents, librarySearch]);

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
      <div key={agent.id} className={`${active ? 'agent-row active' : 'agent-row'}${library ? ' library-agent-row' : ''}`}>
        <button className="agent-row-main" onClick={() => selectAgent(agent)} onDoubleClick={() => !library && startAgentRename(agent)} title={agent.title}>
          <span className="status-dot" />
          <span>
            <strong>{agent.title}</strong>
            <small>{agent.model}</small>
          </span>
        </button>
        {!library && (
          <>
            {pinnedIds.includes(agent.id) && <Pin className="agent-pin" size={12} aria-label={t('agents.pinned')} />}
            <button
              className="agent-row-more"
              title={t('agents.menu', { defaultValue: 'Assistant options' })}
              aria-label={t('agents.menu', { defaultValue: 'Assistant options' })}
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
                <button className="row-menu-item" onClick={() => togglePinned(agent.id)}>
                  {pinnedIds.includes(agent.id) ? <PinOff size={14} /> : <Pin size={14} />}
                  {t(pinnedIds.includes(agent.id) ? 'agents.unpin' : 'agents.pin')}
                </button>
                <button className="row-menu-item" disabled={exportingAgentId === agent.id} onClick={() => startAgentRename(agent)}>
                  <Pencil size={14} /> {t('agents.renameAction', { defaultValue: 'Rename' })}
                </button>
                <button className="row-menu-item" disabled={exportingAgentId === agent.id} onClick={() => void exportAgent(agent)}>
                  <Download size={14} /> {exportingAgentId === agent.id ? 'Exporting…' : t('agents.exportAction', { defaultValue: 'Export' })}
                </button>
                <button className="row-menu-item danger" onClick={() => { setAgentMenuId(null); onRequestDeleteAgent(agent.id); }}>
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
    <aside className="left-panel">
      <div className="brand">
        <span>Brain4All</span>
        <button className="icon-button" title="Toggle sidebar"><PanelLeftClose size={17} /></button>
      </div>

      <div className="history-section assistant-launcher">
        <div className="section-title agents-title">
          <span>{t('agents.title')}</span>
          <button className="icon-button" title={t('agents.new')} onClick={onNewAgent}><Plus size={16} /></button>
        </div>
        <div className="agent-search">
          <Search size={14} />
          <input value={agentSearch} onChange={(event) => onAgentSearch(event.target.value)} placeholder={t('agents.searchPlaceholder')} />
        </div>
        {!q && pinnedAgents.length > 0 && <div className="assistant-list-label"><Pin size={12} /> {t('agents.pinned')}</div>}
        {!q && pinnedAgents.map((agent) => renderAgent(agent))}
        {!q && recentAgents.length > 0 && <div className="assistant-list-label">{t('agents.recent')}</div>}
        {q && focusedAgents.length > 0 && <div className="assistant-list-label">{t('agents.searchResults')}</div>}
        {(q ? focusedAgents : recentAgents).map((agent) => renderAgent(agent))}
        {focusedAgents.length === 0 && <div className="assistant-list-empty">{t('agents.noMatch')}</div>}
        <button className="open-library-button" onClick={() => setLibraryOpen(true)}>
          <BookOpen size={15} /> {t('agents.openLibrary')}
        </button>
        {agentActionError && <div className="agent-action-error" role="alert">{agentActionError}</div>}
      </div>

      <div className="workspace-nav">
        <button className="workspace-nav-toggle" aria-expanded={workspaceOpen} onClick={() => setWorkspaceOpen((open) => !open)}>
          {workspaceOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <span>{t('nav.workspace', { defaultValue: 'Workspace' })}</span>
        </button>
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
        <div className="theme-switcher" role="group" aria-label={t('theme.label')}>
          <button className={themePref === 'light' ? 'active' : ''} title={t('theme.light')} onClick={() => setThemePref('light')}><Sun size={15} /></button>
          <button className={themePref === 'dark' ? 'active' : ''} title={t('theme.dark')} onClick={() => setThemePref('dark')}><Moon size={15} /></button>
          <button className={themePref === 'auto' ? 'active' : ''} title={t('theme.auto')} onClick={() => setThemePref('auto')}><Monitor size={15} /></button>
        </div>
        <div className="lang-switcher">
          <Languages size={15} />
          <select value={i18n.resolvedLanguage} onChange={(event) => i18n.changeLanguage(event.target.value)} aria-label={t('language.label')}>
            {SUPPORTED_LANGUAGES.map((language) => <option key={language} value={language}>{t(`language.${language}`)}</option>)}
          </select>
          <ChevronDown size={14} />
        </div>
        {loginEnabled && (sessionActive ? (
          <div className="user-row">
            <button className="user-login" onClick={onOpenAccount} title="Open account">
              <span className="user-avatar">{user ? (user.displayName || user.email).charAt(0).toUpperCase() : <CircleUserRound size={14} />}</span>
              <span className="user-name">{user ? user.displayName || user.email : 'Account'}</span>
            </button>
            <button className="icon-button" title="Sign out" onClick={() => void onSignOut?.()}><LogOut size={16} /></button>
          </div>
        ) : (
          <button className="user-row user-login" onClick={onOpenLogin}>
            <span className="user-avatar"><LogIn size={14} /></span>
            <span className="user-name">Sign in</span>
          </button>
        ))}
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
              <input value={librarySearch} onChange={(event) => setLibrarySearch(event.target.value)} placeholder={t('agents.searchPlaceholder')} autoFocus />
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
