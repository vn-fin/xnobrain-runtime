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
  provider: 'nine-router',
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
  listConversations: vi.fn(),
  createConversation: vi.fn(),
  listSkills: vi.fn(),
  listDefaultSkills: vi.fn(),
  getGlobalConfig: vi.fn(),
}));

vi.mock('../api/agents', () => ({
  agentsApi: {
    list: mocks.listAgents,
    getGlobalConfig: mocks.getGlobalConfig,
  },
}));

vi.mock('../api/conversations', () => ({
  conversationsApi: {
    list: mocks.listConversations,
    create: mocks.createConversation,
  },
}));

vi.mock('../api/skills', () => ({
  skillsApi: {
    list: mocks.listSkills,
    listDefault: mocks.listDefaultSkills,
  },
}));

describe('useAssistants lazy collections', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads agent summaries once under StrictMode and fetches selected collections on demand', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.listConversations.mockResolvedValue([
      { id: 'conversation-one', title: 'New Conversation', updated: 'now', messages: 0 },
    ]);
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
    expect(mocks.listConversations).toHaveBeenCalledWith('agent-one');
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

  it('opens a conversation committed by an older backend before it returned 409', async () => {
    mocks.listAgents.mockResolvedValue(agents);
    mocks.createConversation.mockRejectedValue(
      new ApiError(409, 'conversation already exists'),
    );
    mocks.listConversations
      .mockResolvedValueOnce([
        { id: 'existing', title: 'New Conversation', updated: 'now', messages: 0 },
      ])
      .mockResolvedValueOnce([
        { id: 'existing', title: 'New Conversation', updated: 'now', messages: 0 },
        { id: 'recovered', title: 'New Conversation 2', updated: 'now', messages: 0 },
      ]);

    const { result } = renderHook(() => useAssistants());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    await act(() => result.current.loadConversations('agent-one'));

    mocks.listConversations.mockClear();
    let id = '';
    await act(async () => {
      id = await result.current.createConversation('agent-one');
    });

    expect(id).toBe('recovered');
    expect(mocks.listConversations).toHaveBeenCalledWith('agent-one');
    expect(result.current.agents[0].conversations).toHaveLength(2);
  });
});
