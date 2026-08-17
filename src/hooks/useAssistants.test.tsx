import { StrictMode, type PropsWithChildren } from 'react';
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api/client';
import type { Agent } from '../types';
import { useAssistants } from './useAssistants';

const agents: Agent[] = ['agent-one', 'agent-two'].map((id) => ({
  id,
  title: id,
  name: id,
  description: '',
  status: 'active',
  provider: 'xnobrain',
  model: 'auto',
  reasoningEffort: 'medium',
  approvalMode: 'on',
  skillsWriteApproval: true,
  memoryWriteApproval: true,
  workspace: '',
  conversations: [],
  skills: [],
}));

const mocks = vi.hoisted(() => ({
  listAgents: vi.fn(),
  agentActivity: vi.fn(),
  listConversations: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  listSkills: vi.fn(),
  listDefaultSkills: vi.fn(),
  listSkillsOverview: vi.fn(),
  getGlobalConfig: vi.fn(),
}));

const conversationPage = (conversations: unknown[], page = 1, hasMore = false) => ({
  conversations,
  pagination: { page, limit: 50, hasMore },
});

vi.mock('../api/agents', () => ({
  agentsApi: {
    list: mocks.listAgents,
    activity: mocks.agentActivity,
    getGlobalConfig: mocks.getGlobalConfig,
  },
}));

vi.mock('../api/conversations', () => ({
  conversationsApi: {
    list: mocks.listConversations,
    detail: mocks.getConversation,
    create: mocks.createConversation,
  },
}));

vi.mock('../api/skills', () => ({
  skillsApi: {
    list: mocks.listSkills,
    listDefault: mocks.listDefaultSkills,
    listOverview: mocks.listSkillsOverview,
  },
}));

describe('useAssistants lazy collections', () => {
  afterEach(() => vi.clearAllMocks());

  it('updates agent runtime activity independently from profile status', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.agentActivity.mockResolvedValue({ 'agent-one': 'running', 'agent-two': 'idle' });

    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await waitFor(() => expect(result.current.agents[0].runtimeStatus).toBe('running'));

    expect(result.current.agents[1].runtimeStatus).toBe('idle');
  });

  it('loads agent summaries once under StrictMode and fetches selected collections on demand', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.listConversations.mockResolvedValue(conversationPage([
      { id: 'conversation-one', title: 'New Conversation', updated: 'now', messages: 0 },
    ]));
    mocks.listSkills.mockResolvedValue({ skills: [], pagination: undefined });
    mocks.listDefaultSkills.mockResolvedValue({ skills: [], pagination: undefined });
    mocks.getGlobalConfig.mockResolvedValue(null);

    const wrapper = ({ children }: PropsWithChildren) => <StrictMode>{children}</StrictMode>;
    const { result } = renderHook(() => useAssistants(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe('ready'));

    expect(mocks.listAgents).toHaveBeenCalledTimes(1);
    expect(mocks.listConversations).not.toHaveBeenCalled();
    expect(mocks.listSkills).not.toHaveBeenCalled();
    expect(mocks.listDefaultSkills).not.toHaveBeenCalled();
    expect(mocks.getGlobalConfig).not.toHaveBeenCalled();

    await act(() => result.current.loadConversations('agent-one'));

    expect(mocks.listConversations).toHaveBeenCalledTimes(1);
    expect(mocks.listConversations).toHaveBeenCalledWith('agent-one', 1, 50);
    expect(result.current.agents[0].conversations[0]?.id).toBe('conversation-one');
    expect(result.current.agents[1].conversations).toEqual([]);

    await act(() => result.current.loadAgentSkills('agent-one'));
    expect(mocks.listSkills).toHaveBeenCalledTimes(1);
    expect(mocks.listSkills).toHaveBeenCalledWith('agent-one');
  });

  it('deduplicates a forced library refresh but allows the next tab entry to reload it', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.listDefaultSkills.mockResolvedValue({ skills: [], pagination: undefined });

    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));

    await act(async () => {
      await Promise.all([
        result.current.loadLibrary(true),
        result.current.loadLibrary(true),
      ]);
    });
    expect(mocks.listDefaultSkills).toHaveBeenCalledTimes(1);

    await act(() => result.current.loadLibrary(true));
    expect(mocks.listDefaultSkills).toHaveBeenCalledTimes(2);
  });

  it('hydrates the library and all agent skills from one overview request', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.listSkillsOverview.mockResolvedValue({
      skills: [{ skill_id: 'shared', name: 'Shared', enabled: true, installed: true }],
      agents: {
        'agent-one': [{ skill_id: 'shared', name: 'Shared', enabled: true, installed: true }],
        'agent-two': [{ skill_id: 'worker', name: 'Worker', enabled: false, installed: true }],
      },
    });

    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(() => result.current.loadSkillsOverview());

    expect(mocks.listSkillsOverview).toHaveBeenCalledTimes(1);
    expect(mocks.listDefaultSkills).not.toHaveBeenCalled();
    expect(mocks.listSkills).not.toHaveBeenCalled();
    expect(result.current.library.map((skill) => skill.skill_id)).toEqual(['shared']);
    expect(result.current.agents[0].skills.map((skill) => skill.skill_id)).toEqual(['shared']);
    expect(result.current.agents[1].skills.map((skill) => skill.skill_id)).toEqual(['worker']);

    await act(() => result.current.loadSkillsOverview());
    expect(mocks.listSkillsOverview).toHaveBeenCalledTimes(2);

    mocks.listSkills.mockResolvedValue({
      skills: [{ skill_id: 'runtime-added', name: 'Runtime added', enabled: true, installed: true }],
      pagination: undefined,
    });
    await act(() => result.current.loadAgentSkills('agent-one'));
    expect(mocks.listSkills).toHaveBeenCalledWith('agent-one');
    expect(result.current.agents[0].skills.map((skill) => skill.skill_id)).toEqual(['runtime-added']);
  });

  it('keeps the populated workspace mounted during a background profile refresh', async () => {
    mocks.listAgents.mockResolvedValueOnce(agents);
    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));

    let resolveRefresh!: (value: Agent[]) => void;
    mocks.listAgents.mockImplementationOnce(() => new Promise((resolve) => { resolveRefresh = resolve; }));
    let pending!: Promise<void>;
    act(() => { pending = result.current.refresh(); });

    expect(result.current.status).toBe('ready');
    expect(result.current.agents).toHaveLength(2);
    await act(async () => resolveRefresh(agents));
    await pending;
  });

  it('opens a conversation committed by an older backend before it returned 409', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.createConversation.mockRejectedValue(
      new ApiError(409, 'conversation already exists'),
    );
    mocks.listConversations
      .mockResolvedValueOnce(conversationPage([
        { id: 'existing', title: 'New Conversation', updated: 'now', messages: 0 },
      ]))
      .mockResolvedValueOnce(conversationPage([
        { id: 'existing', title: 'New Conversation', updated: 'now', messages: 0 },
        { id: 'recovered', title: 'New Conversation 2', updated: 'now', messages: 0 },
      ]));

    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(() => result.current.loadConversations('agent-one'));

    mocks.listConversations.mockClear();
    let id = '';
    await act(async () => {
      id = await result.current.createConversation('agent-one');
    });

    expect(id).toBe('recovered');
    expect(mocks.listConversations).toHaveBeenCalledWith('agent-one', 1, 50);
    expect(result.current.agents[0].conversations).toHaveLength(2);
  });

  it('reconciles a streamed conversation title without another API request', async () => {
    mocks.listAgents.mockResolvedValue([agents[0]]);
    mocks.listConversations.mockResolvedValue(conversationPage([
      { id: 'session-one', title: 'New Session', updated: 'now', messages: 0 },
    ]));
    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(() => result.current.loadConversations('agent-one'));
    mocks.listConversations.mockClear();

    act(() => result.current.setConversationTitle('agent-one', 'session-one', 'Market risk summary'));

    expect(result.current.agents[0].conversations[0].title).toBe('Market risk summary');
    expect(mocks.listConversations).not.toHaveBeenCalled();
  });

  it('sorts recent sessions first and appends the next API page once', async () => {
    mocks.listAgents.mockResolvedValue([agents[0]]);
    mocks.listConversations
      .mockResolvedValueOnce(conversationPage([
        { id: 'older', title: 'Older', startedAt: '2026-08-01T00:00:00Z', updatedAt: 1 },
        { id: 'newest', title: 'Newest', startedAt: '2026-08-03T00:00:00Z', updatedAt: 3 },
      ], 1, true))
      .mockResolvedValueOnce(conversationPage([
        { id: 'middle', title: 'Middle', startedAt: '2026-08-02T00:00:00Z', updatedAt: 2 },
      ], 2, false));

    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(() => result.current.loadConversations('agent-one'));

    expect(result.current.agents[0].conversations.map((item) => item.id)).toEqual(['newest', 'older']);
    await act(async () => {
      await Promise.all([
        result.current.loadMoreConversations('agent-one'),
        result.current.loadMoreConversations('agent-one'),
      ]);
    });

    expect(mocks.listConversations).toHaveBeenCalledTimes(2);
    expect(mocks.listConversations).toHaveBeenLastCalledWith('agent-one', 2, 50);
    expect(result.current.agents[0].conversations.map((item) => item.id)).toEqual(['newest', 'middle', 'older']);
    expect(result.current.conversationPages['agent-one'].hasMore).toBe(false);

    act(() => result.current.touchConversation('agent-one', 'older'));
    expect(result.current.agents[0].conversations.map((item) => item.id)).toEqual(['older', 'newest', 'middle']);
  });
});
