import { useState } from 'react';
import { ArrowLeft, GitCompare, LoaderCircle, RefreshCw } from 'lucide-react';
import type { ReturnTypeOfUseCheckpoints } from './checkpointTypes';
import { CheckpointDiffDrawer } from './CheckpointDiffDrawer';
import { ConfirmDialog } from './modals';
import { useTranslation } from 'react-i18next';

export function WorkspaceRestorePoints({ checkpoints, view, path, checkpointId, onView, onPath, onCheckpoint, onRestored }: {
  checkpoints: ReturnTypeOfUseCheckpoints;
  view: 'restore-points' | 'versions';
  path: string;
  checkpointId: string;
  onView: (view: 'files' | 'restore-points' | 'versions') => void;
  onPath: (path: string) => void;
  onCheckpoint: (id: string) => void;
  onRestored: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [restore, setRestore] = useState<{ id: string; path?: string }>();
  const [diffOpen, setDiffOpen] = useState(false);
  const select = (id: string) => { onCheckpoint(id); void checkpoints.select(id); };
  const confirmRestore = async () => {
    if (!restore) return;
    await checkpoints.restore(restore.id, restore.path);
    setRestore(undefined);
    await onRestored();
  };
  const entries = view === 'versions'
    ? (checkpoints.versions?.items ?? []).map((item) => ({ id: item.checkpointId, shortId: item.shortId, createdAt: item.createdAt, reason: item.reason, exists: item.exists }))
    : checkpoints.items.map((item) => ({ id: item.id, shortId: item.shortId, createdAt: item.createdAt, reason: item.reason }));
  return (
    <div className="workspace-history">
      <header>
        <button className="icon-button" aria-label="Back to workspace files" onClick={() => { onView('files'); onPath(''); onCheckpoint(''); }}><ArrowLeft size={16} /></button>
        <div><strong>{view === 'versions' ? `${path} / ${t('checkpoints.versionHistory').toUpperCase()}` : t('checkpoints.restorePoints').toUpperCase()}</strong><span>{view === 'versions' ? t('checkpoints.versionDescription', 'Recover this exact workspace-relative path') : t('checkpoints.description')}</span></div>
        <button className="icon-button" aria-label="Refresh restore points" onClick={() => void checkpoints.refresh()}><RefreshCw size={15} /></button>
      </header>
      {checkpoints.error && <div className="inline-error" role="alert">{checkpoints.error}</div>}
      {checkpoints.loading && !entries.length ? <div className="workspace-history-empty"><LoaderCircle className="run-step-spin" /> Loading restore points…</div> : null}
      {!checkpoints.loading && !entries.length && <div className="workspace-history-empty">{t('checkpoints.empty')}</div>}
      <div className="checkpoint-list">
        {entries.map((item) => <button key={item.id} className={checkpointId === item.id ? 'active' : ''} onClick={() => select(item.id)}><code>{item.shortId}</code><span>{new Date(item.createdAt).toLocaleString()}</span><small>{item.reason}{'exists' in item && !item.exists ? ' · file absent' : ''}</small></button>)}
      </div>
      {checkpoints.diff && checkpointId && <div className="checkpoint-summary"><strong>{t('checkpoints.changesSince', 'Changes since this point')}</strong>{checkpoints.diff.files.map((file) => <div key={file.path}><b>{file.status[0].toUpperCase()}</b><span>{file.path}</span><em>+{file.insertions} -{file.deletions}</em></div>)}<button className="conn-btn ghost" onClick={() => setDiffOpen(true)}><GitCompare size={14} /> {t('checkpoints.previewDiff')}</button>{view === 'versions' && path && <button className="conn-btn ghost" onClick={() => setRestore({ id: checkpointId, path })}>{t('checkpoints.restoreFile')}</button>}<button className="conn-btn primary" onClick={() => setRestore({ id: checkpointId })}>{t('checkpoints.restoreWorkspace')}</button></div>}
      {diffOpen && checkpoints.diff && <CheckpointDiffDrawer diff={checkpoints.diff} onClose={() => setDiffOpen(false)} onRestoreFile={(file) => setRestore({ id: checkpointId, path: file })} onRestoreWorkspace={() => setRestore({ id: checkpointId })} />}
      {restore && <ConfirmDialog title={restore.path ? `${t('checkpoints.restoreFile')}?` : `${t('checkpoints.restoreWorkspace')}?`} message={`${restore.path ? `${t('checkpoints.restoreFile')}: ${restore.path}` : t('checkpoints.restoreWorkspace')} · ${restore.id.slice(0, 7)}. ${t('checkpoints.safety')} ${t('checkpoints.chatWarning')}`} confirmLabel={restore.path ? t('checkpoints.restoreFile') : t('checkpoints.restoreWorkspace')} danger pending={checkpoints.pending} onConfirm={() => void confirmRestore()} onCancel={() => setRestore(undefined)} />}
    </div>
  );
}
