import { X } from 'lucide-react';
import type { CheckpointDiff } from '../types';

export function CheckpointDiffDrawer({ diff, onClose, onRestoreFile, onRestoreWorkspace }: { diff: CheckpointDiff; onClose: () => void; onRestoreFile: (path: string) => void; onRestoreWorkspace: () => void }) {
  return (
    <div className="checkpoint-drawer-backdrop" onClick={onClose}>
      <section className="checkpoint-drawer" role="dialog" aria-modal="true" aria-label={`Restore point ${diff.shortId}`} onClick={(event) => event.stopPropagation()}>
        <header><div><strong>RESTORE POINT {diff.shortId}</strong><span>{diff.files.length} files changed</span></div><button aria-label="Close diff" onClick={onClose}><X size={17} /></button></header>
        <div className="checkpoint-file-nav">{diff.files.map((file) => <button key={file.path} onClick={() => onRestoreFile(file.path)}><b>{file.status[0].toUpperCase()}</b> {file.path} <span>+{file.insertions} -{file.deletions}</span></button>)}</div>
        <pre className="checkpoint-patch">{diff.patch || 'No changes since this restore point.'}</pre>
        {diff.truncated && <p className="checkpoint-truncated">Diff limited for safe preview. Full patch size: {diff.totalPatchBytes.toLocaleString()} bytes.</p>}
        <footer><button className="conn-btn ghost" disabled={!diff.files[0]} onClick={() => diff.files[0] && onRestoreFile(diff.files[0].path)}>Restore selected file</button><button className="conn-btn primary" onClick={onRestoreWorkspace}>Restore workspace</button></footer>
      </section>
    </div>
  );
}
