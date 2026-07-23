import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ChevronDown,
  History,
  KeyRound,
  Languages,
  Monitor,
  Moon,
  MoreHorizontal,
  PanelLeftClose,
  Pencil,
  Plus,
  Search,
  Server,
  Settings,
  Share2,
  Sparkles,
  Sun,
  Trash2,
  Network,
  Download,
} from 'lucide-react';
import { SUPPORTED_LANGUAGES } from '../i18n';
import { useTheme } from '../theme';
import type { Agent, CenterView, Conversation } from '../types';

const navItems = [
  { id: 'skills', label: 'skills', icon: Sparkles },
  { id: 'sandbox', label: 'runtime', icon: Server },
  { id: 'teams', label: 'teams', icon: Network },
  { id: 'connections', label: 'connections', icon: KeyRound },
  { id: 'data', label: 'settings', icon: Settings },
] as const;

export function Sidebar({
  agents,
  activeAgent,
  activeConversation,
  centerView,
  agentSearch,
  onAgentSearch,
  onNavigate,
  onSelectAgent,
  onSelectConversation,
  onRenameAgent,
  onExportAgent,
  onRenameConversation,
  onRequestDeleteConversation,
  onNewAgent,
}: {
  agents: Agent[];
  activeAgent: Agent;
  activeConversation: Conversation | undefined;
  centerView: CenterView;
  agentSearch: string;
  onAgentSearch: (v: string) => void;
  onNavigate: (view: CenterView) => void;
  onSelectAgent: (agent: Agent) => void;
  onSelectConversation: (conversationId: string) => void;
  onRenameAgent: (agentId: string, displayName: string) => void | Promise<void>;
  onExportAgent: (agentId: string) => Promise<void>;
  onRenameConversation: (conversationId: string, title: string) => void | Promise<void>;
  onRequestDeleteConversation: (conversationId: string) => void;
  onNewAgent: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { preference: themePref, setPreference: setThemePref } = useTheme();
  const [searchOpen, setSearchOpen] = useState(!!agentSearch);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [agentMenuId, setAgentMenuId] = useState<string | null>(null);
  const [exportingAgentId, setExportingAgentId] = useState<string | null>(null);
  const [agentActionError, setAgentActionError] = useState('');
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState('');
  const [renamingAgentId, setRenamingAgentId] = useState<string | null>(null);
  const [agentRenameValue, setAgentRenameValue] = useState('');

  const startAgentRename = (agent: Agent) => {
    setAgentMenuId(null);
    setRenamingAgentId(agent.id);
    setAgentRenameValue(agent.title);
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

  const commitAgentRename = (agent: Agent) => {
    const value = agentRenameValue.trim();
    if (value && value !== agent.title) void onRenameAgent(agent.id, value);
    setRenamingAgentId(null);
  };

  const startRename = (conversation: Conversation) => {
    setMenuId(null);
    setRenamingId(conversation.id);
    setRenameValue(conversation.title);
  };

  const commitRename = (conversationId: string) => {
    const value = renameValue.trim();
    const current = activeAgent.conversations.find((c) => c.id === conversationId);
    if (value && value !== current?.title) void onRenameConversation(conversationId, value);
    setRenamingId(null);
  };

  const q = agentSearch.trim().toLowerCase();
  const filtered = agents.filter(
    (a) => !q || a.title.toLowerCase().includes(q) || a.name.toLowerCase().includes(q) || a.model.toLowerCase().includes(q),
  );

  return (
    <aside className="left-panel">
      <div className="brand">
        <span>Brain4All</span>
        <button className="icon-button" title="Toggle sidebar">
          <PanelLeftClose size={17} />
        </button>
      </div>

      <div className="main-nav">
        {navItems.map((item) => {
          const Icon = item.icon;
          const active =
            (item.id === 'sandbox' && centerView === 'sandbox') ||
            (item.id === 'skills' && centerView === 'skills') ||
            (item.id === 'teams' && centerView === 'teams') ||
            (item.id === 'connections' && centerView === 'connections') ||
            (item.id === 'data' && centerView === 'data');
          return (
            <button
              key={item.id}
              className={active ? 'nav-row active' : 'nav-row'}
              onClick={() => onNavigate(item.id as CenterView)}
            >
              <Icon size={18} />
              <span>{t(`nav.${item.label}`, { defaultValue: item.label[0].toUpperCase() + item.label.slice(1) })}</span>
            </button>
          );
        })}
      </div>

      <div className="history-section">
        <div className="section-title agents-title">
          <span>{t('agents.title')}</span>
          <div className="agents-title-actions">
            <button
              className="icon-button"
              title={t('agents.searchPlaceholder')}
              onClick={() => {
                setSearchOpen((v) => !v);
                onAgentSearch('');
              }}
            >
              <Search size={16} />
            </button>
            <button className="icon-button" title={t('agents.new')} onClick={onNewAgent}>
              <Plus size={16} />
            </button>
          </div>
        </div>

        {searchOpen && (
          <div className="agent-search">
            <Search size={14} />
            <input value={agentSearch} onChange={(e) => onAgentSearch(e.target.value)} placeholder={t('agents.searchPlaceholder')} autoFocus />
          </div>
        )}

        {filtered.map((agent) => {
          const active = agent.id === activeAgent.id && centerView === 'chat';
          if (renamingAgentId === agent.id) {
            return (
              <div key={agent.id} className={active ? 'agent-row active renaming' : 'agent-row renaming'}>
                <input
                  className="history-rename-input"
                  value={agentRenameValue}
                  autoFocus
                  aria-label={t('agents.renameLabel')}
                  onFocus={(e) => e.currentTarget.select()}
                  onChange={(e) => setAgentRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); commitAgentRename(agent); }
                    if (e.key === 'Escape') { e.preventDefault(); setRenamingAgentId(null); }
                  }}
                  onBlur={() => commitAgentRename(agent)}
                />
              </div>
            );
          }
          return (
            <div key={agent.id} className={active ? 'agent-row active' : 'agent-row'}>
              <button
                className="agent-row-main"
                onClick={() => onSelectAgent(agent)}
                onDoubleClick={(e) => { e.stopPropagation(); startAgentRename(agent); }}
                title={agent.title}
              >
                <span className="status-dot" />
                <span>
                  <strong>{agent.title}</strong>
                  <small>{agent.model}</small>
                </span>
              </button>
              <button
                className="agent-row-more"
                title={t('agents.menu', { defaultValue: 'Assistant options' })}
                aria-label={t('agents.menu', { defaultValue: 'Assistant options' })}
                aria-expanded={agentMenuId === agent.id}
                onClick={(event) => {
                  event.stopPropagation();
                  setMenuId(null);
                  setAgentActionError('');
                  setAgentMenuId((id) => (id === agent.id ? null : agent.id));
                }}
              >
                <MoreHorizontal size={16} />
              </button>
              {agentMenuId === agent.id && (
                <div className="row-menu" role="menu">
                  <button className="row-menu-item" disabled={exportingAgentId === agent.id} onClick={() => startAgentRename(agent)}>
                    <Pencil size={14} /> {t('agents.rename')}
                  </button>
                  <button className="row-menu-item" disabled={exportingAgentId === agent.id} onClick={() => void exportAgent(agent)}>
                    <Download size={14} /> {exportingAgentId === agent.id ? 'Exporting…' : t('agents.export', { defaultValue: 'Export profile' })}
                  </button>
                </div>
              )}
            </div>
          );
        })}
        {agentActionError && <div className="agent-action-error" role="alert">{agentActionError}</div>}

        <div className="section-title recent-title">
          <History size={14} />
          {t('agents.recent')}
        </div>
        {activeAgent.conversations.map((conversation) => {
          const active = conversation.id === activeConversation?.id && centerView === 'chat';
          if (renamingId === conversation.id) {
            return (
              <div key={conversation.id} className="history-row renaming">
                <input
                  className="history-rename-input"
                  value={renameValue}
                  autoFocus
                  aria-label={t('conversation.renameLabel')}
                  onFocus={(e) => e.currentTarget.select()}
                  onChange={(e) => setRenameValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') { e.preventDefault(); commitRename(conversation.id); }
                    if (e.key === 'Escape') { e.preventDefault(); setRenamingId(null); }
                  }}
                  onBlur={() => commitRename(conversation.id)}
                />
              </div>
            );
          }
          return (
            <div key={conversation.id} className={active ? 'history-row active' : 'history-row'}>
              <button
                className="history-row-main"
                onClick={() => onSelectConversation(conversation.id)}
                onDoubleClick={(e) => { e.stopPropagation(); startRename(conversation); }}
                title={t('conversation.rename')}
              >
                {conversation.title}
              </button>
              <button
                className="history-row-more"
                title={t('conversation.menu')}
                aria-label={t('conversation.menu')}
                onClick={(e) => { e.stopPropagation(); setMenuId((id) => (id === conversation.id ? null : conversation.id)); }}
              >
                <MoreHorizontal size={16} />
              </button>
              {menuId === conversation.id && (
                <div className="row-menu" role="menu">
                  <button className="row-menu-item" disabled title={t('conversation.shareUnavailable')}>
                    <Share2 size={14} /> {t('conversation.share')}
                  </button>
                  <button className="row-menu-item" onClick={() => startRename(conversation)}>
                    <Pencil size={14} /> {t('conversation.rename')}
                  </button>
                  <button
                    className="row-menu-item danger"
                    onClick={() => { setMenuId(null); onRequestDeleteConversation(conversation.id); }}
                  >
                    <Trash2 size={14} /> {t('conversation.delete')}
                  </button>
                </div>
              )}
            </div>
          );
        })}
        {(menuId || agentMenuId) && <div className="row-menu-catcher" onClick={() => { setMenuId(null); setAgentMenuId(null); }} />}
      </div>

      <div className="left-footer">
        <div className="theme-switcher" role="group" aria-label={t('theme.label')}>
          <button className={themePref === 'light' ? 'active' : ''} title={t('theme.light')} onClick={() => setThemePref('light')}>
            <Sun size={15} />
          </button>
          <button className={themePref === 'dark' ? 'active' : ''} title={t('theme.dark')} onClick={() => setThemePref('dark')}>
            <Moon size={15} />
          </button>
          <button className={themePref === 'auto' ? 'active' : ''} title={t('theme.auto')} onClick={() => setThemePref('auto')}>
            <Monitor size={15} />
          </button>
        </div>

        <div className="lang-switcher">
          <Languages size={15} />
          <select value={i18n.resolvedLanguage} onChange={(e) => i18n.changeLanguage(e.target.value)} aria-label={t('language.label')}>
            {SUPPORTED_LANGUAGES.map((lng) => (
              <option key={lng} value={lng}>
                {t(`language.${lng}`)}
              </option>
            ))}
          </select>
          <ChevronDown size={14} />
        </div>

      </div>
    </aside>
  );
}
