import { useCallback, useEffect, useState } from 'react';
import type { Agent, CenterView, RightView } from '../types';

export type SettingsSection = 'profiles' | 'context' | 'vm' | 'connectors' | 'mcp' | 'blends';

export type RouteState = {
  centerView: CenterView;
  settingsSection: SettingsSection;
  agentId: string;
  conversationId: string;
  teamId: string;
  teamRunId: string;
  teamCreate: boolean;
  kanbanTaskId: string;
  kanbanAgentId: string;
  kanbanConversationId: string;
  rightView: RightView;
  agentSearch: string;
  skillsSearch: string;
  skillsGroupFilter: string;
};

export function parseRoute(pathname: string, search: string): RouteState {
  const seg = pathname.split('/').filter(Boolean);
  const sp = new URLSearchParams(search);
  let centerView: CenterView = 'chat';
  let agentId = '';
  let conversationId = '';
  let teamId = '';
  let teamRunId = '';
  let teamCreate = false;
  let kanbanTaskId = '';
  let kanbanAgentId = '';
  let kanbanConversationId = '';
  let rightView: RightView = 'workspace';
  let settingsSection: SettingsSection = 'profiles';

  // Keep old deep links useful while the two destinations live under Settings.
  if (seg[0] === 'sandbox') {
    centerView = 'data';
    settingsSection = 'vm';
  } else if (seg[0] === 'connections') {
    centerView = 'data';
    settingsSection = 'connectors';
  }
  else if (seg[0] === 'skills') centerView = 'skills';
  else if (seg[0] === 'teams') {
    centerView = 'teams';
    if (seg[1] === 'new') {
      teamCreate = true;
    } else {
      teamId = seg[1] ?? '';
      if (seg[2] === 'runs') teamRunId = seg[3] ?? '';
    }
  }
  else if (seg[0] === 'kanban' || seg[0] === 'board') {
    centerView = 'kanban';
    if (seg[1] === 'tasks') kanbanTaskId = seg[2] ?? '';
    kanbanAgentId = sp.get('agent') ?? '';
    kanbanConversationId = sp.get('conversation') ?? '';
  }
  else if (seg[0] === 'analytics' || seg[0] === 'usage') centerView = 'analytics';
  else if (seg[0] === 'cron' || seg[0] === 'automations') centerView = 'cron';
  else if (seg[0] === 'data' || seg[0] === 'settings') {
    centerView = 'data';
    if (seg[1] === 'vm' || seg[1] === 'connectors' || seg[1] === 'profiles' || seg[1] === 'context' || seg[1] === 'mcp' || seg[1] === 'blends') {
      settingsSection = seg[1];
    }
  }
  else if (seg[0] === 'agents') {
    agentId = seg[1] ?? '';
    if (seg[2] === 'sessions' || seg[2] === 'conversations') conversationId = seg[3] ?? '';
    const panel = sp.get('panel');
    if (panel === 'skills' || panel === 'cron' || panel === 'runtime' || panel === 'settings' || panel === 'workspace') rightView = panel;
  }

  return {
    centerView,
    settingsSection,
    agentId,
    conversationId,
    teamId,
    teamRunId,
    teamCreate,
    kanbanTaskId,
    kanbanAgentId,
    kanbanConversationId,
    rightView,
    agentSearch: sp.get('agentq') ?? '',
    skillsSearch: sp.get('q') ?? '',
    skillsGroupFilter: sp.get('group') ?? 'all',
  };
}

export function reconcileSelection(agentId: string, conversationId: string, agents: Agent[]) {
  const agent = agents.find((item) => item.id === agentId) ?? agents[0];
  if (!agent) return { agentId: '', conversationId: '' };
  const conversation = agent.conversations.find((item) => item.id === conversationId) ?? agent.conversations[0];
  return { agentId: agent.id, conversationId: conversation?.id ?? '' };
}

export function computeUrl(state: RouteState): string {
  const params = new URLSearchParams();
  if (state.centerView === 'data') return `/settings/${state.settingsSection}`;
  if (state.centerView === 'teams') {
    if (state.teamCreate) return '/teams/new';
    if (!state.teamId) return '/teams';
    let path = `/teams/${encodeURIComponent(state.teamId)}`;
    if (state.teamRunId) path += `/runs/${encodeURIComponent(state.teamRunId)}`;
    return path;
  }
  if (state.centerView === 'kanban') {
    if (!state.kanbanTaskId) return '/kanban';
    const path = `/kanban/tasks/${encodeURIComponent(state.kanbanTaskId)}`;
    if (state.kanbanAgentId) params.set('agent', state.kanbanAgentId);
    if (state.kanbanConversationId) params.set('conversation', state.kanbanConversationId);
    return `${path}${params.size ? `?${params}` : ''}`;
  }
  if (state.centerView === 'analytics') return '/analytics';
  if (state.centerView === 'cron') return '/cron';
  if (state.centerView === 'skills') {
    if (state.skillsSearch.trim()) params.set('q', state.skillsSearch.trim());
    if (state.skillsGroupFilter !== 'all') params.set('group', state.skillsGroupFilter);
    return `/skills${params.size ? `?${params}` : ''}`;
  }
  if (!state.agentId) return '/';
  let path = `/agents/${encodeURIComponent(state.agentId)}`;
  if (state.conversationId) path += `/sessions/${encodeURIComponent(state.conversationId)}`;
  if (state.rightView !== 'workspace') params.set('panel', state.rightView);
  if (state.agentSearch.trim()) params.set('agentq', state.agentSearch.trim());
  return `${path}${params.size ? `?${params}` : ''}`;
}

function browserRoute() {
  return parseRoute(window.location.pathname, window.location.search);
}

const bootRoute = browserRoute();

export function useRouter() {
  const [centerView, setCenterView] = useState<CenterView>(bootRoute.centerView);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>(bootRoute.settingsSection);
  const [rightView, setRightView] = useState<RightView>(bootRoute.rightView);
  const [activeAgentId, setActiveAgentId] = useState(bootRoute.agentId);
  const [activeConversationId, setActiveConversationId] = useState(bootRoute.conversationId);
  const [activeTeamId, setActiveTeamId] = useState(bootRoute.teamId);
  const [activeTeamRunId, setActiveTeamRunId] = useState(bootRoute.teamRunId);
  const [teamCreate, setTeamCreate] = useState(bootRoute.teamCreate);
  const [kanbanTaskId, setKanbanTaskId] = useState(bootRoute.kanbanTaskId);
  const [kanbanAgentId, setKanbanAgentId] = useState(bootRoute.kanbanAgentId);
  const [kanbanConversationId, setKanbanConversationId] = useState(bootRoute.kanbanConversationId);
  const [agentSearch, setAgentSearch] = useState(bootRoute.agentSearch);
  const [skillsSearch, setSkillsSearch] = useState(bootRoute.skillsSearch);
  const [skillsGroupFilter, setSkillsGroupFilter] = useState(bootRoute.skillsGroupFilter);

  useEffect(() => {
    const url = computeUrl({
      centerView, settingsSection, agentId: activeAgentId, conversationId: activeConversationId,
      teamId: activeTeamId, teamRunId: activeTeamRunId, teamCreate, rightView,
      kanbanTaskId, kanbanAgentId, kanbanConversationId,
      agentSearch, skillsSearch, skillsGroupFilter,
    });
    const current = window.location.pathname + window.location.search;
    if (url === current) return;
    if (url.split('?')[0] !== window.location.pathname) window.history.pushState(null, '', url);
    else window.history.replaceState(null, '', url);
  }, [
    centerView, settingsSection, activeAgentId, activeConversationId, activeTeamId, activeTeamRunId,
    teamCreate, kanbanTaskId, kanbanAgentId, kanbanConversationId,
    rightView, agentSearch, skillsSearch, skillsGroupFilter,
  ]);

  useEffect(() => {
    const onPop = () => {
      const state = browserRoute();
      setCenterView(state.centerView);
      setSettingsSection(state.settingsSection);
      setActiveAgentId(state.agentId);
      setActiveConversationId(state.conversationId);
      setActiveTeamId(state.teamId);
      setActiveTeamRunId(state.teamRunId);
      setTeamCreate(state.teamCreate);
      setKanbanTaskId(state.kanbanTaskId);
      setKanbanAgentId(state.kanbanAgentId);
      setKanbanConversationId(state.kanbanConversationId);
      setRightView(state.rightView);
      setAgentSearch(state.agentSearch);
      setSkillsSearch(state.skillsSearch);
      setSkillsGroupFilter(state.skillsGroupFilter);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const openChat = useCallback((agentId: string, conversationId: string) => {
    setActiveAgentId(agentId);
    setActiveConversationId(conversationId);
    setCenterView('chat');
  }, []);

  const openTeam = useCallback((teamId = '', runId = '', replace = false) => {
    setActiveTeamId(teamId);
    setActiveTeamRunId(runId);
    setTeamCreate(false);
    setCenterView('teams');
    if (replace) {
      let path = teamId ? `/teams/${encodeURIComponent(teamId)}` : '/teams';
      if (runId) path += `/runs/${encodeURIComponent(runId)}`;
      window.history.replaceState(null, '', path);
    }
  }, []);

  const createTeam = useCallback(() => {
    setActiveTeamId('');
    setActiveTeamRunId('');
    setTeamCreate(true);
    setCenterView('teams');
  }, []);

  const openKanbanTask = useCallback((taskId = '', agentId = '', conversationId = '') => {
    setKanbanTaskId(taskId);
    setKanbanAgentId(taskId ? agentId : '');
    setKanbanConversationId(taskId ? conversationId : '');
    setCenterView('kanban');
  }, []);

  const reconcileAgents = useCallback((agents: Agent[]) => {
    const next = reconcileSelection(activeAgentId, activeConversationId, agents);
    setActiveAgentId(next.agentId);
    setActiveConversationId(next.conversationId);
    const currentState: RouteState = {
      centerView, settingsSection, agentId: next.agentId, conversationId: next.conversationId,
      teamId: activeTeamId, teamRunId: activeTeamRunId, teamCreate, rightView,
      kanbanTaskId, kanbanAgentId, kanbanConversationId,
      agentSearch, skillsSearch, skillsGroupFilter,
    };
    window.history.replaceState(null, '', computeUrl(currentState));
  }, [
    activeAgentId, activeConversationId, activeTeamId, activeTeamRunId, teamCreate,
    kanbanTaskId, kanbanAgentId, kanbanConversationId,
    centerView, settingsSection, rightView, agentSearch, skillsSearch, skillsGroupFilter,
  ]);

  return {
    centerView, setCenterView, settingsSection, setSettingsSection, rightView, setRightView,
    activeAgentId, setActiveAgentId, activeConversationId, setActiveConversationId,
    activeTeamId, activeTeamRunId, teamCreate, openTeam, createTeam,
    kanbanTaskId, kanbanAgentId, kanbanConversationId, openKanbanTask,
    agentSearch, setAgentSearch, skillsSearch, setSkillsSearch,
    skillsGroupFilter, setSkillsGroupFilter,
    openChat, reconcileAgents,
  };
}
