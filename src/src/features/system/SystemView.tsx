import { useEffect, useState } from 'react';
import { Download, FileArchive, Network, Server, ShieldCheck, Upload, X } from 'lucide-react';
import type { Agent } from '../../types';
import { systemApi, type BundleDryRun, type BundleTransfer, type DeploymentStatus, type ImportReport, type TransferProgress } from './api';

export function SystemView({ agents, onImported, onClose }: { agents: Agent[]; onImported: () => Promise<void>; onClose: () => void }) {
  const [selected, setSelected] = useState(() => new Set(agents.map((agent) => agent.id)));
  const [deployment, setDeployment] = useState<DeploymentStatus>();
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState<BundleDryRun>();
  const [report, setReport] = useState<ImportReport>();
  const [transfer, setTransfer] = useState<BundleTransfer>();
  const [progress, setProgress] = useState<TransferProgress>();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    void systemApi.deployment().then(setDeployment).catch((value) => setError(value instanceof Error ? value.message : 'Could not load deployment mode.'));
  }, []);

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setError('');
    try { await action(); } catch (value) { setError(value instanceof Error ? value.message : 'Operation failed.'); } finally { setBusy(''); }
  };

  const exportProfiles = () => run('export', async () => {
    setProgress(undefined);
    const download = await systemApi.export([...selected], setProgress);
    const url = URL.createObjectURL(download.blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = download.filename; anchor.click();
    URL.revokeObjectURL(url);
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
      <div className="system-scroll">
        {error && <div className="system-error">{error}</div>}
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
            {agents.map((agent) => <label key={agent.id}><input type="checkbox" checked={selected.has(agent.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(agent.id)) next.delete(agent.id); else next.add(agent.id); return next; })} /><span>{agent.title}</span><code>{agent.id}</code></label>)}
          </div>
          <button className="conn-btn primary" disabled={selected.size === 0 || !!busy} onClick={() => void exportProfiles()}><Download size={15} />{busy === 'export' ? `Exporting${progress ? ` ${progress.percent}%` : '…'}` : 'Export selected'}</button>
          <div className="system-import">
            <label className="system-file"><Upload size={16} /><span>{file?.name ?? 'Choose a .zip profile archive'}</span><input type="file" accept=".zip,application/zip" onChange={(event) => { if (transfer?.upload_id) void systemApi.cancelUpload(transfer.upload_id); setFile(event.target.files?.[0]); setTransfer(undefined); setPreview(undefined); setReport(undefined); }} /></label>
            <button className="conn-btn ghost" disabled={!file || !!busy} onClick={() => void previewImport()}>{busy === 'preview' ? `Uploading${progress ? ` ${progress.percent}%` : '…'}` : 'Upload & preview'}</button>
          </div>
          {preview && <div className="system-preview"><ShieldCheck size={17} /><div><strong>{preview.inspection.manifest.agents.length} profile(s), {preview.inspection.files} files</strong><span>{preview.collisions.length} ID collision(s) · {preview.paused_cron_jobs} cron(s) paused · {preview.quarantined_code.length} code file(s) quarantined</span></div><button className="conn-btn primary" disabled={!!busy} onClick={() => void applyImport()}>{busy === 'apply' ? 'Applying…' : 'Apply import'}</button></div>}
          {report && <div className="system-success">Imported {Object.keys(report.agent_id_mappings).length} profile(s). Review providers, approvals, quarantined code, and paused crons before use.</div>}
        </section>
        <section className="system-card system-advanced-card">
          <div className="system-card-title"><Network size={18} /><div><strong>MCP servers</strong><small>Advanced integration setup. Skills stay the recommended way to add reusable agent behavior.</small></div></div>
          <p className="system-advanced-copy">MCP configuration is isolated per assistant in its profile. Add it only when a skill cannot provide the required external tool connection.</p>
          <div className="system-agent-list">
            {agents.map((agent) => <div className="system-agent-config" key={agent.id}><span>{agent.title}</span><code>profiles/{agent.id}/mcp.json</code></div>)}
          </div>
        </section>
      </div>
    </div>
  );
}
