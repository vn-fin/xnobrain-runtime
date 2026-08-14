import { lazy, Suspense, useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import DOMPurify from 'dompurify';
import { ArrowUp, ChevronDown, ChevronUp, Download, FilePlus2, FileText, FolderPlus, HardDrive, LayoutGrid, List, LoaderCircle, Pencil, RefreshCw, UploadCloud, X } from 'lucide-react';
import { useWorkspace, WORKSPACE_PREVIEW_MAX_BYTES } from '../hooks/useWorkspace';
import { TreeIcon } from './common';
import { AsyncState } from './AsyncState';
import { ConfirmDialog, PromptDialog } from './modals';
import type { WorkspaceEntry } from '../types';
import type { WorkspaceView } from '../types';
import { useCheckpoints } from '../hooks/useCheckpoints';
import { WorkspaceRowMenu } from './WorkspaceRowMenu';
import { WorkspaceRestorePoints } from './WorkspaceRestorePoints';
import { useTranslation } from 'react-i18next';

type ViewMode = 'list' | 'grid';
type SortKey = 'name' | 'modified' | 'size';
type SortDir = 'asc' | 'desc';

export function workspaceNameError(name: string): string | undefined {
  const normalized = name.normalize('NFC');
  if (!name.trim()) return 'A name is required.';
  if (normalized !== name) return 'Use a normalized file name.';
  if (name === '.' || name === '..') return 'Choose a normal file or folder name.';
  if (/[\\/\u2215\u2044\uff0f\0\r\n]/u.test(name)) return 'Enter a name only, without path separators.';
  try {
    if (decodeURIComponent(name) !== name) return 'Encoded path characters are not allowed.';
  } catch {
    return 'The name contains invalid encoding.';
  }
  return undefined;
}

export const WORKSPACE_FILE_MIME = 'application/x-workspace-file';
const SpreadsheetViewer = lazy(() => import('./SpreadsheetViewer'));
const CodeViewer = lazy(() => import('./CodeViewer'));
const isInteractiveSpreadsheet = (entry: WorkspaceEntry) => (
  entry.language === 'spreadsheet'
  && (
    entry.name.toLowerCase().endsWith('.xlsx')
    || entry.name.toLowerCase().endsWith('.xlsm')
    || entry.name.toLowerCase().endsWith('.csv')
  )
);

/** Shared workspace controller shape (from useWorkspace), so it can be lifted
 * to App and passed to both the workspace panel and the chat drop target. */
export type WorkspaceController = ReturnType<typeof useWorkspace>;

function toTimestamp(modified: string | number): number {
  const numeric = typeof modified === 'number' ? modified : (/^\d+$/.test(modified) ? Number(modified) : NaN);
  if (!Number.isNaN(numeric)) return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = new Date(modified).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function formatSize(size: string) {
  const bytes = Number(size);
  if (!size || Number.isNaN(bytes)) return size || '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
}

function formatBytes(bytes?: number) {
  if (bytes == null) return '';
  return formatSize(String(bytes));
}

function formatModified(modified: string | number) {
  if (!modified && modified !== 0) return '—';
  const ts = toTimestamp(modified);
  if (!ts) return String(modified);
  return new Date(ts).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

/** Collect dropped files/folders (Chromium) preserving their relative directory. */
export async function readDroppedEntries(entries: FileSystemEntry[]): Promise<{ file: File; relativeDir: string }[]> {
  const out: { file: File; relativeDir: string }[] = [];
  const walk = async (entry: FileSystemEntry, dir: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve, reject) => (entry as FileSystemFileEntry).file(resolve, reject));
      out.push({ file, relativeDir: dir });
      return;
    }
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    const nextDir = dir ? `${dir}/${entry.name}` : entry.name;
    const readBatch = () => new Promise<FileSystemEntry[]>((resolve, reject) => reader.readEntries(resolve, reject));
    let batch = await readBatch();
    while (batch.length) {
      for (const child of batch) await walk(child, nextDir);
      batch = await readBatch();
    }
  };
  for (const entry of entries) await walk(entry, '');
  return out;
}

function joinSource(source: unknown): string {
  if (Array.isArray(source)) return source.join('');
  return typeof source === 'string' ? source : '';
}

type NotebookCell = { cell_type?: string; source?: unknown; outputs?: Array<Record<string, unknown>> };

function NotebookView({ content }: { content: string }) {
  const cells = useMemo<NotebookCell[] | null>(() => {
    try {
      const parsed = JSON.parse(content) as { cells?: NotebookCell[] };
      return Array.isArray(parsed.cells) ? parsed.cells : null;
    } catch { return null; }
  }, [content]);

  if (!cells) return <pre className="gd-code">{content || '(empty notebook)'}</pre>;

  return (
    <div className="nb-view">
      {cells.map((cell, index) => {
        const src = joinSource(cell.source);
        if (cell.cell_type === 'markdown') {
          return <div key={index} className="nb-cell nb-md"><pre>{src || ' '}</pre></div>;
        }
        return (
          <div key={index} className="nb-cell nb-code-cell">
            <div className="nb-gutter">[{cell.cell_type === 'code' ? ' ' : ''}]</div>
            <pre className="nb-src">{src}</pre>
            {(cell.outputs ?? []).map((output, outIndex) => {
              const data = (output.data ?? {}) as Record<string, unknown>;
              const png = data['image/png'];
              if (typeof png === 'string') {
                return <img key={outIndex} className="nb-out-img" src={`data:image/png;base64,${png}`} alt="output" />;
              }
              const text = joinSource(output.text) || joinSource(data['text/plain']);
              if (text) return <pre key={outIndex} className="nb-out">{text}</pre>;
              return null;
            })}
          </div>
        );
      })}
    </div>
  );
}

export function htmlPreviewDocument(content: string) {
  const sanitized = DOMPurify.sanitize(content, {
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed', 'form', 'base'],
    FORBID_ATTR: ['srcset'],
  });
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: blob:; style-src 'unsafe-inline'; font-src data:">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>html,body{margin:0;min-height:100%;background:#fff;color:#111}body{padding:16px;box-sizing:border-box}</style>
</head>
<body>${sanitized}</body>
</html>`;
}

function HtmlView({ content, title }: { content: string; title: string }) {
  const source = useMemo(() => htmlPreviewDocument(content), [content]);
  return <iframe className="gd-html" sandbox="" srcDoc={source} title={title} />;
}

export function OpeningFile({
  name,
  onCancel,
}: {
  name: string;
  onCancel: () => void;
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = Date.now();
    const timer = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [name]);
  const detail = elapsed < 10
    ? 'Loading file…'
    : elapsed < 30
      ? 'Preparing preview…'
      : 'Large workbooks can take a few minutes.';
  return (
    <div className="gd-file-opening" aria-live="polite">
      <span className="gd-sheet-spinner" />
      <strong>Opening {name}</strong>
      <span>{detail}</span>
      <span className="gd-opening-elapsed">{elapsed}s elapsed</span>
      <div className="gd-opening-track"><span /></div>
      <button className="conn-btn ghost" onClick={onCancel}>Cancel</button>
    </div>
  );
}

export function LargeFileNotice({
  name,
  size,
  downloading,
  error,
  onDownload,
}: {
  name: string;
  size: string;
  downloading: boolean;
  error: string;
  onDownload: () => void;
}) {
  return (
    <div className="gd-noview gd-large-file" role="status">
      <HardDrive size={42} />
      <p>This file is too large to preview</p>
      <span>
        {name} is {formatSize(size)}. Preview is limited to {formatSize(String(WORKSPACE_PREVIEW_MAX_BYTES))}
        {' '}to keep the app responsive.
      </span>
      <span>Download the file and open it with an application on your computer.</span>
      <button className="conn-btn primary" disabled={downloading} onClick={onDownload}>
        {downloading
          ? <><LoaderCircle className="run-step-spin" size={14} /> Downloading…</>
          : <><Download size={14} /> Download file</>}
      </button>
      {error && <span className="gd-large-file-error" role="alert">{error}</span>}
    </div>
  );
}

export function WorkspacePanel({ workspace, agentId, workspaceView, checkpointId, versionPath, onWorkspaceView, onCheckpoint, onVersionPath, onEnableHistory, openRequest }: { workspace: WorkspaceController; agentId: string; workspaceView: WorkspaceView; checkpointId: string; versionPath: string; onWorkspaceView: (view: WorkspaceView) => void; onCheckpoint: (id: string) => void; onVersionPath: (path: string) => void; onEnableHistory: () => void; openRequest?: { path: string; token: number } }) {
  const { t } = useTranslation();
  const [view, setView] = useState<ViewMode>('list');
  const [sortKey, setSortKey] = useState<SortKey>('name');
  const [sortDir, setSortDir] = useState<SortDir>('asc');
  const [activePath, setActivePath] = useState('');
  const [createType, setCreateType] = useState<'file' | 'directory' | null>(null);
  const [pendingDelete, setPendingDelete] = useState<WorkspaceEntry | null>(null);
  const [pendingRename, setPendingRename] = useState<WorkspaceEntry | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [dragging, setDragging] = useState(false);
  const [htmlMode, setHtmlMode] = useState<'preview' | 'source'>('preview');
  const dragDepth = useRef(0);
  const uploadRef = useRef<HTMLInputElement>(null);
  const openByPath = workspace.openByPath;
  const checkpoints = useCheckpoints(agentId, true);

  useEffect(() => {
    if (workspaceView === 'versions' && versionPath && checkpoints.status?.enabled) void checkpoints.loadVersions(versionPath);
    if (workspaceView !== 'files' && checkpointId && checkpoints.status?.enabled) void checkpoints.select(checkpointId);
  }, [workspaceView, versionPath, checkpointId, checkpoints.status?.enabled]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (openRequest?.path) {
      setActivePath(openRequest.path);
      void openByPath(openRequest.path);
    }
    // Re-run only when a new open request arrives (token changes).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openRequest?.token]);

  const create = (type: 'file' | 'directory') => {
    setCreateType(type);
  };

  const confirmRemove = (entry: WorkspaceEntry) => {
    setPendingDelete(entry);
  };

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) { setSortDir((dir) => (dir === 'asc' ? 'desc' : 'asc')); return; }
    setSortKey(key);
    setSortDir('asc');
  };

  const entries = workspace.currentEntries ?? [];
  const uploadProgress = workspace.uploadProgress;
  const uploadPercent = uploadProgress?.percent ?? 0;
  const uploadSize = uploadProgress
    ? uploadProgress.total
      ? `${formatBytes(uploadProgress.loaded)} / ${formatBytes(uploadProgress.total)}`
      : formatBytes(uploadProgress.loaded)
    : '';
  const sortedEntries = useMemo(() => {
    const factor = sortDir === 'asc' ? 1 : -1;
    return [...entries].sort((a, b) => {
      if (a.type !== b.type) return a.type === 'directory' ? -1 : 1; // folders first
      let cmp = 0;
      if (sortKey === 'name') cmp = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
      else if (sortKey === 'size') cmp = (Number(a.size) || 0) - (Number(b.size) || 0);
      else cmp = toTimestamp(a.modified) - toTimestamp(b.modified);
      if (cmp === 0) cmp = a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
      return cmp * factor;
    });
  }, [entries, sortKey, sortDir]);

  const initialLoading = workspace.status === 'loading' && workspace.currentEntries === undefined;

  const activate = (entry: WorkspaceEntry) => setActivePath(entry.path);
  const openEntry = (entry: WorkspaceEntry) => { setActivePath(entry.path); void workspace.open(entry); };
  const copyPath = async (entry: WorkspaceEntry) => {
    await navigator.clipboard.writeText(entry.path);
    setAnnouncement(`Copied ${entry.path}`);
  };
  const openHistory = (entry: WorkspaceEntry) => {
    onVersionPath(entry.path); onCheckpoint(''); onWorkspaceView('versions');
    void checkpoints.loadVersions(entry.path);
  };

  useEffect(() => {
    setHtmlMode('preview');
  }, [workspace.selected?.path]);

  const onEntryDragStart = (event: DragEvent, entry: WorkspaceEntry) => {
    if (entry.type !== 'file') { event.preventDefault(); return; }
    event.dataTransfer.setData(WORKSPACE_FILE_MIME, entry.path);
    event.dataTransfer.setData('text/plain', entry.path);
    event.dataTransfer.effectAllowed = 'copy';
  };

  const hasFiles = (event: DragEvent) => {
    const dt = event.dataTransfer;
    if (!dt) return false;
    return Array.from(dt.types).some((t) => t === 'Files' || t === 'application/x-moz-file');
  };

  const onDragEnter = (event: DragEvent) => {
    event.preventDefault();
    if (!hasFiles(event)) return;
    dragDepth.current += 1;
    setDragging(true);
  };

  const onDragOver = (event: DragEvent) => {
    // Must preventDefault on every dragover for the drop event to fire.
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  };

  const onDragLeave = (event: DragEvent) => {
    if (!hasFiles(event)) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);

    // Capture entries synchronously — the DataTransfer becomes invalid after an await.
    const fsEntries: FileSystemEntry[] = [];
    const items = event.dataTransfer?.items;
    if (items && items.length && typeof items[0].webkitGetAsEntry === 'function') {
      for (const item of Array.from(items)) {
        const entry = item.webkitGetAsEntry();
        if (entry) fsEntries.push(entry);
      }
    }

    if (fsEntries.length) {
      void readDroppedEntries(fsEntries).then((collected) => {
        if (collected.length) void workspace.uploadTree(collected);
      });
      return;
    }

    const files = Array.from(event.dataTransfer?.files ?? []);
    if (files.length) void workspace.uploadFiles(files);
  };

  const sortIcon = (key: SortKey) => sortKey !== key ? null : sortDir === 'asc' ? <ChevronUp size={12} /> : <ChevronDown size={12} />;

  const renderViewerBody = () => {
    const selected = workspace.selected;
    if (!selected) return null;
    if (workspace.opening) {
      return <OpeningFile name={selected.name} onCancel={workspace.cancelOpen} />;
    }
    if (workspace.previewBlocked) {
      return (
        <LargeFileNotice
          name={selected.name}
          size={selected.size}
          downloading={workspace.downloading}
          error={workspace.downloadError}
          onDownload={() => void workspace.downloadSelected()}
        />
      );
    }
    if (workspace.error) {
      return (
        <div className="gd-file-opening" role="alert">
          <FileText size={36} />
          <strong>Unable to open {selected.name}</strong>
          <span>{workspace.error}</span>
          <button className="conn-btn primary" onClick={() => void workspace.retryOpen()}>Try again</button>
        </div>
      );
    }
    if (selected.language === 'image') return <img src={workspace.previewUrl} alt={selected.name} />;
    if (selected.language === 'pdf') return <iframe className="gd-pdf" src={workspace.previewUrl} title={selected.name} />;
    if (isInteractiveSpreadsheet(selected)) {
      return (
        <Suspense fallback={<div className="gd-sheet-state">Loading spreadsheet viewer…</div>}>
          <SpreadsheetViewer sourceUrl={workspace.previewUrl} title={selected.name} />
        </Suspense>
      );
    }
    if (['document', 'spreadsheet', 'presentation'].includes(selected.language ?? '')) {
      return <iframe className="gd-office" src={workspace.previewUrl} title={selected.name} />;
    }
    if (selected.language === 'html' && htmlMode === 'preview') {
      return <HtmlView content={workspace.content} title={selected.name} />;
    }
    if (selected.language === 'binary') {
      return (
        <div className="gd-noview">
          <FileText size={40} />
          <p>Preview isn't available for this file type</p>
          <span>{selected.name}{selected.size ? ` · ${formatSize(selected.size)}` : ''}. Download the file to open it in a compatible app.</span>
          <button className="conn-btn primary" disabled={workspace.downloading} onClick={() => void workspace.downloadSelected()}>
            {workspace.downloading
              ? <><LoaderCircle className="run-step-spin" size={14} /> Downloading…</>
              : <><Download size={14} /> Download file</>}
          </button>
          {workspace.downloadError && <span className="gd-large-file-error" role="alert">{workspace.downloadError}</span>}
        </div>
      );
    }
    if (workspace.editing) return <textarea autoFocus value={workspace.content} onChange={(event) => workspace.setContent(event.target.value)} />;
    if (selected.language === 'notebook') return <NotebookView content={workspace.content} />;
    return (
      <Suspense fallback={<div className="gd-sheet-state">Loading code viewer…</div>}>
        <CodeViewer content={workspace.content} path={selected.path} />
      </Suspense>
    );
  };

  return (
    <section className="panel-section workspace-section">
      <div className="workspace-view-tabs" role="tablist" aria-label="Workspace view">
        <button role="tab" aria-selected={workspaceView === 'files'} className={workspaceView === 'files' ? 'active' : ''} onClick={() => onWorkspaceView('files')}>{t('checkpoints.files')}</button>
        <button role="tab" aria-selected={workspaceView !== 'files'} className={workspaceView !== 'files' ? 'active' : ''} onClick={() => onWorkspaceView('restore-points')}>{t('checkpoints.restorePoints')} {checkpoints.status?.checkpointCount ? <span>{checkpoints.status.checkpointCount}</span> : null}</button>
      </div>
      <span className="sr-only" aria-live="polite">{announcement}</span>
      {workspaceView !== 'files' ? (
        !checkpoints.status?.enabled ? <div className="workspace-history-empty"><strong>{t('checkpoints.disabled')}</strong><span>{t('checkpoints.description')}</span><button className="conn-btn primary" onClick={onEnableHistory}>{t('checkpoints.enableHistory')}</button></div>
          : !checkpoints.status.available ? <div className="workspace-history-empty" role="alert"><strong>{t('checkpoints.unavailable')}</strong><span>{checkpoints.status.unavailableReason}</span></div>
            : <WorkspaceRestorePoints checkpoints={checkpoints} view={workspaceView} path={versionPath} checkpointId={checkpointId} onView={onWorkspaceView} onPath={onVersionPath} onCheckpoint={onCheckpoint} onRestored={async () => { await workspace.refresh(); if (workspace.selected) await workspace.retryOpen(); setAnnouncement('Workspace restore completed'); }} />
      ) : (
      <div
        className={dragging ? 'gd-drive gd-dragging' : 'gd-drive'}
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {dragging && (
          <div className="gd-dropzone">
            <UploadCloud size={30} />
            <p>Drop files or folders to upload</p>
            <span>to /{workspace.cwd || 'Workspace'}</span>
          </div>
        )}
        <div className="gd-toolbar">
          <div className="gd-actions">
            <button title="New file" onClick={() => create('file')}><FilePlus2 size={16} /></button>
            <button title="New folder" onClick={() => create('directory')}><FolderPlus size={16} /></button>
            <button title="Upload files" onClick={() => uploadRef.current?.click()}><UploadCloud size={16} /></button>
            <input ref={uploadRef} hidden multiple type="file" onChange={(event) => { const files = Array.from(event.target.files ?? []); if (files.length) void workspace.uploadFiles(files); event.target.value = ''; }} />
          </div>
          <div className="gd-tools">
            <button title="Refresh" onClick={() => void workspace.refresh()} className={workspace.loading ? 'gd-spinning' : undefined}><RefreshCw size={15} /></button>
            <div className="gd-viewtoggle" role="group" aria-label="View mode">
              <button title="List view" aria-pressed={view === 'list'} className={view === 'list' ? 'active' : undefined} onClick={() => setView('list')}><List size={15} /></button>
              <button title="Grid view" aria-pressed={view === 'grid'} className={view === 'grid' ? 'active' : undefined} onClick={() => setView('grid')}><LayoutGrid size={15} /></button>
            </div>
          </div>
        </div>

        <div className="gd-breadcrumb">
          <button className="gd-crumb" title="Up one level" disabled={!workspace.cwd} onClick={workspace.up}><ArrowUp size={14} /></button>
          <button className="gd-crumb" onClick={() => workspace.navigate('')}><HardDrive size={14} /> Workspace</button>
          {workspace.breadcrumb.map((segment) => (
            <span key={segment.path} className="gd-crumb-wrap">
              <span className="gd-sep">/</span>
              <button className="gd-crumb" onClick={() => workspace.navigate(segment.path)}>{segment.name}</button>
            </span>
          ))}
        </div>

        <div className="gd-content">
          {workspace.error && <div className="inline-error gd-error">{workspace.error}</div>}
          {uploadProgress && (
            <div className="gd-upload-progress" aria-live="polite">
              <div className="gd-upload-row">
                <span>Uploading {uploadProgress.fileName}</span>
                <span>{uploadProgress.currentFile}/{uploadProgress.totalFiles} - {uploadProgress.percent ?? 0}% - {uploadSize}</span>
              </div>
              <div className="gd-upload-track"><span style={{ width: `${uploadPercent}%` }} /></div>
            </div>
          )}

          {initialLoading ? <AsyncState status="loading" /> : (
            sortedEntries.length === 0 ? (
              <div className="gd-empty">
                <HardDrive size={30} />
                <p>This folder is empty</p>
                <span>Create, upload, or drop files here to get started.</span>
              </div>
            ) : view === 'list' ? (
              <div className="gd-list">
                <div className="gd-list-head">
                  <button className={sortKey === 'name' ? 'gd-sort gd-h-name active' : 'gd-sort gd-h-name'} onClick={() => toggleSort('name')}>Name {sortIcon('name')}</button>
                  <button className={sortKey === 'modified' ? 'gd-sort gd-col-modified active' : 'gd-sort gd-col-modified'} onClick={() => toggleSort('modified')}>Modified {sortIcon('modified')}</button>
                  <button className={sortKey === 'size' ? 'gd-sort gd-col-size active' : 'gd-sort gd-col-size'} onClick={() => toggleSort('size')}>Size {sortIcon('size')}</button>
                </div>
                {sortedEntries.map((entry) => (
                  <div key={entry.path} className={activePath === entry.path ? 'gd-row active' : 'gd-row'}>
                    <button
                      className="gd-row-open"
                      draggable={entry.type === 'file'}
                      onDragStart={(event) => onEntryDragStart(event, entry)}
                      onClick={() => activate(entry)}
                      onDoubleClick={() => openEntry(entry)}
                    >
                      <TreeIcon entry={entry} size={16} />
                      <span className="gd-name">{entry.name}</span>
                      <span className="gd-col-modified">{formatModified(entry.modified)}</span>
                      <span className="gd-col-size">{entry.type === 'directory' ? '—' : formatSize(entry.size)}</span>
                    </button>
                    <WorkspaceRowMenu entry={entry} historyEnabled={!!checkpoints.status?.enabled} onOpen={() => openEntry(entry)} onDownload={() => void workspace.download(entry)} onHistory={() => openHistory(entry)} onEnableHistory={onEnableHistory} onRename={() => setPendingRename(entry)} onCopy={() => void copyPath(entry)} onDelete={() => confirmRemove(entry)} />
                  </div>
                ))}
              </div>
            ) : (
              <div className="gd-grid">
                {sortedEntries.map((entry) => (
                  <div key={entry.path} className={activePath === entry.path ? 'gd-card active' : 'gd-card'}>
                    <button
                      className="gd-card-open"
                      draggable={entry.type === 'file'}
                      onDragStart={(event) => onEntryDragStart(event, entry)}
                      onClick={() => activate(entry)}
                      onDoubleClick={() => openEntry(entry)}
                    >
                      <span className="gd-thumb"><TreeIcon entry={entry} size={34} /></span>
                      <span className="gd-cardname" title={entry.name}>{entry.name}</span>
                    </button>
                    <WorkspaceRowMenu entry={entry} historyEnabled={!!checkpoints.status?.enabled} onOpen={() => openEntry(entry)} onDownload={() => void workspace.download(entry)} onHistory={() => openHistory(entry)} onEnableHistory={onEnableHistory} onRename={() => setPendingRename(entry)} onCopy={() => void copyPath(entry)} onDelete={() => confirmRemove(entry)} />
                  </div>
                ))}
              </div>
            )
          )}
        </div>
      </div>
      )}

      {workspace.selected && (
        <div className="workspace-editor-backdrop" onClick={workspace.close}>
          <div className="workspace-editor" onClick={(event) => event.stopPropagation()}>
            <header>
              <span className="gd-editor-title"><TreeIcon entry={workspace.selected} size={18} /><strong>{workspace.selected.name}</strong></span>
              <div className="gd-editor-actions">
                {workspace.selected.language === 'html' && !workspace.editing && (
                  <span className="gd-mode-switch" aria-label="HTML view mode">
                    <button className={htmlMode === 'preview' ? 'active' : ''} onClick={() => setHtmlMode('preview')}>Preview</button>
                    <button className={htmlMode === 'source' ? 'active' : ''} onClick={() => setHtmlMode('source')}>Source</button>
                  </span>
                )}
                {isInteractiveSpreadsheet(workspace.selected) && (
                  <span className="gd-readonly-badge">Read only</span>
                )}
                {workspace.canEdit && !workspace.editing && (
                  <button className="conn-btn ghost" onClick={workspace.startEdit}><Pencil size={14} /> Edit</button>
                )}
                {workspace.previewUrl && (
                  <a className="conn-btn ghost" href={workspace.previewUrl} download={workspace.selected.name}><Download size={14} /> Download</a>
                )}
                <button className="gd-editor-close" title="Close" onClick={workspace.close}><X size={16} /></button>
              </div>
            </header>

            <div className="gd-viewer">{renderViewerBody()}</div>

            {workspace.editing && (
              <footer>
                {workspace.pending && <span className="gd-saving"><LoaderCircle className="run-step-spin" size={14} /> Saving…</span>}
                <button className="conn-btn ghost" onClick={workspace.cancelEdit}>Cancel</button>
                <button className="conn-btn primary" disabled={workspace.pending} onClick={() => void workspace.save()}>Save</button>
              </footer>
            )}
          </div>
        </div>
      )}

      {pendingDelete && (
        <ConfirmDialog
          title="Delete workspace item?"
          message={`Delete ${pendingDelete.path}? This action cannot be undone.`}
          confirmLabel="Delete"
          danger
          onConfirm={() => {
            void workspace.remove(pendingDelete);
            setPendingDelete(null);
          }}
          onCancel={() => setPendingDelete(null)}
        />
      )}

      {createType && (
        <PromptDialog
          title={createType === 'file' ? 'Create file' : 'Create folder'}
          message={`Create a new ${createType === 'file' ? 'file' : 'folder'} in the current workspace directory.`}
          label={createType === 'file' ? 'File name' : 'Folder name'}
          placeholder={createType === 'file' ? 'notes.md' : 'new-folder'}
          confirmLabel="Create"
          validate={workspaceNameError}
          onConfirm={(name) => {
            void workspace.create(name, createType);
            setCreateType(null);
          }}
          onCancel={() => setCreateType(null)}
        />
      )}
      {pendingRename && (
        <PromptDialog
          title={`Rename ${pendingRename.name}`}
          message="Choose a new name in the same workspace folder."
          label="New name"
          placeholder={pendingRename.name}
          initialValue={pendingRename.name}
          confirmLabel="Rename"
          validate={workspaceNameError}
          onConfirm={(name) => { void workspace.rename(pendingRename, name); setAnnouncement(`Renamed ${pendingRename.path} to ${name}`); setPendingRename(null); }}
          onCancel={() => setPendingRename(null)}
        />
      )}
    </section>
  );
}
