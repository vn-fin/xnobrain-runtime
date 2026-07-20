import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from './hooks/useRouter';
import { useAssistants, useActiveAgent } from './hooks/useAssistants';
import { useConnections } from './hooks/useConnections';
import { useSandbox } from './hooks/useSandbox';
import { useCrons } from './hooks/useCrons';
import { useConversation } from './hooks/useConversation';
import { useWorkspace } from './hooks/useWorkspace';
import { useTeams } from './hooks/useTeams';
import { streamStore, type CompletionEvent } from './chat/streamStore';
import { Sidebar } from './components/Sidebar';
import { ChatArea } from './components/ChatArea';
import { RightPanel } from './components/RightPanel';
import { SandboxView } from './components/SandboxView';
import { ConnectionsView } from './components/ConnectionsView';
import { SkillsView } from './components/SkillsView';
import { Onboarding } from './components/Onboarding';
import { SystemView } from './features/system/SystemView';
import { TeamsView } from './components/TeamsView';
import { AuthModal, CreateAgentModal, AgentSettingsModal, ConfirmDialog } from './components/modals';
import { AsyncState } from './components/AsyncState';
import type { Agent } from './types';

export default function App() {
  const { t } = useTranslation();
  const router = useRouter();
  const assistants = useAssistants();
  const connections = useConnections();
  const sandbox = useSandbox();
  const crons = useCrons();
  const conversation = useConversation(router.activeAgentId, router.activeConversationId);
  const workspace = useWorkspace(router.activeAgentId);
	const teams = useTeams();

  // Resizable right panel width (persisted). Applied as the --right grid column.
  const RIGHT_MIN = 280;
  const RIGHT_MAX = 720;
  const [rightWidth, setRightWidth] = useState(() => {
    const stored = Number(localStorage.getItem('rightPanelWidth'));
    return Number.isFinite(stored) && stored >= RIGHT_MIN ? Math.min(stored, RIGHT_MAX) : 330;
  });
  useEffect(() => { localStorage.setItem('rightPanelWidth', String(rightWidth)); }, [rightWidth]);
  const clampRightWidth = (width: number) => Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, Math.min(width, Math.round(window.innerWidth * 0.6))));

  const activeAgent = useActiveAgent(assistants.agents, router.activeAgentId);
  const activeConversation =
    activeAgent?.conversations.find((c) => c.id === router.activeConversationId) ?? activeAgent?.conversations[0];

  // UI-only modal state
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [deleteAgentId, setDeleteAgentId] = useState<string | null>(null);
  const [workspaceOpenRequest, setWorkspaceOpenRequest] = useState<{ path: string; token: number }>();
  const [deleteConversationId, setDeleteConversationId] = useState<string | null>(null);
  const [toasts, setToasts] = useState<Array<{ id: string; agentId: string; conversationId: string; title: string; status: CompletionEvent['status'] }>>([]);

  // Notify when a conversation the user is NOT currently viewing finishes.
  // Also refresh the open workspace directory whenever a conversation for the
  // active agent completes, since the assistant may have written new files.
  const workspaceRefreshRef = useRef(workspace.refresh);
  workspaceRefreshRef.current = workspace.refresh;
  const activeAgentIdRef = useRef(router.activeAgentId);
  activeAgentIdRef.current = router.activeAgentId;

  useEffect(() => {
    const unsubscribe = streamStore.onComplete((event) => {
      if (event.agentId === activeAgentIdRef.current) void workspaceRefreshRef.current();
      if (event.active) return;
      const agent = assistants.agents.find((a) => a.id === event.agentId);
      const convo = agent?.conversations.find((c) => c.id === event.conversationId);
      const id = crypto.randomUUID();
      setToasts((list) => [...list, { id, agentId: event.agentId, conversationId: event.conversationId, title: convo?.title || 'Conversation', status: event.status }]);
      window.setTimeout(() => setToasts((list) => list.filter((item) => item.id !== id)), 8000);
    });
    return () => { unsubscribe(); };
  }, [assistants.agents]);

  const dismissToast = (id: string) => setToasts((list) => list.filter((item) => item.id !== id));

  const handleOpenWorkspaceFile = (path: string) => {
    router.setRightView('workspace');
    setWorkspaceOpenRequest({ path, token: Date.now() });
  };

  const handleRenameConversation = (conversationId: string, title: string) =>
    assistants.renameConversation(router.activeAgentId, conversationId, title);

  useEffect(() => {
    if (assistants.status === 'ready') router.reconcileAgents(assistants.agents);
    // Reconcile only when the server-backed collection changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assistants.status, assistants.agents]);

  const { centerView } = router;
  const authProvider = connections.connections.find((p) => p.id === connections.authProviderId) ?? null;
  const runtimeProviders = useMemo(() => connections.connections.map((provider) => ({
    id: provider.id,
    display_name: provider.display_name,
    provider_type: provider.provider_type,
    connected: provider.connected,
    connection_mode: provider.connection_mode,
    default_model: provider.default_model ?? provider.available_models?.[0] ?? '',
    status: provider.status,
  })), [connections.connections]);

  // --- Coordinated actions (wire domain hooks to router navigation) ---
  const handleCreateAgent = async (name: string, description: string) => {
    const { agentId, conversationId } = await assistants.createAgent(name, description);
    router.openChat(agentId, conversationId);
    setCreateAgentOpen(false);
  };

  const handleDeleteAgent = async (id: string) => {
    const next = await assistants.deleteAgent(id);
    if (id === router.activeAgentId && next) router.openChat(next.agentId, next.conversationId);
    setDeleteAgentId(null);
  };

  const handleUpdateAgent = async (updates: Partial<Agent>) => {
    await assistants.updateAgent(router.activeAgentId, updates);
    setSettingsOpen(false);
  };

  const handleCreateConversation = async () => {
    const id = await assistants.createConversation(router.activeAgentId, activeAgent.model);
    router.setActiveConversationId(id);
  };

  const handleDeleteConversation = async (conversationId: string) => {
    const nextId = await assistants.deleteConversation(router.activeAgentId, conversationId);
    if (conversationId === router.activeConversationId) router.setActiveConversationId(nextId ?? '');
  };

  if (assistants.status === 'loading') return <AsyncState status="loading" />;
  if (assistants.status === 'error') return <AsyncState status="error" error={assistants.error} onRetry={assistants.refresh} />;
  if (!activeAgent) {
    return (
      <div className="empty-app">
        <Onboarding
          sandboxStatus={sandbox.status}
          sandboxProvisioned={sandbox.provisioned}
          setupRunning={sandbox.setupRunning}
          setupProgress={sandbox.setupProgress}
          sandboxError={sandbox.error}
          onCreateSandbox={sandbox.createSandbox}
          providers={connections.connections}
          providerPendingId={connections.pendingId}
          onStartConnect={connections.startConnect}
          onCheckConnect={connections.checkConnect}
          onSubmitConnectText={connections.submitAuth}
          onTestProvider={connections.test}
          onSaveKey={connections.saveKey}
          defaultConfig={assistants.defaultConfig}
          onLoadModels={connections.loadModels}
          onSaveDefaultModel={assistants.setDefaultModel}
          onCreateAgent={handleCreateAgent}
          creatingAgent={assistants.pending}
        />
      </div>
    );
  }

  return (
    <div
      className={centerView === 'chat' ? 'app' : 'app no-right'}
      style={centerView === 'chat' ? ({ '--right': `${rightWidth}px` } as CSSProperties) : undefined}
    >
      <Sidebar
        agents={assistants.agents}
        activeAgent={activeAgent}
        activeConversation={activeConversation}
        centerView={centerView}
        agentSearch={router.agentSearch}
        onAgentSearch={router.setAgentSearch}
        onNavigate={router.setCenterView}
        onSelectAgent={(agent) => router.openChat(agent.id, agent.conversations[0]?.id ?? '')}
        onSelectConversation={(id) => router.openChat(router.activeAgentId, id)}
        onRenameConversation={handleRenameConversation}
        onRequestDeleteConversation={(id) => setDeleteConversationId(id)}
        onNewAgent={() => setCreateAgentOpen(true)}
      />

      <main className={centerView === 'chat' ? 'chat-area' : 'chat-area sandbox-mode'}>
        {centerView === 'sandbox' ? (
          <SandboxView
            data={sandbox.data}
            provisioned={sandbox.provisioned}
            status={sandbox.status}
            error={sandbox.error}
            setupRunning={sandbox.setupRunning}
            setupProgress={sandbox.setupProgress}
            onCreate={sandbox.createSandbox}
            onRefresh={sandbox.refresh}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'connections' ? (
          <ConnectionsView
            providers={connections.connections}
            keyProviderId={connections.keyProviderId}
            pendingId={connections.pendingId}
            onSelectKeyProvider={connections.setKeyProviderId}
            onConnect={connections.connect}
            onDisconnect={connections.disconnect}
            onTest={connections.test}
            onSaveKey={connections.saveKey}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'skills' ? (
          <SkillsView
            library={assistants.library}
            agents={assistants.agents}
            agentSkills={assistants.agentSkills}
            search={router.skillsSearch}
            onSearch={router.setSkillsSearch}
            groupFilter={router.skillsGroupFilter}
            onGroupFilter={router.setSkillsGroupFilter}
            page={router.skillsPage}
            onPage={router.setSkillsPage}
            onInstall={assistants.installSkill}
            onInstallExisting={assistants.installExistingSkill}
            onApply={assistants.applySkillsToAgents}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'data' ? (
          <SystemView
            agents={assistants.agents}
            onImported={assistants.refresh}
            onClose={() => router.setCenterView('chat')}
          />
		) : centerView === 'teams' ? (
		  <TeamsView agents={assistants.agents} state={teams} onClose={() => router.setCenterView('chat')} />
        ) : (
          <ChatArea
            agent={activeAgent}
            activeConversation={activeConversation}
            providers={connections.connections}
            runs={conversation.runs}
            usage={conversation.usage}
            usageStatus={conversation.usageStatus}
            usageError={conversation.usageError}
            messages={conversation.messages}
            queuedMessages={conversation.queuedMessages}
            onEditQueued={conversation.editQueuedMessage}
            onDeleteQueued={conversation.removeQueuedMessage}
            onMoveQueued={conversation.moveQueuedMessage}
            chatStatus={conversation.status}
            chatError={conversation.error}
            streaming={conversation.streaming}
            canStop={conversation.canStop}
            onSend={conversation.sendMessage}
            onStop={conversation.stopStream}
            onResolveRunApproval={conversation.resolveRunApproval}
            onRetry={conversation.refresh}
            onRequestUsage={conversation.requestUsage}
            onSelectModel={(provider, model) => assistants.updateAgent(activeAgent.id, { provider, model })}
            onTestAgent={() => void assistants.testAgent(activeAgent.id)}
            onOpenSettings={() => setSettingsOpen(true)}
            onOpenRuntime={() => router.setRightView('runtime')}
            onDeleteAgent={() => setDeleteAgentId(activeAgent.id)}
            onSelectConversation={router.setActiveConversationId}
            onCreateConversation={handleCreateConversation}
            onDeleteConversation={(id) => setDeleteConversationId(id)}
            onRenameConversation={handleRenameConversation}
            onOpenFile={handleOpenWorkspaceFile}
            onUploadFiles={workspace.uploadFiles}
            onUploadTree={workspace.uploadTree}
            workspaceCwd={workspace.cwd}
          />
        )}
      </main>

      {centerView === 'chat' && (
        <RightPanel
          rightView={router.rightView}
          onRightView={router.setRightView}
          agent={activeAgent}
          library={assistants.library}
          agentSkills={assistants.agentSkills}
          providers={runtimeProviders}
          defaultConfig={{
            provider: activeAgent.provider,
            model: activeAgent.model,
            skillsWriteApproval: activeAgent.skillsWriteApproval,
            memoryWriteApproval: activeAgent.memoryWriteApproval,
          }}
          onToggleSkill={(skillId) => assistants.toggleAgentSkill(router.activeAgentId, skillId)}
          onSetSkillEnabled={(skillId, enabled) => assistants.setSkillEnabled(router.activeAgentId, skillId, enabled)}
          onUpdateWriteApprovals={(updates) => assistants.setWriteApprovals(activeAgent.id, updates)}
          skillsPagination={assistants.agentSkillPages[router.activeAgentId]}
          onLoadSkillsPage={(page) => assistants.loadSkillsPage(router.activeAgentId, page)}
          crons={crons.crons}
          onCreateCron={(input) => crons.createCron({ ...input, agentId: activeAgent.id })}
          onToggleCron={crons.toggleCron}
          onDeleteCron={crons.deleteCron}
          onCreateAgent={() => setCreateAgentOpen(true)}
          onOpenSettings={() => setSettingsOpen(true)}
          onDeleteAgent={() => setDeleteAgentId(activeAgent.id)}
          workspaceOpenRequest={workspaceOpenRequest}
          workspace={workspace}
          width={rightWidth}
          onResize={(width) => setRightWidth(clampRightWidth(width))}
        />
      )}

      {authProvider && connections.authInfo && (
        <AuthModal
          provider={authProvider}
          info={connections.authInfo}
          onSubmit={(code) => connections.submitAuth(authProvider.id, code)}
          onClose={connections.closeAuth}
        />
      )}

      {createAgentOpen && <CreateAgentModal onCreate={handleCreateAgent} onClose={() => setCreateAgentOpen(false)} />}

      {settingsOpen && (
        <AgentSettingsModal agent={activeAgent} providers={runtimeProviders} onSave={handleUpdateAgent} onClose={() => setSettingsOpen(false)} />
      )}

      {deleteConversationId && (
        <ConfirmDialog
          title={t('conversation.deleteTitle')}
          message={t('conversation.deleteMessage', {
            name: activeAgent.conversations.find((c) => c.id === deleteConversationId)?.title ?? 'conversation',
          })}
          confirmLabel={t('conversation.delete')}
          danger
          onConfirm={() => { void handleDeleteConversation(deleteConversationId); setDeleteConversationId(null); }}
          onCancel={() => setDeleteConversationId(null)}
        />
      )}

      {deleteAgentId && (
        <ConfirmDialog
          title={t('modals.deleteAgentTitle')}
          message={t('modals.deleteAgentMsg', { name: assistants.agents.find((a) => a.id === deleteAgentId)?.title ?? 'assistant' })}
          confirmLabel={t('common.delete')}
          danger
          onConfirm={() => handleDeleteAgent(deleteAgentId)}
          onCancel={() => setDeleteAgentId(null)}
        />
      )}

      {toasts.length > 0 && (
        <div className="toast-stack">
          {toasts.map((item) => (
            <div className={`toast toast-${item.status}`} key={item.id}>
              <span className={`toast-dot ${item.status}`} />
              <div className="toast-body">
                <strong>{item.title}</strong>
                <span>{t(`notify.${item.status}`)}</span>
              </div>
              <button
                className="toast-open"
                onClick={() => { router.openChat(item.agentId, item.conversationId); dismissToast(item.id); }}
              >
                {t('notify.open')}
              </button>
              <button className="toast-close" aria-label={t('common.close')} onClick={() => dismissToast(item.id)}>×</button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
