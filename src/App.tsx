import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { useRouter } from './hooks/useRouter';
import { useAssistants, useActiveAgent } from './hooks/useAssistants';
import { useConnections } from './hooks/useConnections';
import { useSandbox } from './hooks/useSandbox';
import { useCrons } from './hooks/useCrons';
import { useKanban } from './hooks/useKanban';
import { useAnalytics } from './hooks/useAnalytics';
import { useBlends } from './hooks/useBlends';
import { useConversation } from './hooks/useConversation';
import { useWorkspace } from './hooks/useWorkspace';
import { useTeams } from './hooks/useTeams';
import { streamStore, type CompletionEvent } from './chat/streamStore';
import { Sidebar } from './components/Sidebar';
import { ChatArea } from './components/ChatArea';
import { RightPanel } from './components/RightPanel';
import { SkillsView } from './components/SkillsView';
import { Onboarding } from './components/Onboarding';
import { SystemView } from './features/system/SystemView';
import { TeamsView } from './components/TeamsView';
import { KanbanView } from './components/KanbanView';
import { KanbanNotifications } from './components/KanbanNotifications';
import { AnalyticsView } from './components/AnalyticsView';
import { AccountView } from './components/AccountView';
import { CronView } from './components/CronView';
import { systemApi, type ImportReport } from './features/system/api';
import { AuthModal, CreateAgentModal, AgentSettingsModal, ConfirmDialog } from './components/modals';
import { AsyncState } from './components/AsyncState';
import { useAuth } from './auth';
import type { Agent } from './types';

export default function App() {
  const { t } = useTranslation();
  const auth = useAuth();
  const router = useRouter();
  const assistants = useAssistants();
  const assistantsReady = assistants.status === 'ready';
  const onboarding = assistantsReady && assistants.agents.length === 0;
  const connections = useConnections(
    assistantsReady
    && (onboarding || router.centerView === 'chat'
      || (router.centerView === 'data' && router.settingsSection === 'connectors')),
  );
  const sandbox = useSandbox(
    assistantsReady
    && (onboarding || (router.centerView === 'data' && router.settingsSection === 'vm')),
  );
  const conversation = useConversation(
    router.centerView === 'chat' ? router.activeAgentId : '',
    router.centerView === 'chat' ? router.activeConversationId : '',
  );
  const workspace = useWorkspace(
    router.activeAgentId,
    router.centerView === 'chat' && router.rightView === 'workspace',
  );
  // Kanban's new-task modal also needs saved teams, so keep this lightweight
  // list loaded outside the dedicated Teams screen as well.
  const teams = useTeams(true);
  const kanban = useKanban(router.centerView === 'kanban' || router.centerView === 'analytics');
  const analytics = useAnalytics(router.centerView === 'analytics', assistants.agents);
  const blends = useBlends(router.centerView === 'chat');
  const crons = useCrons(
    assistantsReady && (router.centerView === 'cron' || (router.centerView === 'chat' && router.rightView === 'cron')),
  );

  // Resizable right panel width (persisted). Applied as the --right grid column.
  const RIGHT_MIN = 280;
  const RIGHT_MAX = 720;
  const [rightWidth, setRightWidth] = useState(() => {
    const stored = Number(localStorage.getItem('rightPanelWidth'));
    return Number.isFinite(stored) && stored >= RIGHT_MIN ? Math.min(stored, RIGHT_MAX) : 330;
  });
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  useEffect(() => { localStorage.setItem('rightPanelWidth', String(rightWidth)); }, [rightWidth]);
  const clampRightWidth = (width: number) => Math.min(RIGHT_MAX, Math.max(RIGHT_MIN, Math.min(width, Math.round(window.innerWidth * 0.6))));

  const activeAgent = useActiveAgent(assistants.agents, router.activeAgentId);
  const activeConversation =
    activeAgent?.conversations.find((c) => c.id === router.activeConversationId) ?? activeAgent?.conversations[0];

  // UI-only modal state
  const [createAgentOpen, setCreateAgentOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [logoutConfirmOpen, setLogoutConfirmOpen] = useState(false);
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
  const workspaceVisibleRef = useRef(router.centerView === 'chat' && router.rightView === 'workspace');
  workspaceVisibleRef.current = router.centerView === 'chat' && router.rightView === 'workspace';

  useEffect(() => {
    const unsubscribe = streamStore.onComplete((event) => {
      if (event.agentId === activeAgentIdRef.current && workspaceVisibleRef.current) {
        void workspaceRefreshRef.current();
      }
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
    setRightPanelOpen(true);
    setWorkspaceOpenRequest({ path, token: Date.now() });
  };

  const handleRenameConversation = (conversationId: string, title: string) =>
    assistants.renameConversation(router.activeAgentId, conversationId, title);

  useEffect(() => {
    if (!assistantsReady || assistants.agents.length === 0) return;
    if (assistants.agents.some((agent) => agent.id === router.activeAgentId)) return;
    router.setActiveAgentId(assistants.agents[0].id);
    router.setActiveConversationId('');
  }, [
    assistants.agents,
    assistantsReady,
    router.activeAgentId,
    router.setActiveAgentId,
    router.setActiveConversationId,
  ]);

  useEffect(() => {
    if (!assistantsReady || router.centerView !== 'chat' || !activeAgent) return undefined;
    if (activeAgent.id !== router.activeAgentId) return undefined;
    let cancelled = false;
    void assistants.loadConversations(activeAgent.id).then((rows) => {
      if (cancelled) return;
      const selected = rows.find((item) => item.id === router.activeConversationId) ?? rows[0];
      if ((selected?.id ?? '') !== router.activeConversationId) {
        router.setActiveConversationId(selected?.id ?? '');
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [
    activeAgent,
    assistants.loadConversations,
    assistantsReady,
    router.activeAgentId,
    router.activeConversationId,
    router.centerView,
    router.setActiveConversationId,
  ]);

  useEffect(() => {
    if (!assistantsReady) return;
    if (onboarding) {
      void assistants.loadDefaultConfig();
      return;
    }
    if (router.centerView === 'skills') {
      void assistants.loadLibrary(true);
    } else if (router.centerView === 'chat' && router.rightView === 'skills' && activeAgent) {
      void assistants.loadLibrary();
      void assistants.loadAgentSkills(activeAgent.id);
    }
  }, [
    activeAgent,
    assistants.loadAgentSkills,
    assistants.loadDefaultConfig,
    assistants.loadLibrary,
    assistantsReady,
    onboarding,
    router.centerView,
    router.rightView,
  ]);

  // The agent list endpoint only returns summaries, so hydrate each agent's
  // skills before rendering the library's per-agent usage indicators.
  useEffect(() => {
    if (!assistantsReady || onboarding || router.centerView !== 'skills') return;
    void Promise.all(
      assistants.agents.map((agent) => assistants.loadAgentSkills(agent.id)),
    );
  }, [
    assistants.agents,
    assistants.loadAgentSkills,
    assistantsReady,
    onboarding,
    router.centerView,
  ]);

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

  const handleImportedProfile = async (report: ImportReport) => {
    await assistants.refresh();
    const agentId = Object.values(report.agent_id_mappings)[0];
    if (agentId) router.openChat(agentId, '');
    setCreateAgentOpen(false);
  };

  const exportProfile = async (agentId: string) => {
    const download = await systemApi.export([agentId]);
    const url = URL.createObjectURL(download.blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = download.filename;
    anchor.click();
    URL.revokeObjectURL(url);
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
          setupMessage={sandbox.setupMessage}
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
      className={centerView === 'chat' ? `app${rightPanelOpen ? ' inspector-open' : ' inspector-collapsed'}` : 'app no-right'}
      style={centerView === 'chat' ? ({ '--right': `${rightPanelOpen ? rightWidth : 48}px` } as CSSProperties) : undefined}
    >
      <Sidebar
        agents={assistants.agents}
        activeAgent={activeAgent}
        centerView={centerView}
        agentSearch={router.agentSearch}
        onAgentSearch={router.setAgentSearch}
        onNavigate={router.setCenterView}
        onSelectAgent={(agent) => {
          void assistants.loadConversations(agent.id).then((rows) => {
            router.openChat(agent.id, rows[0]?.id ?? '');
          });
        }}
        onRenameAgent={assistants.renameAgent}
        onExportAgent={exportProfile}
        onRequestDeleteAgent={setDeleteAgentId}
        onNewAgent={() => setCreateAgentOpen(true)}
        user={auth.user}
        edition={auth.config.edition}
        loginEnabled={auth.config.features.login && auth.config.auth.mode !== 'disabled'}
        sessionActive={auth.sessionActive}
        onOpenLogin={auth.openLogin}
        onOpenAccount={() => setAccountOpen(true)}
        onSignOut={async () => setLogoutConfirmOpen(true)}
      />

      <main className={centerView === 'chat' ? 'chat-area' : 'chat-area sandbox-mode'}>
        {centerView === 'skills' ? (
          <SkillsView
            library={assistants.library}
            agents={assistants.agents}
            agentSkills={assistants.agentSkills}
            search={router.skillsSearch}
            onSearch={router.setSkillsSearch}
            groupFilter={router.skillsGroupFilter}
            onGroupFilter={router.setSkillsGroupFilter}
            onInstall={assistants.installDefaultSkill}
            onSetDefaultEnabled={assistants.setDefaultSkillEnabled}
            installPending={assistants.skillInstallPending}
            installError={assistants.skillInstallError}
            onInstallExisting={assistants.installExistingSkill}
            onApply={assistants.applySkillsToAgents}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'data' ? (
          <SystemView
            agents={assistants.agents}
            providers={connections.connections}
            keyProviderId={connections.keyProviderId}
            providerPendingId={connections.pendingId}
            onSelectKeyProvider={connections.setKeyProviderId}
            onConnect={connections.connect}
            onDisconnect={connections.disconnect}
            onTestProvider={connections.test}
            onSaveKey={connections.saveKey}
            accounts={{
              connectionsByProvider: connections.connectionsByProvider,
              usageByConnection: connections.usageByConnection,
              rowPendingId: connections.rowPendingId,
              onLoadConnections: connections.loadConnections,
              onAddAccount: connections.addAccount,
              onSetAccountActive: connections.setAccountActive,
              onReorderAccount: connections.reorderAccount,
              onTestAccount: connections.testAccount,
              onRemoveAccount: connections.removeAccount,
              onLoadAccountUsage: connections.loadAccountUsage,
            }}
            sandbox={{
              data: sandbox.data,
              provisioned: sandbox.provisioned,
              status: sandbox.status,
              error: sandbox.error,
              setupRunning: sandbox.setupRunning,
              setupProgress: sandbox.setupProgress,
              setupMessage: sandbox.setupMessage,
              onCreate: sandbox.createSandbox,
              onRefresh: sandbox.refresh,
            }}
            onImported={assistants.refresh}
            section={router.settingsSection}
            onSectionChange={router.setSettingsSection}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'teams' ? (
          <TeamsView
            agents={assistants.agents}
            state={teams}
            routeTeamId={router.activeTeamId}
            routeRunId={router.activeTeamRunId}
            routeCreate={router.teamCreate}
            onNavigate={(teamId, runId, create, replace) => {
              if (create) router.createTeam();
              else router.openTeam(teamId, runId, replace);
            }}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'kanban' ? (
          <KanbanView
            teams={teams.teams}
            agents={assistants.agents}
            state={kanban}
            onLoadAgentSkills={assistants.loadAgentSkills}
            routeTaskId={router.kanbanTaskId}
            routeAgentId={router.kanbanAgentId}
            routeConversationId={router.kanbanConversationId}
            onNavigate={router.openKanbanTask}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'analytics' ? (
          <AnalyticsView
            state={analytics}
            workspace={{
              agents: assistants.agents,
              teams: teams.teams,
              teamStatus: teams.status,
              boards: kanban.boards,
              kanbanStatus: kanban.status,
            }}
            onNavigate={router.setCenterView}
            onClose={() => router.setCenterView('chat')}
          />
        ) : centerView === 'cron' ? (
          <CronView
            agents={assistants.agents}
            crons={crons.crons}
            status={crons.status}
            error={crons.error}
            pendingId={crons.pendingId}
            detail={crons.detail}
            onCreate={crons.createCron}
            onToggle={crons.toggleCron}
            onRun={crons.runCron}
            onDelete={crons.deleteCron}
            onLoadDetail={crons.loadDetail}
            onCloseDetail={crons.closeDetail}
          />
        ) : (
          <ChatArea
            agent={activeAgent}
            agents={assistants.agents}
            activeConversation={activeConversation}
            providers={connections.connections}
            blends={blends.blends.map((blend) => blend.name)}
            runs={conversation.runs}
            messages={conversation.messages}
            usage={conversation.usage}
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
            onSelectModel={(provider, model) => assistants.updateAgent(activeAgent.id, { provider, model })}
            onTestAgent={() => void assistants.testAgent(activeAgent.id)}
            onOpenSettings={() => setSettingsOpen(true)}
            onOpenRuntime={() => {
              router.setRightView('runtime');
              setRightPanelOpen(true);
            }}
            onSelectAgent={(agent) => {
              void assistants.loadConversations(agent.id).then((rows) => {
                router.openChat(agent.id, rows[0]?.id ?? '');
              });
            }}
            onDeleteAgent={() => setDeleteAgentId(activeAgent.id)}
            onSelectConversation={(id) => {
              if (id === router.activeConversationId) void conversation.requestUsage();
              router.setActiveConversationId(id);
            }}
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
          open={rightPanelOpen}
          onOpen={() => setRightPanelOpen(true)}
          onClose={() => setRightPanelOpen(false)}
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
          crons={crons.crons.filter((job) => job.agentId === activeAgent.id)}
          cronStatus={crons.status}
          cronError={crons.error}
          cronPendingId={crons.pendingId}
          onCreateCron={(input) => crons.createCron({ agentId: activeAgent.id, ...input })}
          onToggleCron={crons.toggleCron}
          onRunCron={crons.runCron}
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

      {createAgentOpen && <CreateAgentModal onCreate={handleCreateAgent} onImported={handleImportedProfile} onClose={() => setCreateAgentOpen(false)} />}

      {settingsOpen && (
        <AgentSettingsModal agent={activeAgent} providers={runtimeProviders} onSave={handleUpdateAgent} onClose={() => setSettingsOpen(false)} />
      )}

      {accountOpen && auth.sessionActive && (
        <div className="modal-overlay" onClick={() => setAccountOpen(false)}>
          <div
            className="app-modal account-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Account details"
            onClick={(event) => event.stopPropagation()}
          >
            <AccountView
              onClose={() => setAccountOpen(false)}
              onRequestSignOut={() => setLogoutConfirmOpen(true)}
            />
          </div>
        </div>
      )}

      {logoutConfirmOpen && (
        <ConfirmDialog
          title="Sign out?"
          message="Are you sure you want to sign out of this account?"
          confirmLabel="Sign out"
          danger
          onConfirm={() => {
            setLogoutConfirmOpen(false);
            void auth.signOut().then(() => setAccountOpen(false));
          }}
          onCancel={() => setLogoutConfirmOpen(false)}
        />
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

      <KanbanNotifications
        events={kanban.events}
        liveStatus={kanban.liveStatus}
        onOpenTask={(taskId) => {
          kanban.setActiveBoardId('default');
          router.openKanbanTask(taskId);
        }}
      />
    </div>
  );
}
