import { useCallback, useEffect, useState } from 'react';
import type { Agent, CenterView, RightView } from '../types';
import { requestNavigation } from '../utils/navigationGuard';

export type SettingsSection = 'profiles' | 'context' | 'vm' | 'connectors' | 'mcp' | 'blends';

export type RouteState = {
  centerView: CenterView;
  settingsSection: SettingsSection;
  agentId: string;
  conversationId: string;
  teamId: string;
  teamRunId: string;
  teamCreate: boolean;
  teamAgentId: string;
  teamConversationId: string;
  kanbanBoardId: string;
  kanbanTaskId: string;
  kanbanAgentId: string;
  kanbanConversationId: string;
  cronJobId: string;
  cronAgentId: string;
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
  let teamAgentId = '';
  let teamConversationId = '';
  let kanbanTaskId = '';
  let kanbanBoardId = '';
  let kanbanAgentId = '';
  let kanbanConversationId = '';
  let cronJobId = '';
  let cronAgentId = '';
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
      if (teamId && seg[2] === 'edit') {
        teamCreate = true;
      } else if (seg[2] === 'runs') {
        teamRunId = seg[3] ?? '';
        teamAgentId = teamRunId ? sp.get('agent') ?? '' : '';
        teamConversationId = teamRunId ? sp.get('conversation') ?? '' : '';
      }
    }
  }
  else if (seg[0] === 'kanban' || seg[0] === 'board') {
    centerView = 'kanban';
    if (seg[1] === 'boards') {
      kanbanBoardId = seg[2] ?? '';
      if (seg[3] === 'tasks') kanbanTaskId = seg[4] ?? '';
    } else if (seg[1] === 'tasks') kanbanTaskId = seg[2] ?? '';
    kanbanAgentId = sp.get('agent') ?? '';
    kanbanConversationId = sp.get('conversation') ?? '';
  }
  else if (seg[0] === 'analytics' || seg[0] === 'usage') centerView = 'analytics';
  else if (seg[0] === 'cron' || seg[0] === 'automations') {
    centerView = 'cron';
    if (seg[1] === 'jobs') cronJobId = seg[2] ?? '';
    cronAgentId = cronJobId ? sp.get('agent') ?? '' : '';
  }
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
    if (panel === 'skills' || panel === 'cron' || panel === 'runtime' || panel === 'workspace') rightView = panel;
  }

  return {
    centerView,
    settingsSection,
    agentId,
    conversationId,
    teamId,
    teamRunId,
    teamCreate,
    teamAgentId,
    teamConversationId,
    kanbanBoardId,
    kanbanTaskId,
    kanbanAgentId,
    kanbanConversationId,
    cronJobId,
    cronAgentId,
    rightView,
    agentSearch: sp.get('agentq') ?? '',
    skillsSearch: sp.get('q') ?? '',
    skillsGroupFilter: sp.get('group') ?? 'all',
  };
}

export function reconcileSelection(agentId: string, conversationId: string, agents: Agent[]) {
  const agent = agents.find((item) => item.id === agentId) ?? agents[0];
  if (!agent) return { agentId: '', conversationId: '' };
  if (conversationId) return { agentId: agent.id, conversationId };
  return { agentId: agent.id, conversationId: agent.conversations[0]?.id ?? '' };
}

export function computeUrl(state: RouteState): string {
  const params = new URLSearchParams();
  if (state.centerView === 'data') return `/settings/${state.settingsSection}`;
  if (state.centerView === 'teams') {
    if (state.teamCreate) return state.teamId
      ? `/teams/${encodeURIComponent(state.teamId)}/edit`
      : '/teams/new';
    if (!state.teamId) return '/teams';
    let path = `/teams/${encodeURIComponent(state.teamId)}`;
    if (state.teamRunId) path += `/runs/${encodeURIComponent(state.teamRunId)}`;
    if (state.teamRunId && state.teamAgentId) params.set('agent', state.teamAgentId);
    if (state.teamRunId && state.teamConversationId) params.set('conversation', state.teamConversationId);
    return `${path}${params.size ? `?${params}` : ''}`;
  }
  if (state.centerView === 'kanban') {
    if (!state.kanbanBoardId) {
      if (!state.kanbanTaskId) return '/kanban';
      const path = `/kanban/tasks/${encodeURIComponent(state.kanbanTaskId)}`;
      if (state.kanbanAgentId) params.set('agent', state.kanbanAgentId);
      if (state.kanbanConversationId) params.set('conversation', state.kanbanConversationId);
      return `${path}${params.size ? `?${params}` : ''}`;
    }
    let path = `/kanban/boards/${encodeURIComponent(state.kanbanBoardId)}`;
    if (state.kanbanTaskId) path += `/tasks/${encodeURIComponent(state.kanbanTaskId)}`;
    if (state.kanbanAgentId) params.set('agent', state.kanbanAgentId);
    if (state.kanbanConversationId) params.set('conversation', state.kanbanConversationId);
    return `${path}${params.size ? `?${params}` : ''}`;
  }
  if (state.centerView === 'analytics') return '/analytics';
  if (state.centerView === 'cron') {
    if (!state.cronJobId) return '/cron';
    const path = `/cron/jobs/${encodeURIComponent(state.cronJobId)}`;
    if (state.cronAgentId) params.set('agent', state.cronAgentId);
    return `${path}${params.size ? `?${params}` : ''}`;
  }
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
  const [teamAgentId, setTeamAgentId] = useState(bootRoute.teamAgentId);
  const [teamConversationId, setTeamConversationId] = useState(bootRoute.teamConversationId);
  const [kanbanTaskId, setKanbanTaskId] = useState(bootRoute.kanbanTaskId);
  const [kanbanBoardId, setKanbanBoardId] = useState(bootRoute.kanbanBoardId);
  const [kanbanAgentId, setKanbanAgentId] = useState(bootRoute.kanbanAgentId);
  const [kanbanConversationId, setKanbanConversationId] = useState(bootRoute.kanbanConversationId);
  const [cronJobId, setCronJobId] = useState(bootRoute.cronJobId);
  const [cronAgentId, setCronAgentId] = useState(bootRoute.cronAgentId);
  const [agentSearch, setAgentSearch] = useState(bootRoute.agentSearch);
  const [skillsSearch, setSkillsSearch] = useState(bootRoute.skillsSearch);
  const [skillsGroupFilter, setSkillsGroupFilter] = useState(bootRoute.skillsGroupFilter);

  useEffect(() => {
    const url = computeUrl({
      centerView, settingsSection, agentId: activeAgentId, conversationId: activeConversationId,
      teamId: activeTeamId, teamRunId: activeTeamRunId, teamCreate, teamAgentId, teamConversationId, rightView,
      kanbanBoardId, kanbanTaskId, kanbanAgentId, kanbanConversationId,
      cronJobId, cronAgentId,
      agentSearch, skillsSearch, skillsGroupFilter,
    });
    const current = window.location.pathname + window.location.search;
    if (url === current) return;
    if (url.split('?')[0] !== window.location.pathname) window.history.pushState(null, '', url);
    else window.history.replaceState(null, '', url);
  }, [
    centerView, settingsSection, activeAgentId, activeConversationId, activeTeamId, activeTeamRunId,
    teamCreate, teamAgentId, teamConversationId, kanbanBoardId, kanbanTaskId, kanbanAgentId, kanbanConversationId,
    cronJobId, cronAgentId,
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
      setTeamAgentId(state.teamAgentId);
      setTeamConversationId(state.teamConversationId);
      setKanbanBoardId(state.kanbanBoardId);
      setKanbanTaskId(state.kanbanTaskId);
      setKanbanAgentId(state.kanbanAgentId);
      setKanbanConversationId(state.kanbanConversationId);
      setCronJobId(state.cronJobId);
      setCronAgentId(state.cronAgentId);
      setRightView(state.rightView);
      setAgentSearch(state.agentSearch);
      setSkillsSearch(state.skillsSearch);
      setSkillsGroupFilter(state.skillsGroupFilter);
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const openChat = useCallback((agentId: string, conversationId: string) => {
    requestNavigation(() => {
      setActiveAgentId(agentId);
      setActiveConversationId(conversationId);
      setCenterView('chat');
    });
  }, []);

  const openTeam = useCallback((teamId = '', runId = '', replace = false) => {
    requestNavigation(() => {
    setActiveTeamId(teamId);
    setActiveTeamRunId(runId);
    setTeamCreate(false);
    setTeamAgentId('');
    setTeamConversationId('');
    setCenterView('teams');
    if (replace) {
      let path = teamId ? `/teams/${encodeURIComponent(teamId)}` : '/teams';
      if (runId) path += `/runs/${encodeURIComponent(runId)}`;
      window.history.replaceState(null, '', path);
    }
    });
  }, []);

  const openTeamConversation = useCallback((agentId = '', conversationId = '') => {
    requestNavigation(() => {
    setTeamAgentId(conversationId ? agentId : '');
    setTeamConversationId(conversationId);
    setCenterView('teams');
    });
  }, []);

  const createTeam = useCallback(() => {
    requestNavigation(() => {
    setActiveTeamId('');
    setActiveTeamRunId('');
    setTeamCreate(true);
    setCenterView('teams');
    });
  }, []);

  const editTeam = useCallback((teamId: string) => {
    requestNavigation(() => {
    setActiveTeamId(teamId);
    setActiveTeamRunId('');
    setTeamCreate(true);
    setTeamAgentId('');
    setTeamConversationId('');
    setCenterView('teams');
    });
  }, []);

  const openKanbanBoard = useCallback((boardId = '') => {
    requestNavigation(() => {
    setKanbanBoardId(boardId);
    setKanbanTaskId('');
    setKanbanAgentId('');
    setKanbanConversationId('');
    setCenterView('kanban');
    });
  }, []);

  const openKanbanTask = useCallback((taskId = '', agentId = '', conversationId = '', boardId?: string) => {
    requestNavigation(() => {
    if (boardId !== undefined) setKanbanBoardId(boardId);
    setKanbanTaskId(taskId);
    setKanbanAgentId(taskId ? agentId : '');
    setKanbanConversationId(taskId ? conversationId : '');
    setCenterView('kanban');
    });
  }, []);

  const openCronJob = useCallback((jobId = '', agentId = '') => {
    requestNavigation(() => {
    setCronJobId(jobId);
    setCronAgentId(jobId ? agentId : '');
    setCenterView('cron');
    });
  }, []);

  const guardedSetCenterView = useCallback((view: CenterView) => {
    requestNavigation(() => setCenterView(view));
  }, []);
  const guardedSetSettingsSection = useCallback((section: SettingsSection) => {
    requestNavigation(() => setSettingsSection(section));
  }, []);

  const reconcileAgents = useCallback((agents: Agent[]) => {
    const next = reconcileSelection(activeAgentId, activeConversationId, agents);
    setActiveAgentId(next.agentId);
    setActiveConversationId(next.conversationId);
    const currentState: RouteState = {
      centerView, settingsSection, agentId: next.agentId, conversationId: next.conversationId,
      teamId: activeTeamId, teamRunId: activeTeamRunId, teamCreate, teamAgentId, teamConversationId, rightView,
      kanbanBoardId, kanbanTaskId, kanbanAgentId, kanbanConversationId,
      cronJobId, cronAgentId,
      agentSearch, skillsSearch, skillsGroupFilter,
    };
    window.history.replaceState(null, '', computeUrl(currentState));
  }, [
    activeAgentId, activeConversationId, activeTeamId, activeTeamRunId, teamCreate, teamAgentId, teamConversationId,
    kanbanBoardId, kanbanTaskId, kanbanAgentId, kanbanConversationId,
    cronJobId, cronAgentId,
    centerView, settingsSection, rightView, agentSearch, skillsSearch, skillsGroupFilter,
  ]);

  return {
    centerView, setCenterView: guardedSetCenterView,
    settingsSection, setSettingsSection: guardedSetSettingsSection, rightView, setRightView,
    activeAgentId, setActiveAgentId, activeConversationId, setActiveConversationId,
    activeTeamId, activeTeamRunId, teamCreate, teamAgentId, teamConversationId, openTeam, openTeamConversation, createTeam, editTeam,
    kanbanBoardId, kanbanTaskId, kanbanAgentId, kanbanConversationId, openKanbanBoard, openKanbanTask,
    cronJobId, cronAgentId, openCronJob,
    agentSearch, setAgentSearch, skillsSearch, setSkillsSearch,
    skillsGroupFilter, setSkillsGroupFilter,
    openChat, reconcileAgents,
  };
}
