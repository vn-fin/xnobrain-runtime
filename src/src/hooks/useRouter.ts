import { useCallback, useEffect, useState } from 'react';
import type { Agent, CenterView, RightView } from '../types';

export type RouteState = {
  centerView: CenterView;
  agentId: string;
  conversationId: string;
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
  let rightView: RightView = 'workspace';

  if (seg[0] === 'sandbox') centerView = 'sandbox';
  else if (seg[0] === 'connections') centerView = 'connections';
  else if (seg[0] === 'skills') centerView = 'skills';
  else if (seg[0] === 'teams') centerView = 'teams';
  else if (seg[0] === 'kanban' || seg[0] === 'board') centerView = 'kanban';
  else if (seg[0] === 'data' || seg[0] === 'settings') centerView = 'data';
  else if (seg[0] === 'agents') {
    agentId = seg[1] ?? '';
    if (seg[2] === 'conversations') conversationId = seg[3] ?? '';
    const panel = sp.get('panel');
    if (panel === 'skills' || panel === 'runtime' || panel === 'cron' || panel === 'workspace') rightView = panel;
  }

  return {
    centerView,
    agentId,
    conversationId,
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
  if (state.centerView === 'sandbox') return '/sandbox';
  if (state.centerView === 'connections') return '/connections';
  if (state.centerView === 'data') return '/settings';
  if (state.centerView === 'teams') return '/teams';
  if (state.centerView === 'kanban') return '/kanban';
  if (state.centerView === 'skills') {
    if (state.skillsSearch.trim()) params.set('q', state.skillsSearch.trim());
    if (state.skillsGroupFilter !== 'all') params.set('group', state.skillsGroupFilter);
    return `/skills${params.size ? `?${params}` : ''}`;
  }
  if (!state.agentId) return '/';
  let path = `/agents/${encodeURIComponent(state.agentId)}`;
  if (state.conversationId) path += `/conversations/${encodeURIComponent(state.conversationId)}`;
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
  const [rightView, setRightView] = useState<RightView>(bootRoute.rightView);
  const [activeAgentId, setActiveAgentId] = useState(bootRoute.agentId);
  const [activeConversationId, setActiveConversationId] = useState(bootRoute.conversationId);
  const [agentSearch, setAgentSearch] = useState(bootRoute.agentSearch);
  const [skillsSearch, setSkillsSearch] = useState(bootRoute.skillsSearch);
  const [skillsGroupFilter, setSkillsGroupFilter] = useState(bootRoute.skillsGroupFilter);

  useEffect(() => {
    const url = computeUrl({
      centerView, agentId: activeAgentId, conversationId: activeConversationId, rightView,
      agentSearch, skillsSearch, skillsGroupFilter,
    });
    const current = window.location.pathname + window.location.search;
    if (url === current) return;
    if (url.split('?')[0] !== window.location.pathname) window.history.pushState(null, '', url);
    else window.history.replaceState(null, '', url);
  }, [centerView, activeAgentId, activeConversationId, rightView, agentSearch, skillsSearch, skillsGroupFilter]);

  useEffect(() => {
    const onPop = () => {
      const state = browserRoute();
      setCenterView(state.centerView);
      setActiveAgentId(state.agentId);
      setActiveConversationId(state.conversationId);
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

  const reconcileAgents = useCallback((agents: Agent[]) => {
    const next = reconcileSelection(activeAgentId, activeConversationId, agents);
    setActiveAgentId(next.agentId);
    setActiveConversationId(next.conversationId);
    const currentState: RouteState = {
      centerView, agentId: next.agentId, conversationId: next.conversationId, rightView,
      agentSearch, skillsSearch, skillsGroupFilter,
    };
    window.history.replaceState(null, '', computeUrl(currentState));
  }, [activeAgentId, activeConversationId, centerView, rightView, agentSearch, skillsSearch, skillsGroupFilter]);

  return {
    centerView, setCenterView, rightView, setRightView,
    activeAgentId, setActiveAgentId, activeConversationId, setActiveConversationId,
    agentSearch, setAgentSearch, skillsSearch, setSkillsSearch,
    skillsGroupFilter, setSkillsGroupFilter,
    openChat, reconcileAgents,
  };
}
