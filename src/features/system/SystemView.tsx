import { useEffect, useMemo, useRef, useState } from 'react';
import { Download, FileArchive, Server, ShieldCheck, Upload, X } from 'lucide-react';
import { ConnectionsView, type AccountProps } from '../../components/ConnectionsView';
import { BlendsSection } from './BlendsSection';
import { McpSection } from './McpSection';
import { SandboxView } from '../../components/SandboxView';
import type { Agent, AsyncStatus, ConnectionProvider, SandboxData } from '../../types';
import type { ProviderTestOutcome } from '../../hooks/useConnections';
import type { SettingsSection } from '../../hooks/useRouter';
import { systemApi, type BundleDryRun, type BundleTransfer, type DeploymentStatus, type ImportReport, type TransferProgress } from './api';
import { sortPortableAgents } from './portableProfiles';

type SystemViewProps = {
  agents: Agent[];
  providers: ConnectionProvider[];
  keyProviderId: string;
  providerPendingId: string | null;
  onSelectKeyProvider: (id: string) => void;
  onConnect: (id: string) => void;
  onDisconnect: (id: string) => void;
  onTestProvider: (id: string) => Promise<ProviderTestOutcome>;
  onSaveKey: (id: string, key: string, baseUrl?: string) => void;
  accounts: AccountProps;
  sandbox: {
    data: SandboxData | null;
    provisioned: boolean;
    status: AsyncStatus;
    error: string;
    setupRunning: boolean;
    setupProgress: number;
    setupMessage: string;
    onCreate: () => void;
    onRefresh: () => void;
  };
  onImported: () => Promise<void>;
  onClose: () => void;
  section: SettingsSection;
  onSectionChange: (section: SettingsSection) => void;
};

export function SystemView({
  agents,
  providers,
  keyProviderId,
  providerPendingId,
  onSelectKeyProvider,
  onConnect,
  onDisconnect,
  onTestProvider,
  onSaveKey,
  accounts,
  sandbox,
  onImported,
  onClose,
  section,
  onSectionChange,
}: SystemViewProps) {
  const [selected, setSelected] = useState(() => new Set(agents.map((agent) => agent.id)));
  const [deployment, setDeployment] = useState<DeploymentStatus>();
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState<BundleDryRun>();
  const [report, setReport] = useState<ImportReport>();
  const [transfer, setTransfer] = useState<BundleTransfer>();
  const [progress, setProgress] = useState<TransferProgress>();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const deploymentLoadStarted = useRef(false);
  const portableAgents = useMemo(() => sortPortableAgents(agents), [agents]);
  const tabs: Array<{ id: SettingsSection; label: string }> = [
    { id: 'profiles', label: 'Profiles' },
    { id: 'vm', label: 'VM' },
    { id: 'connectors', label: 'Connectors' },
    { id: 'mcp', label: 'MCP' },
    { id: 'blends', label: 'Model Blends' },
  ];

  useEffect(() => {
    if (section !== 'profiles' || deploymentLoadStarted.current) return;
    deploymentLoadStarted.current = true;
    void systemApi.deployment().then(setDeployment).catch((value) => setError(value instanceof Error ? value.message : 'Could not load deployment mode.'));
  }, [section]);

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setError('');
    try { await action(); } catch (value) { setError(value instanceof Error ? value.message : 'Operation failed.'); } finally { setBusy(''); }
  };

  const exportProfiles = () => run('export', async () => {
    setProgress(undefined);
    await systemApi.download([...selected], setProgress);
    setProgress(undefined);
  });

  const previewImport = () => file && run('preview', async () => {
    setProgress(undefined);
    const uploaded = await systemApi.upload(file, setProgress);
    setTransfer(uploaded);
    setPreview(uploaded.preview);
    setReport(undefined);
    setProgress(undefined);
  });
  const applyImport = () => transfer?.upload_id && run('apply', async () => {
    setReport(await systemApi.applyUpload(transfer.upload_id!));
    setTransfer(undefined);
    await onImported();
  });

  return (
    <div className="system-view">
      <header className="conn-topbar">
        <div><h1>Settings</h1><p>Local deployment, portable data, and advanced integrations.</p></div>
        <button className="icon-button" onClick={onClose} title="Close"><X size={17} /></button>
      </header>
      <nav className="settings-tabs" role="tablist" aria-label="Settings sections">
        {tabs.map((tab) => (
          <button
            key={tab.id}
            className={`settings-tab${section === tab.id ? ' active' : ''}`}
            role="tab"
            aria-selected={section === tab.id}
            aria-controls="settings-panel"
            tabIndex={section === tab.id ? 0 : -1}
            onClick={() => onSectionChange(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>
      <div className="system-scroll" id="settings-panel" role="tabpanel">
        {error && <div className="system-error">{error}</div>}
        {section === 'profiles' && <>
          <section className="system-card">
          <div className="system-card-title"><Server size={18} /><div><strong>Deployment</strong><small>This installation runs locally and does not contact a managed service.</small></div></div>
          <div className="system-deployment">
            <span className="conn-badge ok">Local</span>
            <div><strong>Local runtime</strong><small>{deployment?.runtime_transport || 'Checking runtime…'}</small></div>
          </div>
          </section>
          <section className="system-card">
          <div className="system-card-title"><FileArchive size={18} /><div><strong>Portable profiles</strong><small>Credentials, logs, host paths, and device identity are excluded.</small></div></div>
          <div className="system-agent-list">
            {portableAgents.map((agent) => <label key={agent.id}><input type="checkbox" checked={selected.has(agent.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(agent.id)) next.delete(agent.id); else next.add(agent.id); return next; })} /><span>{agent.title}</span><code>{agent.id}</code></label>)}
          </div>
          <button className="conn-btn primary" disabled={selected.size === 0 || !!busy} onClick={() => void exportProfiles()}><Download size={15} />{busy === 'export' ? `Exporting${progress ? ` ${progress.percent}%` : '…'}` : 'Export selected'}</button>
          <div className="system-import">
            <label className="system-file"><Upload size={16} /><span>{file?.name ?? 'Choose a .zip profile archive'}</span><input type="file" accept=".zip,application/zip" onChange={(event) => { if (transfer?.upload_id) void systemApi.cancelUpload(transfer.upload_id); setFile(event.target.files?.[0]); setTransfer(undefined); setPreview(undefined); setReport(undefined); }} /></label>
            <button className="conn-btn ghost" disabled={!file || !!busy} onClick={() => void previewImport()}>{busy === 'preview' ? `Uploading${progress ? ` ${progress.percent}%` : '…'}` : 'Upload & preview'}</button>
          </div>
          {preview && <div className="system-preview"><ShieldCheck size={17} /><div><strong>{preview.inspection.manifest.agents.length} profile(s), {preview.inspection.files} files</strong><span>{preview.collisions.length} ID collision(s) · {preview.paused_cron_jobs} cron(s) paused · {preview.quarantined_code.length} code file(s) quarantined</span></div><button className="conn-btn primary" disabled={!!busy} onClick={() => void applyImport()}>{busy === 'apply' ? 'Applying…' : 'Apply import'}</button></div>}
          {report && <div className="system-success">Imported {Object.keys(report.agent_id_mappings).length} profile(s). Review providers, approvals, quarantined code, and paused crons before use.</div>}
          </section>
        </>}
        {section === 'mcp' && <McpSection agents={agents} />}
        {section === 'connectors' && <section className="system-integrations-card">
          <ConnectionsView
            providers={providers}
            keyProviderId={keyProviderId}
            pendingId={providerPendingId}
            onSelectKeyProvider={onSelectKeyProvider}
            onConnect={onConnect}
            onDisconnect={onDisconnect}
            onTest={onTestProvider}
            onSaveKey={onSaveKey}
            onClose={() => undefined}
            embedded
            {...accounts}
          />
        </section>}
        {section === 'blends' && <section className="system-integrations-card">
          <BlendsSection />
        </section>}
        {section === 'vm' && <section className="system-integrations-card">
          <SandboxView
            data={sandbox.data}
            provisioned={sandbox.provisioned}
            status={sandbox.status}
            error={sandbox.error}
            setupRunning={sandbox.setupRunning}
            setupProgress={sandbox.setupProgress}
            setupMessage={sandbox.setupMessage}
            onCreate={sandbox.onCreate}
            onRefresh={sandbox.onRefresh}
            onClose={() => undefined}
            embedded
          />
        </section>}
      </div>
    </div>
  );
}
