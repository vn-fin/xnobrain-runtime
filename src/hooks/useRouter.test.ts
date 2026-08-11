import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { computeUrl, parseRoute, type RouteState, useRouter } from './useRouter';

const state = (settingsSection: RouteState['settingsSection']): RouteState => ({
  centerView: 'data',
  settingsSection,
  agentId: '',
  conversationId: '',
  teamId: '',
  teamRunId: '',
  teamCreate: false,
  teamAgentId: '',
  teamConversationId: '',
  kanbanTaskId: '',
  kanbanAgentId: '',
  kanbanConversationId: '',
  cronJobId: '',
  cronAgentId: '',
  rightView: 'workspace',
  agentSearch: '',
  skillsSearch: '',
  skillsGroupFilter: 'all',
});

const teamsState = (overrides: Partial<RouteState> = {}): RouteState => ({
  ...state('profiles'),
  centerView: 'teams',
  ...overrides,
});

describe('settings routes', () => {
  it.each([
    ['/settings/profiles', 'profiles'],
    ['/settings/vm', 'vm'],
    ['/settings/connectors', 'connectors'],
  ] as const)('parses %s', (path, section) => {
    expect(parseRoute(path, '').settingsSection).toBe(section);
    expect(computeUrl(state(section))).toBe(path);
  });

  it('redirects legacy settings destinations to their matching section', () => {
    expect(parseRoute('/sandbox', '').settingsSection).toBe('vm');
    expect(parseRoute('/connections', '').settingsSection).toBe('connectors');
  });

  it('falls back to Profiles for an unknown settings section', () => {
    expect(parseRoute('/settings/unknown', '').settingsSection).toBe('profiles');
  });
});

describe('Team routes', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it.each([
    ['/teams', { teamId: '', teamRunId: '', teamCreate: false }],
    ['/teams/new', { teamId: '', teamRunId: '', teamCreate: true }],
    ['/teams/team-01', { teamId: 'team-01', teamRunId: '', teamCreate: false }],
    ['/teams/team-01/edit', { teamId: 'team-01', teamRunId: '', teamCreate: true }],
    ['/teams/team-01/runs/tr_123', { teamId: 'team-01', teamRunId: 'tr_123', teamCreate: false }],
  ] as const)('parses and rebuilds %s', (path, expected) => {
    const route = parseRoute(path, '');
    expect(route).toMatchObject({ centerView: 'teams', ...expected });
    expect(computeUrl(teamsState(expected))).toBe(path);
  });

  it('tracks the selected worker conversation for a Team run', () => {
    const path = '/teams/team-01/runs/tr_123?agent=agent-01&conversation=session-01';
    const route = parseRoute('/teams/team-01/runs/tr_123', '?agent=agent-01&conversation=session-01');
    expect(route).toMatchObject({
      centerView: 'teams',
      teamId: 'team-01',
      teamRunId: 'tr_123',
      teamAgentId: 'agent-01',
      teamConversationId: 'session-01',
    });
    expect(computeUrl({ ...state('profiles'), ...route })).toBe(path);
  });

  it('tracks Team navigation and restores Team state on popstate', async () => {
    const { result } = renderHook(() => useRouter());

    act(() => result.current.openTeam('team-01', 'tr_123'));
    await waitFor(() => expect(window.location.pathname).toBe('/teams/team-01/runs/tr_123'));

    act(() => result.current.createTeam());
    await waitFor(() => expect(window.location.pathname).toBe('/teams/new'));

    act(() => result.current.editTeam('team-01'));
    await waitFor(() => expect(window.location.pathname).toBe('/teams/team-01/edit'));

    act(() => {
      window.history.replaceState(null, '', '/teams/team-01/runs/tr_123');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.centerView).toBe('teams');
    expect(result.current.activeTeamId).toBe('team-01');
    expect(result.current.activeTeamRunId).toBe('tr_123');
    expect(result.current.teamCreate).toBe(false);
  });

  it('opens and restores a Team worker conversation', async () => {
    const { result } = renderHook(() => useRouter());

    act(() => result.current.openTeam('team-01', 'tr_123'));
    act(() => result.current.openTeamConversation('agent-01', 'session-01'));
    await waitFor(() => expect(window.location.href).toContain(
      '/teams/team-01/runs/tr_123?agent=agent-01&conversation=session-01',
    ));

    act(() => {
      window.history.replaceState(null, '', '/teams/team-01/runs/tr_123?agent=agent-02&conversation=session-02');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.teamAgentId).toBe('agent-02');
    expect(result.current.teamConversationId).toBe('session-02');
  });
});

describe('Kanban routes', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('tracks a task and its worker conversation in the URL', () => {
    const path = '/kanban/tasks/task-01?agent=agent-01&conversation=conversation-01';
    const route = parseRoute('/kanban/tasks/task-01', '?agent=agent-01&conversation=conversation-01');
    expect(route).toMatchObject({
      centerView: 'kanban',
      kanbanTaskId: 'task-01',
      kanbanAgentId: 'agent-01',
      kanbanConversationId: 'conversation-01',
    });
    expect(computeUrl({ ...state('profiles'), ...route })).toBe(path);
  });

  it('updates and restores Kanban task state on popstate', async () => {
    const { result } = renderHook(() => useRouter());

    act(() => result.current.openKanbanTask('task-01', 'agent-01', 'conversation-01'));
    await waitFor(() => expect(window.location.href).toContain(
      '/kanban/tasks/task-01?agent=agent-01&conversation=conversation-01',
    ));

    act(() => {
      window.history.replaceState(null, '', '/kanban/tasks/task-02');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.centerView).toBe('kanban');
    expect(result.current.kanbanTaskId).toBe('task-02');
    expect(result.current.kanbanAgentId).toBe('');
    expect(result.current.kanbanConversationId).toBe('');
  });
});

describe('Session routes', () => {
  it('canonicalizes the legacy conversation path to sessions', () => {
    const route = parseRoute('/agents/agent-01/conversations/session-01', '');
    expect(route).toMatchObject({
      centerView: 'chat',
      agentId: 'agent-01',
      conversationId: 'session-01',
    });
    expect(computeUrl(route)).toBe('/agents/agent-01/sessions/session-01');
  });
});

describe('Cron routes', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/');
  });

  it('tracks a profile-scoped Cron job in the URL', () => {
    const path = '/cron/jobs/job-01?agent=agent-01';
    const route = parseRoute('/cron/jobs/job-01', '?agent=agent-01');
    expect(route).toMatchObject({
      centerView: 'cron',
      cronJobId: 'job-01',
      cronAgentId: 'agent-01',
    });
    expect(computeUrl({ ...state('profiles'), ...route })).toBe(path);
  });

  it('updates and restores Cron detail state on popstate', async () => {
    const { result } = renderHook(() => useRouter());

    act(() => result.current.openCronJob('job-01', 'agent-01'));
    await waitFor(() => expect(window.location.href).toContain('/cron/jobs/job-01?agent=agent-01'));

    act(() => {
      window.history.replaceState(null, '', '/cron/jobs/job-02?agent=agent-02');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.centerView).toBe('cron');
    expect(result.current.cronJobId).toBe('job-02');
    expect(result.current.cronAgentId).toBe('agent-02');
  });
});
