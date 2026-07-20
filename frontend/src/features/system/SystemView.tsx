import { useEffect, useState } from 'react';
import { Cloud, Download, FileArchive, RefreshCw, ShieldCheck, Upload, X } from 'lucide-react';
import type { Agent } from '../../types';
import { systemApi, type BundleDryRun, type DeviceStatus, type ImportReport } from './api';

export function SystemView({ agents, onImported, onClose }: { agents: Agent[]; onImported: () => Promise<void>; onClose: () => void }) {
  const [selected, setSelected] = useState(() => new Set(agents.map((agent) => agent.id)));
  const [device, setDevice] = useState<DeviceStatus>();
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState<BundleDryRun>();
  const [report, setReport] = useState<ImportReport>();
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const refreshDevice = async () => {
    try { setDevice(await systemApi.device()); } catch (value) { setError(value instanceof Error ? value.message : 'Could not load device status.'); }
  };
  useEffect(() => { void refreshDevice(); }, []);

  const run = async (name: string, action: () => Promise<void>) => {
    setBusy(name); setError('');
    try { await action(); } catch (value) { setError(value instanceof Error ? value.message : 'Operation failed.'); } finally { setBusy(''); }
  };

  const exportProfiles = () => run('export', async () => {
    const blob = await systemApi.export([...selected]);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = `open-lumora-${new Date().toISOString().slice(0, 10)}.lumora`; anchor.click();
    URL.revokeObjectURL(url);
  });

  const previewImport = () => file && run('preview', async () => { setPreview(await systemApi.dryRun(file)); setReport(undefined); });
  const applyImport = () => file && run('apply', async () => { setReport(await systemApi.apply(file)); await onImported(); });

  return (
    <div className="system-view">
      <header className="conn-topbar">
        <div><h1>Data & cloud</h1><p>Move profiles safely and manage the optional outbound connection.</p></div>
        <button className="icon-button" onClick={onClose} title="Close"><X size={17} /></button>
      </header>
      <div className="system-scroll">
        {error && <div className="system-error">{error}</div>}
        <section className="system-card">
          <div className="system-card-title"><FileArchive size={18} /><div><strong>Portable profiles</strong><small>Credentials, logs, host paths, and device identity are excluded.</small></div></div>
          <div className="system-agent-list">
            {agents.map((agent) => <label key={agent.id}><input type="checkbox" checked={selected.has(agent.id)} onChange={() => setSelected((current) => { const next = new Set(current); if (next.has(agent.id)) next.delete(agent.id); else next.add(agent.id); return next; })} /><span>{agent.name}</span><code>{agent.id}</code></label>)}
          </div>
          <button className="conn-btn primary" disabled={selected.size === 0 || !!busy} onClick={() => void exportProfiles()}><Download size={15} />{busy === 'export' ? 'Exporting…' : 'Export selected'}</button>
          <div className="system-import">
            <label className="system-file"><Upload size={16} /><span>{file?.name ?? 'Choose a .lumora bundle'}</span><input type="file" accept=".lumora,application/vnd.open-lumora.bundle" onChange={(event) => { setFile(event.target.files?.[0]); setPreview(undefined); setReport(undefined); }} /></label>
            <button className="conn-btn ghost" disabled={!file || !!busy} onClick={() => void previewImport()}>{busy === 'preview' ? 'Inspecting…' : 'Inspect & preview'}</button>
          </div>
          {preview && <div className="system-preview"><ShieldCheck size={17} /><div><strong>{preview.inspection.manifest.agents.length} profile(s), {preview.inspection.files} files</strong><span>{preview.collisions.length} ID collision(s) · {preview.paused_cron_jobs} cron(s) paused · {preview.quarantined_code.length} code file(s) quarantined</span></div><button className="conn-btn primary" disabled={!!busy} onClick={() => void applyImport()}>{busy === 'apply' ? 'Applying…' : 'Apply import'}</button></div>}
          {report && <div className="system-success">Imported {Object.keys(report.agent_id_mappings).length} profile(s). Review providers, approvals, quarantined code, and paused crons before use.</div>}
        </section>
        <section className="system-card">
          <div className="system-card-title"><Cloud size={18} /><div><strong>Cloud connection</strong><small>Outbound HTTPS only. Local agents continue to work while offline.</small></div><button className="icon-button" onClick={() => void refreshDevice()}><RefreshCw size={15} /></button></div>
          <div className="system-device"><span className={device?.connected ? 'conn-badge ok' : 'conn-badge'}>{device?.connected ? 'Connected' : device?.enabled ? 'Connecting / offline' : 'Disabled'}</span><span>{device?.endpoint || 'No cloud endpoint configured'}</span>{device?.last_error_code && <code>{device.last_error_code}</code>}</div>
          {device?.recovery_code && <div className="system-recovery"><strong>Recovery code</strong><code>{device.recovery_code}</code><span>Store this safely. It is not a login credential.</span></div>}
          <button className={device?.enabled ? 'conn-btn danger' : 'conn-btn primary'} disabled={!device || !!busy} onClick={() => void run('device', async () => { if (device?.enabled) await systemApi.unpair(); else await systemApi.pair(); await refreshDevice(); })}>{device?.enabled ? 'Unpair' : 'Pair device'}</button>
        </section>
      </div>
    </div>
  );
}
