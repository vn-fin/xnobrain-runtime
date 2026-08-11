import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { agentsApi } from '../api/agents';
import { conversationsApi } from '../api/conversations';
import { skillsApi } from '../api/skills';
import { ApiError, type ResponsePagination } from '../api/client';
import type { AgentConfigDTO } from '../api/contracts/agentGateway';
import type { Agent, AgentSkill, AgentSkillMap, AsyncStatus, GlobalRuntimeConfig, SkillStateMap } from '../types';

function deriveSkills(agents: Agent[]) {
  const enabled: AgentSkillMap = {};
  const states: SkillStateMap = {};
  for (const agent of agents) {
    enabled[agent.id] = {};
    for (const skill of agent.skills) {
      enabled[agent.id][skill.skill_id] = skill.enabled;
      states[skill.skill_id] = {
        ...(states[skill.skill_id] ?? {}),
        [agent.id]: { installed: skill.installed, enabled: skill.enabled },
      };
    }
  }
  return { enabled, states };
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function nestedWriteApproval(config: AgentConfigDTO, section: 'skills' | 'memory'): boolean | undefined {
  const nested = objectValue(objectValue(config.config)[section]);
  return typeof nested.write_approval === 'boolean' ? nested.write_approval : undefined;
}

function mapGlobalConfig(config: AgentConfigDTO | null): GlobalRuntimeConfig | null {
  if (!config) return null;
  return {
    provider: config.provider ?? '',
    model: config.model ?? '',
    skillsWriteApproval: config.skills_write_approval ?? nestedWriteApproval(config, 'skills') ?? false,
    memoryWriteApproval: config.memory_write_approval ?? nestedWriteApproval(config, 'memory') ?? false,
  };
}

const CONVERSATION_PAGE_SIZE = 50;

function recentConversations(conversations: Agent['conversations']): Agent['conversations'] {
  return [...conversations].sort((left, right) =>
    (right.updatedAt ?? (Date.parse(right.startedAt) || 0))
    - (left.updatedAt ?? (Date.parse(left.startedAt) || 0)));
}

function mergeConversations(
  current: Agent['conversations'],
  incoming: Agent['conversations'],
): Agent['conversations'] {
  const conversations = new Map(current.map((conversation) => [conversation.id, conversation]));
  for (const conversation of incoming) conversations.set(conversation.id, conversation);
  return recentConversations([...conversations.values()]);
}

export function useAssistants(active = true) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [library, setLibrary] = useState<AgentSkill[]>([]);
  const [agentSkillPages, setAgentSkillPages] = useState<Record<string, ResponsePagination>>({});
  const [conversationPages, setConversationPages] = useState<Record<string, { page: number; limit: number; hasMore: boolean }>>({});
  const [defaultConfig, setDefaultConfig] = useState<GlobalRuntimeConfig | null>(null);
  const [status, setStatus] = useState<AsyncStatus>(active ? 'loading' : 'ready');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [skillInstallPending, setSkillInstallPending] = useState(false);
  const [skillInstallError, setSkillInstallError] = useState('');
  const [skillsOverviewLoading, setSkillsOverviewLoading] = useState(false);
  const agentsRef = useRef(agents);
  const loadedConversations = useRef(new Set<string>());
  const conversationRequests = useRef(new Map<string, Promise<Agent['conversations']>>());
  const conversationPageRequests = useRef(new Map<string, Promise<Agent['conversations']>>());
  const conversationPagesRef = useRef(conversationPages);
  const loadedSkills = useRef(new Set<string>());
  const skillRequests = useRef(new Map<string, Promise<AgentSkill[]>>());
  const skillsOverviewRequest = useRef<Promise<AgentSkill[]> | null>(null);
  const libraryRequest = useRef<Promise<AgentSkill[]> | null>(null);
  const libraryLoaded = useRef(false);
  const libraryRef = useRef(library);
  const defaultConfigRequest = useRef<Promise<GlobalRuntimeConfig | null> | null>(null);
  const defaultConfigLoaded = useRef(false);
  const initialLoadStarted = useRef(false);

  agentsRef.current = agents;
  libraryRef.current = library;
  conversationPagesRef.current = conversationPages;
  const { enabled: agentSkills, states: skillStates } = useMemo(() => deriveSkills(agents), [agents]);

  const refresh = useCallback(async () => {
    const isInitialLoad = agentsRef.current.length === 0;
    if (isInitialLoad) setStatus('loading');
    setError('');
    try {
      const baseAgents = await agentsApi.list();
      setAgents((current) => baseAgents.map((agent) => {
        const cached = current.find((item) => item.id === agent.id);
        return {
          ...agent,
          conversations: cached?.conversations ?? [],
          skills: cached?.skills ?? [],
        };
      }));
      setStatus('ready');
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not load agents.');
      setStatus(isInitialLoad ? 'error' : 'ready');
    }
  }, []);

  useEffect(() => {
    if (!active || initialLoadStarted.current) return;
    initialLoadStarted.current = true;
    void refresh();
  }, [active, refresh]);

  const loadConversations = useCallback(async (agentId: string, force = false) => {
    if (!agentId) return [];
    if (!force && loadedConversations.current.has(agentId)) {
      return agentsRef.current.find((agent) => agent.id === agentId)?.conversations ?? [];
    }
    const pendingRequest = conversationRequests.current.get(agentId);
    if (pendingRequest && !force) return pendingRequest;

    const request = conversationsApi.list(agentId, 1, CONVERSATION_PAGE_SIZE)
      .then(({ conversations, pagination }) => {
        const sorted = recentConversations(conversations);
        loadedConversations.current.add(agentId);
        setAgents((current) => current.map((agent) =>
          agent.id === agentId ? { ...agent, conversations: sorted } : agent));
        setConversationPages((current) => ({ ...current, [agentId]: pagination }));
        return sorted;
      })
      .finally(() => {
        if (conversationRequests.current.get(agentId) === request) {
          conversationRequests.current.delete(agentId);
        }
      });
    conversationRequests.current.set(agentId, request);
    return request;
  }, []);

  const loadMoreConversations = useCallback(async (agentId: string) => {
    if (!agentId) return [];
    const pagination = conversationPagesRef.current[agentId];
    const existing = agentsRef.current.find((agent) => agent.id === agentId)?.conversations ?? [];
    if (!pagination?.hasMore) return existing;
    const pendingRequest = conversationPageRequests.current.get(agentId);
    if (pendingRequest) return pendingRequest;

    const request = conversationsApi.list(agentId, pagination.page + 1, pagination.limit)
      .then(({ conversations, pagination: nextPage }) => {
        let merged = existing;
        setAgents((current) => current.map((agent) => {
          if (agent.id !== agentId) return agent;
          merged = mergeConversations(agent.conversations, conversations);
          return { ...agent, conversations: merged };
        }));
        setConversationPages((current) => ({ ...current, [agentId]: nextPage }));
        return merged;
      })
      .finally(() => {
        if (conversationPageRequests.current.get(agentId) === request) {
          conversationPageRequests.current.delete(agentId);
        }
      });
    conversationPageRequests.current.set(agentId, request);
    return request;
  }, []);

  const loadConversation = useCallback(async (agentId: string, conversationId: string) => {
    const existing = agentsRef.current.find((agent) => agent.id === agentId)
      ?.conversations.find((conversation) => conversation.id === conversationId);
    if (existing) return existing;
    const conversation = await conversationsApi.detail(agentId, conversationId);
    setAgents((current) => current.map((agent) => agent.id === agentId
      ? { ...agent, conversations: mergeConversations(agent.conversations, [conversation]) }
      : agent));
    return conversation;
  }, []);

  const loadAgentSkills = useCallback(async (agentId: string, force = false) => {
    if (!agentId) return [];
    if (!force && loadedSkills.current.has(agentId)) {
      return agentsRef.current.find((agent) => agent.id === agentId)?.skills ?? [];
    }
    const pendingRequest = skillRequests.current.get(agentId);
    if (pendingRequest) return pendingRequest;

    const request = skillsApi.list(agentId)
      .then(({ skills, pagination }) => {
        loadedSkills.current.add(agentId);
        setAgents((current) => current.map((agent) =>
          agent.id === agentId ? { ...agent, skills } : agent));
        if (pagination) {
          setAgentSkillPages((current) => ({ ...current, [agentId]: pagination }));
        }
        return skills;
      })
      .finally(() => {
        if (skillRequests.current.get(agentId) === request) {
          skillRequests.current.delete(agentId);
        }
      });
    skillRequests.current.set(agentId, request);
    return request;
  }, []);

  const loadLibrary = useCallback(async (force = false) => {
    if (libraryRequest.current) return libraryRequest.current;
    if (!force && libraryLoaded.current) return libraryRef.current;
    const request = skillsApi.listDefault()
      .then((page) => {
        libraryLoaded.current = true;
        setLibrary(page.skills);
        return page.skills;
      })
      .finally(() => {
        if (libraryRequest.current === request) libraryRequest.current = null;
      });
    libraryRequest.current = request;
    return request;
  }, []);

  const loadSkillsOverview = useCallback(async () => {
    if (skillsOverviewRequest.current) return skillsOverviewRequest.current;
    setSkillsOverviewLoading(true);
    const request = skillsApi.listOverview()
      .then((overview) => {
        libraryLoaded.current = true;
        setLibrary(overview.skills);
        setAgents((current) => current.map((agent) => {
          const skills = overview.agents[agent.id];
          return skills ? { ...agent, skills } : agent;
        }));
        return overview.skills;
      })
      .finally(() => {
        if (skillsOverviewRequest.current === request) {
          skillsOverviewRequest.current = null;
        }
        setSkillsOverviewLoading(false);
      });
    skillsOverviewRequest.current = request;
    return request;
  }, []);

  const loadDefaultConfig = useCallback(async (force = false) => {
    if (!force && defaultConfigLoaded.current) return defaultConfig;
    if (defaultConfigRequest.current && !force) return defaultConfigRequest.current;
    const request = agentsApi.getGlobalConfig()
      .then(mapGlobalConfig)
      .catch(() => null)
      .then((config) => {
        defaultConfigLoaded.current = true;
        setDefaultConfig(config);
        return config;
      })
      .finally(() => {
        if (defaultConfigRequest.current === request) defaultConfigRequest.current = null;
      });
    defaultConfigRequest.current = request;
    return request;
  }, [defaultConfig]);

  const createAgent = async (name: string, description: string) => {
    setPending(true);
    setError('');
    const agent = await agentsApi.create(name, description);
    let conversationId = '';
    let conversations: Agent['conversations'] = [];
    try {
      const conversation = await conversationsApi.create(agent.id);
      conversationId = conversation.id;
      conversations = [conversation];
      loadedConversations.current.add(agent.id);
      setConversationPages((current) => ({
        ...current,
        [agent.id]: { page: 1, limit: CONVERSATION_PAGE_SIZE, hasMore: false },
      }));
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Agent created, but its first session could not be created.');
    } finally {
      setPending(false);
    }
    setAgents((current) => [...current, { ...agent, conversations, skills: [] }]);
    return { agentId: agent.id, conversationId };
  };

  const updateAgent = async (id: string, updates: Partial<Agent>) => {
    setPending(true);
    setError('');
    try {
      const updated = await agentsApi.update(id, updates);
      setAgents((current) => current.map((agent) => agent.id === id
        ? { ...updated, conversations: agent.conversations, skills: agent.skills }
        : agent));
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not update agent.');
      throw value;
    } finally {
      setPending(false);
    }
  };

  const renameAgent = async (id: string, displayName: string) => {
    const clean = displayName.trim();
    if (!clean) return;
    setPending(true);
    setError('');
    try {
      const updated = await agentsApi.rename(id, clean);
      setAgents((current) => current.map((agent) => agent.id === id
        ? { ...updated, conversations: agent.conversations, skills: agent.skills }
        : agent));
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not rename agent.');
      throw value;
    } finally {
      setPending(false);
    }
  };

  const deleteAgent = async (id: string) => {
    setPending(true);
    try {
      await agentsApi.remove(id);
      const remaining = agentsRef.current.filter((agent) => agent.id !== id);
      loadedConversations.current.delete(id);
      loadedSkills.current.delete(id);
      setAgents(remaining);
      return remaining[0] ? { agentId: remaining[0].id, conversationId: remaining[0].conversations[0]?.id ?? '' } : null;
    } finally {
      setPending(false);
    }
  };

  const testAgent = (id: string) => agentsApi.test(id);

  // Set the global (default) provider + model used as the template for new
  // agents, via PATCH /xnobrain/api/runtime/v1/agents-configs/global.
  const setDefaultModel = async (provider: string, model: string) => {
    const updated = await agentsApi.updateGlobalConfig({ provider, model });
    setDefaultConfig(mapGlobalConfig(updated) ?? {
      provider,
      model,
      skillsWriteApproval: defaultConfig?.skillsWriteApproval ?? false,
      memoryWriteApproval: defaultConfig?.memoryWriteApproval ?? false,
    });
  };

  const setWriteApprovals = async (agentId: string, updates: Partial<Pick<GlobalRuntimeConfig, 'skillsWriteApproval' | 'memoryWriteApproval'>>) => {
    const payload: AgentConfigDTO = {};
    if (updates.skillsWriteApproval !== undefined) payload.skills_write_approval = updates.skillsWriteApproval;
    if (updates.memoryWriteApproval !== undefined) payload.memory_write_approval = updates.memoryWriteApproval;
    await agentsApi.updateConfig(agentId, payload);
    const updated = await agentsApi.detail(agentId);
    setAgents((current) => current.map((agent) => agent.id === agentId
      ? { ...updated, conversations: agent.conversations, skills: agent.skills }
      : agent));
  };

  const createConversation = async (agentId: string, _model?: string) => {
    const previousIds = new Set(
      agentsRef.current.find((agent) => agent.id === agentId)?.conversations.map((item) => item.id) ?? [],
    );
    let conversation: Agent['conversations'][number];
    try {
      conversation = await conversationsApi.create(agentId);
    } catch (value) {
      if (!(value instanceof ApiError) || value.status !== 409) throw value;
      const conversations = await loadConversations(agentId, true);
      const recovered = conversations.find((item) => !previousIds.has(item.id));
      if (!recovered) throw value;
      // Older running backends could commit the session before a title
      // conflict returned 409. Reconcile that successful write immediately so
      // the user can continue without refreshing the page.
      return recovered.id;
    }
    loadedConversations.current.add(agentId);
    setAgents((current) => current.map((agent) => agent.id === agentId
      ? { ...agent, conversations: mergeConversations(agent.conversations, [{ ...conversation, updatedAt: Date.now() }]) }
      : agent));
    return conversation.id;
  };

  const renameConversation = async (agentId: string, conversationId: string, title: string) => {
    const clean = title.trim();
    if (!clean) return;
    const updated = await conversationsApi.rename(agentId, conversationId, clean);
    setAgents((current) => current.map((agent) => agent.id === agentId
      ? {
          ...agent,
          conversations: agent.conversations.map((conversation) =>
            conversation.id === conversationId ? { ...conversation, title: updated.title || clean } : conversation),
        }
      : agent));
  };

  const setConversationTitle = (agentId: string, conversationId: string, title: string) => {
    const clean = title.trim();
    if (!clean) return;
    setAgents((current) => current.map((agent) => agent.id === agentId
      ? {
          ...agent,
          conversations: recentConversations(agent.conversations.map((conversation) =>
            conversation.id === conversationId
              ? { ...conversation, title: clean, updatedAt: Date.now() }
              : conversation)),
        }
      : agent));
  };

  const touchConversation = useCallback((agentId: string, conversationId: string) => {
    setAgents((current) => current.map((agent) => agent.id === agentId
      ? {
          ...agent,
          conversations: recentConversations(agent.conversations.map((conversation) =>
            conversation.id === conversationId
              ? { ...conversation, updatedAt: Date.now() }
              : conversation)),
        }
      : agent));
  }, []);

  const deleteConversation = async (agentId: string, conversationId: string) => {
    await conversationsApi.remove(agentId, conversationId);
    let nextId: string | null = null;
    setAgents((current) => current.map((agent) => {
      if (agent.id !== agentId) return agent;
      const conversations = agent.conversations.filter((item) => item.id !== conversationId);
      nextId = conversations[0]?.id ?? null;
      return { ...agent, conversations };
    }));
    return nextId;
  };

  // Explicitly enable/disable an installed skill for an agent via
  // PATCH /xnobrain/api/runtime/v1/agents-skills/{agent_id}/{skill_id}. Optimistically
  // reflects the new state so the toggle feels instant, then reconciles.
  const setSkillEnabled = async (agentId: string, skillId: string, enabled: boolean) => {
    const previous = agentsRef.current;
    const optimistic = previous.map((agent) => agent.id === agentId
      ? { ...agent, skills: agent.skills.map((skill) => skill.skill_id === skillId ? { ...skill, enabled } : skill) }
      : agent);
    setAgents(optimistic);
    try {
      await skillsApi.setEnabled(agentId, skillId, enabled);
      await loadAgentSkills(agentId, true);
    } catch (value) {
      setAgents(previous);
      setError(value instanceof Error ? value.message : 'Could not update skill.');
      throw value;
    }
  };

  const toggleAgentSkill = async (agentId: string, skillId: string) => {
    const state = skillStates[skillId]?.[agentId];
    const skill = library.find((item) => item.skill_id === skillId);
    if (!skill) return;
    if (state?.installed) {
      await setSkillEnabled(agentId, skillId, !state.enabled);
      return;
    }

    const previous = agentsRef.current;
    try {
      await skillsApi.install(agentId, {
        skill_id: skill.skill_id,
        name: skill.name,
        category: skill.category,
        enable: false,
      });
      await loadAgentSkills(agentId, true);
    } catch (value) {
      setAgents(previous);
      setError(value instanceof Error ? value.message : 'Could not update skill.');
      throw value;
    }
  };

  // Load a specific server page of an agent's skills (only used when the API
  // reports more than one page via its pagination block).
  const loadSkillsPage = async (agentId: string, page: number) => {
    const { skills, pagination } = await skillsApi.list(agentId, page);
    setAgents((current) => current.map((agent) => agent.id === agentId ? { ...agent, skills } : agent));
    setAgentSkillPages((prev) => ({ ...prev, [agentId]: pagination ?? prev[agentId] }));
  };

  const installDefaultSkill = async (source: string, force = false) => {
    const clean = source.trim();
    if (!clean) return false;
    setSkillInstallPending(true);
    setSkillInstallError('');
    try {
      await skillsApi.installDefault({ source: clean, enable: false, force });
      await loadLibrary(true);
      return true;
    } catch (value) {
      setSkillInstallError(value instanceof Error ? value.message : 'Could not install skill.');
      return false;
    } finally {
      setSkillInstallPending(false);
    }
  };

  const setDefaultSkillEnabled = async (skillId: string, enabled: boolean) => {
    const previous = library;
    setSkillInstallError('');
    setLibrary((current) => current.map((skill) =>
      skill.skill_id === skillId ? { ...skill, enabled } : skill));
    try {
      const skills = await skillsApi.setDefaultEnabled(skillId, enabled);
      setLibrary(skills);
      libraryLoaded.current = true;
      return true;
    } catch (value) {
      setLibrary(previous);
      setSkillInstallError(value instanceof Error ? value.message : 'Could not update default skill.');
      return false;
    }
  };

  const installExistingSkill = async (skillId: string, agentIds: string[] = []) => {
    const skill = library.find((item) => item.skill_id === skillId);
    if (!skill) return [];
    const results = await Promise.allSettled(agentIds.map((agentId) =>
      skillsApi.install(agentId, { skill_id: skill.skill_id, name: skill.name, category: skill.category, enable: false })));
    await Promise.all(agentIds.map((agentId) => loadAgentSkills(agentId, true)));
    return results;
  };

  const applySkillsToAgents = async (skillIds: string[], agentIds: string[]) => {
    const operations = agentIds.flatMap((agentId) => skillIds.map(async (skillId) => {
      const state = skillStates[skillId]?.[agentId];
      const skill = library.find((item) => item.skill_id === skillId);
      if (!skill) throw new Error(`Unknown skill: ${skillId}`);
      if (state?.installed) return skillsApi.setEnabled(agentId, skillId, true);
      return skillsApi.install(agentId, { skill_id: skillId, name: skill.name, category: skill.category, enable: false });
    }));
    const results = await Promise.allSettled(operations);
    await Promise.all(agentIds.map((agentId) => loadAgentSkills(agentId, true)));
    return results;
  };

  const previewSkillSync = (agentIds: string[]) => skillsApi.previewSync(agentIds);

  const syncAgentSkills = async (agentIds: string[], sourceRevision: string) => {
    const result = await skillsApi.sync(agentIds, sourceRevision);
    await loadSkillsOverview();
    return result;
  };

  return {
    agents, library, agentSkills, skillStates, agentSkillPages, conversationPages, defaultConfig, status, error, pending,
    skillInstallPending, skillInstallError, skillsOverviewLoading, refresh, loadConversations, loadMoreConversations, loadConversation, loadAgentSkills, loadLibrary, loadSkillsOverview, loadDefaultConfig,
    createAgent, updateAgent, renameAgent, deleteAgent, testAgent, setDefaultModel, setWriteApprovals,
    createConversation, deleteConversation, renameConversation, setConversationTitle, touchConversation,
    toggleAgentSkill, setSkillEnabled, loadSkillsPage, installDefaultSkill, setDefaultSkillEnabled, installExistingSkill, applySkillsToAgents, previewSkillSync, syncAgentSkills,
  };
}

export function useActiveAgent(agents: Agent[], activeAgentId: string) {
  return useMemo(() => agents.find((agent) => agent.id === activeAgentId) ?? agents[0], [agents, activeAgentId]);
}
