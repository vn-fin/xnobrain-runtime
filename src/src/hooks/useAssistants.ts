import { useCallback, useEffect, useMemo, useState } from 'react';
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

/** Picks a conversation title that doesn't collide with existing ones, since
 * the backend rejects duplicate titles for an agent with a 409 conflict. */
function uniqueConversationTitle(existing: string[], base = 'New conversation'): string {
  const taken = new Set(existing.map((title) => title.trim().toLowerCase()));
  if (!taken.has(base.toLowerCase())) return base;
  for (let i = 2; i < 1000; i += 1) {
    const candidate = `${base} ${i}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
  return `${base} ${Date.now()}`;
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
    skillsWriteApproval: config.skills_write_approval ?? nestedWriteApproval(config, 'skills') ?? true,
    memoryWriteApproval: config.memory_write_approval ?? nestedWriteApproval(config, 'memory') ?? true,
  };
}

export function useAssistants() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [library, setLibrary] = useState<AgentSkill[]>([]);
  const [agentSkills, setAgentSkills] = useState<AgentSkillMap>({});
  const [skillStates, setSkillStates] = useState<SkillStateMap>({});
  const [agentSkillPages, setAgentSkillPages] = useState<Record<string, ResponsePagination>>({});
  const [defaultConfig, setDefaultConfig] = useState<GlobalRuntimeConfig | null>(null);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const [skillInstallPending, setSkillInstallPending] = useState(false);
  const [skillInstallError, setSkillInstallError] = useState('');

  const setComposedAgents = useCallback((next: Agent[]) => {
    const derived = deriveSkills(next);
    setAgents(next);
    setAgentSkills(derived.enabled);
    setSkillStates(derived.states);
  }, []);

  const refresh = useCallback(async () => {
    setStatus('loading');
    setError('');
    try {
      const [baseAgents, defaultSkillsPage, globalConfig] = await Promise.all([
        agentsApi.list(),
        skillsApi.listDefault(),
        agentsApi.getGlobalConfig().catch(() => null),
      ]);
      setLibrary(defaultSkillsPage.skills);
      setDefaultConfig(mapGlobalConfig(globalConfig));
      const pages: Record<string, ResponsePagination> = {};
      const composed = await Promise.all(baseAgents.map(async (agent) => {
        const [conversations, skillsPage] = await Promise.all([
          conversationsApi.list(agent.id),
          skillsApi.list(agent.id),
        ]);
        if (skillsPage.pagination) pages[agent.id] = skillsPage.pagination;
        return { ...agent, conversations, skills: skillsPage.skills };
      }));
      setComposedAgents(composed);
      setAgentSkillPages(pages);
      setStatus('ready');
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not load assistants.');
      setStatus('error');
    }
  }, [setComposedAgents]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const createAgent = async (name: string, description: string) => {
    setPending(true);
    setError('');
    const agent = await agentsApi.create(name, description);
    let conversationId = '';
    let conversations: Agent['conversations'] = [];
    let profileSkills: AgentSkill[] = [];
    try {
      const conversation = await conversationsApi.create(agent.id, 'New conversation');
      conversationId = conversation.id;
      conversations = [conversation];
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Agent created, but its first conversation could not be created.');
    }
    try {
      profileSkills = (await skillsApi.list(agent.id)).skills;
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Agent created, but its skills could not be loaded.');
    } finally {
      setPending(false);
    }
    setComposedAgents([...agents, { ...agent, conversations, skills: profileSkills }]);
    return { agentId: agent.id, conversationId };
  };

  const updateAgent = async (id: string, updates: Partial<Agent>) => {
    setPending(true);
    setError('');
    try {
      const updated = await agentsApi.update(id, updates);
      setComposedAgents(agents.map((agent) => agent.id === id
        ? { ...updated, conversations: agent.conversations, skills: agent.skills }
        : agent));
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not update assistant.');
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
      setComposedAgents(agents.map((agent) => agent.id === id
        ? { ...updated, conversations: agent.conversations, skills: agent.skills }
        : agent));
    } catch (value) {
      setError(value instanceof Error ? value.message : 'Could not rename assistant.');
      throw value;
    } finally {
      setPending(false);
    }
  };

  const deleteAgent = async (id: string) => {
    setPending(true);
    try {
      await agentsApi.remove(id);
      const remaining = agents.filter((agent) => agent.id !== id);
      setComposedAgents(remaining);
      return remaining[0] ? { agentId: remaining[0].id, conversationId: remaining[0].conversations[0]?.id ?? '' } : null;
    } finally {
      setPending(false);
    }
  };

  const testAgent = (id: string) => agentsApi.test(id);

  // Set the global (default) provider + model used as the template for new
  // agents, via PATCH /agent-gateway/v1/agents-configs/global.
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
    setComposedAgents(agents.map((agent) => agent.id === agentId
      ? { ...updated, conversations: agent.conversations, skills: agent.skills }
      : agent));
  };

  const createConversation = async (agentId: string, _model?: string) => {
    const existing = agents.find((agent) => agent.id === agentId)?.conversations ?? [];
    const title = uniqueConversationTitle(existing.map((conversation) => conversation.title));
    let conversation;
    try {
      conversation = await conversationsApi.create(agentId, title);
    } catch (error) {
      // The backend returns 409 when the title already exists (e.g. a race with
      // another tab, or a stale local list). Retry once with a unique suffix.
      if (error instanceof ApiError && error.status === 409) {
        conversation = await conversationsApi.create(agentId, `${title} · ${new Date().toLocaleTimeString()}`);
      } else {
        throw error;
      }
    }
    const conversations = await conversationsApi.list(agentId);
    setComposedAgents(agents.map((agent) => agent.id === agentId
      ? { ...agent, conversations }
      : agent));
    return conversation.id;
  };

  const renameConversation = async (agentId: string, conversationId: string, title: string) => {
    const clean = title.trim();
    if (!clean) return;
    const updated = await conversationsApi.rename(agentId, conversationId, clean);
    setComposedAgents(agents.map((agent) => agent.id === agentId
      ? {
          ...agent,
          conversations: agent.conversations.map((conversation) =>
            conversation.id === conversationId ? { ...conversation, title: updated.title || clean } : conversation),
        }
      : agent));
  };

  const deleteConversation = async (agentId: string, conversationId: string) => {
    await conversationsApi.remove(agentId, conversationId);
    let nextId: string | null = null;
    setComposedAgents(agents.map((agent) => {
      if (agent.id !== agentId) return agent;
      const conversations = agent.conversations.filter((item) => item.id !== conversationId);
      nextId = conversations[0]?.id ?? null;
      return { ...agent, conversations };
    }));
    return nextId;
  };

  // Explicitly enable/disable an installed skill for an agent via
  // PATCH /agent-gateway/v1/agents-skills/{agent_id}/{skill_id}. Optimistically
  // reflects the new state so the toggle feels instant, then reconciles.
  const setSkillEnabled = async (agentId: string, skillId: string, enabled: boolean) => {
    const previous = agents;
    const optimistic = agents.map((agent) => agent.id === agentId
      ? { ...agent, skills: agent.skills.map((skill) => skill.skill_id === skillId ? { ...skill, enabled } : skill) }
      : agent);
    setComposedAgents(optimistic);
    try {
      await skillsApi.setEnabled(agentId, skillId, enabled);
      const currentPage = agentSkillPages[agentId]?.page;
      const { skills, pagination } = await skillsApi.list(agentId, currentPage);
      setComposedAgents(optimistic.map((agent) => agent.id === agentId ? { ...agent, skills } : agent));
      if (pagination) {
        setAgentSkillPages((current) => ({ ...current, [agentId]: pagination }));
      }
    } catch (value) {
      setComposedAgents(previous);
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

    const previous = agents;
    try {
      await skillsApi.install(agentId, {
        skill_id: skill.skill_id,
        name: skill.name,
        category: skill.category,
        enable: true,
      });
      const currentPage = agentSkillPages[agentId]?.page;
      const { skills, pagination } = await skillsApi.list(agentId, currentPage);
      setComposedAgents(agents.map((agent) => agent.id === agentId ? { ...agent, skills } : agent));
      if (pagination) {
        setAgentSkillPages((current) => ({ ...current, [agentId]: pagination }));
      }
    } catch (value) {
      setComposedAgents(previous);
      setError(value instanceof Error ? value.message : 'Could not update skill.');
      throw value;
    }
  };

  // Load a specific server page of an agent's skills (only used when the API
  // reports more than one page via its pagination block).
  const loadSkillsPage = async (agentId: string, page: number) => {
    const { skills, pagination } = await skillsApi.list(agentId, page);
    setComposedAgents(agents.map((agent) => agent.id === agentId ? { ...agent, skills } : agent));
    setAgentSkillPages((prev) => ({ ...prev, [agentId]: pagination ?? prev[agentId] }));
  };

  const installDefaultSkill = async (source: string) => {
    const clean = source.trim();
    if (!clean) return false;
    setSkillInstallPending(true);
    setSkillInstallError('');
    try {
      await skillsApi.installDefault({ source: clean, enable: true });
      await refresh();
      return true;
    } catch (value) {
      setSkillInstallError(value instanceof Error ? value.message : 'Could not install skill.');
      return false;
    } finally {
      setSkillInstallPending(false);
    }
  };

  const installExistingSkill = async (skillId: string, agentIds: string[] = []) => {
    const skill = library.find((item) => item.skill_id === skillId);
    if (!skill) return [];
    const results = await Promise.allSettled(agentIds.map((agentId) =>
      skillsApi.install(agentId, { skill_id: skill.skill_id, name: skill.name, category: skill.category, enable: true })));
    await refresh();
    return results;
  };

  const applySkillsToAgents = async (skillIds: string[], agentIds: string[]) => {
    const operations = agentIds.flatMap((agentId) => skillIds.map(async (skillId) => {
      const state = skillStates[skillId]?.[agentId];
      const skill = library.find((item) => item.skill_id === skillId);
      if (!skill) throw new Error(`Unknown skill: ${skillId}`);
      if (state?.installed) return skillsApi.setEnabled(agentId, skillId, true);
      return skillsApi.install(agentId, { skill_id: skillId, name: skill.name, category: skill.category, enable: true });
    }));
    const results = await Promise.allSettled(operations);
    await refresh();
    return results;
  };

  return {
    agents, library, agentSkills, skillStates, agentSkillPages, defaultConfig, status, error, pending,
    skillInstallPending, skillInstallError, refresh,
    createAgent, updateAgent, renameAgent, deleteAgent, testAgent, setDefaultModel, setWriteApprovals,
    createConversation, deleteConversation, renameConversation,
    toggleAgentSkill, setSkillEnabled, loadSkillsPage, installDefaultSkill, installExistingSkill, applySkillsToAgents,
  };
}

export function useActiveAgent(agents: Agent[], activeAgentId: string) {
  return useMemo(() => agents.find((agent) => agent.id === activeAgentId) ?? agents[0], [agents, activeAgentId]);
}
