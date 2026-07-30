import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { workspaceApi } from '../api/workspace';
import { detectLanguage } from '../api/mappers/workspace';
import type { AsyncStatus, WorkspaceEntry } from '../types';

export type WorkspaceUploadProgress = {
  fileName: string;
  loaded: number;
  total?: number;
  percent?: number;
  currentFile: number;
  totalFiles: number;
};

export const WORKSPACE_PREVIEW_MAX_BYTES = 3 * 1024 * 1024;

const parentPath = (path: string) => path.split('/').filter(Boolean).slice(0, -1).join('/');

function sorted(entries: WorkspaceEntry[]) {
  return [...entries].sort((a, b) => {
    if (a.type !== b.type) return a.type === 'directory' ? -1 : 1;
    return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
  });
}

export function useWorkspace(agentId: string, active = true) {
  const [entriesByPath, setEntriesByPath] = useState<Record<string, WorkspaceEntry[]>>({});
  const [cwd, setCwd] = useState('');
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<WorkspaceEntry | null>(null);
  const [content, setContent] = useState('');
  const [previewUrl, setPreviewUrl] = useState('');
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState<AsyncStatus>('loading');
  const [pending, setPending] = useState(false);
  const [opening, setOpening] = useState(false);
  const [previewBlocked, setPreviewBlocked] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState('');
  const [error, setError] = useState('');
  const [uploadProgress, setUploadProgress] = useState<WorkspaceUploadProgress | null>(null);

  // Cancels only the previous in-flight list request when a new one starts
  // (navigation, refresh, agent switch) — not on React effect cleanup, so it
  // doesn't fight React StrictMode's dev remount.
  const listController = useRef<AbortController>();
  const openController = useRef<AbortController>();
  // Guards the initial load so it runs once per agent even when StrictMode
  // invokes the mount effect twice in development.
  const loadedAgent = useRef<string | null>(null);

  const loadPath = useCallback(async (path: string) => {
    if (!agentId) return;
    listController.current?.abort();
    const controller = new AbortController();
    listController.current = controller;
    const { signal } = controller;
    setLoading(true);
    setError('');
    if (!path && !(path in entriesByPath)) setStatus('loading');
    try {
      const entries = sorted(await workspaceApi.list(agentId, path, signal));
      if (signal.aborted) return;
      setEntriesByPath((current) => ({ ...current, [path]: entries }));
      setStatus('ready');
    } catch (cause) {
      if (signal.aborted) return;
      const message = cause instanceof Error ? cause.message : 'Unable to load workspace';
      setError(message);
      if (!path) setStatus('error');
    } finally {
      if (listController.current === controller) listController.current = undefined;
      if (!signal.aborted) setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  useEffect(() => {
    if (!active || !agentId || loadedAgent.current === agentId) return;
    loadedAgent.current = agentId;
    setEntriesByPath({});
    setCwd('');
    void loadPath('');
  }, [active, agentId, loadPath]);

  const navigate = useCallback((path: string) => {
    setCwd(path);
    void loadPath(path);
  }, [loadPath]);

  const refresh = useCallback(() => loadPath(cwd), [cwd, loadPath]);

  const currentEntries = entriesByPath[cwd];

  const breadcrumb = useMemo(() => {
    const segments = cwd.split('/').filter(Boolean);
    let acc = '';
    return segments.map((name) => {
      acc = acc ? `${acc}/${name}` : name;
      return { name, path: acc };
    });
  }, [cwd]);

  const NON_TEXT_LANGUAGES = ['image', 'pdf', 'document', 'spreadsheet', 'presentation', 'binary'];
  const OFFICE_LANGUAGES = ['document', 'spreadsheet', 'presentation'];
  const isText = (entry: WorkspaceEntry) => (
    entry.type === 'file' && !NON_TEXT_LANGUAGES.includes(entry.language ?? 'text')
  );
  const interactiveSpreadsheetExtension = (entry: WorkspaceEntry) => {
    const name = entry.name.toLowerCase();
    if (entry.language !== 'spreadsheet') return '';
    if (name.endsWith('.xlsm')) return 'xlsm';
    if (name.endsWith('.xlsx')) return 'xlsx';
    if (name.endsWith('.csv')) return 'csv';
    return '';
  };

  const open = async (entry: WorkspaceEntry) => {
    if (entry.type === 'directory') { navigate(entry.path); return; }
    openController.current?.abort();
    const controller = new AbortController();
    openController.current = controller;
    setSelected(entry);
    setEditing(false);
    setPreviewBlocked(false);
    setDownloadError('');
    setError('');
    try {
      if (previewUrl) { URL.revokeObjectURL(previewUrl); setPreviewUrl(''); }
      setContent('');
      let fileSize = Number(entry.size);
      if (!entry.size || !Number.isFinite(fileSize)) {
        setOpening(true);
        fileSize = await workspaceApi.size(agentId, entry.path, controller.signal) ?? 0;
        if (fileSize > 0) {
          setSelected((current) => current?.path === entry.path
            ? { ...current, size: String(fileSize) }
            : current);
        }
      }
      if (fileSize > WORKSPACE_PREVIEW_MAX_BYTES) {
        setPreviewBlocked(true);
        return;
      }
      setOpening(true);
      if (isText(entry)) {
        setContent(await workspaceApi.read(agentId, entry.path, controller.signal));
      } else if (interactiveSpreadsheetExtension(entry) === 'xlsm') {
        setPreviewUrl(URL.createObjectURL(await workspaceApi.view(agentId, entry.path, controller.signal)));
      } else if (interactiveSpreadsheetExtension(entry) === 'xlsx') {
        setPreviewUrl(URL.createObjectURL(await workspaceApi.workbook(agentId, entry.path, controller.signal)));
      } else if (interactiveSpreadsheetExtension(entry) === 'csv') {
        setPreviewUrl(URL.createObjectURL(await workspaceApi.view(agentId, entry.path, controller.signal)));
      } else if (OFFICE_LANGUAGES.includes(entry.language ?? '')) {
        setPreviewUrl(URL.createObjectURL(await workspaceApi.preview(agentId, entry.path, controller.signal)));
      } else {
        setPreviewUrl(URL.createObjectURL(await workspaceApi.view(agentId, entry.path, controller.signal)));
      }
    } catch (cause) {
      if (controller.signal.aborted) return;
      setError(cause instanceof Error ? cause.message : 'Unable to read file');
    } finally {
      if (openController.current === controller) {
        openController.current = undefined;
        setOpening(false);
      }
    }
  };

  const downloadSelected = async () => {
    if (!selected || downloading) return;
    setDownloading(true);
    setDownloadError('');
    try {
      const blob = await workspaceApi.download(agentId, selected.path);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = selected.name;
      anchor.style.display = 'none';
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (cause) {
      setDownloadError(cause instanceof Error ? cause.message : 'Unable to download file');
    } finally {
      setDownloading(false);
    }
  };

  const openByPath = async (path: string) => {
    const absolute = path.trim().startsWith('/');
    const segments = path.split('/').filter(Boolean);
    if (!segments.length) return;
    const clean = (absolute ? '/' : '') + segments.join('/');
    const name = segments[segments.length - 1];
    // Only sync the browsable tree for workspace-relative paths; absolute paths
    // (e.g. agent-produced /tmp files) live outside the tree — just preview them.
    if (!absolute) {
      const dir = segments.slice(0, -1).join('/');
      setCwd(dir);
      void loadPath(dir);
    }
    await open({
      name,
      path: clean,
      type: 'file',
      level: Math.max(0, segments.length - 1),
      language: detectLanguage(clean),
      size: '',
      modified: '',
    });
  };

  const mutate = async (operation: () => Promise<unknown>) => {
    setPending(true); setError('');
    try { await operation(); await loadPath(cwd); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Workspace operation failed'); }
    finally { setPending(false); setUploadProgress(null); }
  };

  const uploadOne = async (file: File, path: string, currentFile = 1, totalFiles = 1) => {
    const fallbackTotal = file.size || undefined;
    setUploadProgress({ fileName: file.name, loaded: 0, total: fallbackTotal, percent: 0, currentFile, totalFiles });
    await workspaceApi.upload(agentId, path, file, (progress) => {
      const total = progress.total ?? fallbackTotal;
      setUploadProgress({
        fileName: file.name,
        loaded: progress.loaded,
        total,
        percent: progress.percent ?? (total ? Math.min(100, Math.round((progress.loaded / total) * 100)) : undefined),
        currentFile,
        totalFiles,
      });
    });
    setUploadProgress({
      fileName: file.name,
      loaded: fallbackTotal ?? file.size,
      total: fallbackTotal,
      percent: 100,
      currentFile,
      totalFiles,
    });
  };

  return {
    cwd,
    currentEntries,
    breadcrumb,
    loading,
    selected, content, previewUrl, setContent, status, pending, opening, previewBlocked,
    downloading, downloadError, error, uploadProgress,
    editing,
    canEdit: selected ? isText(selected) : false,
    startEdit: () => setEditing(true),
    cancelEdit: () => setEditing(false),
    navigate,
    refresh,
    open,
    openByPath,
    up: () => navigate(parentPath(cwd)),
    cancelOpen: () => {
      openController.current?.abort();
      openController.current = undefined;
      setOpening(false);
      setSelected(null);
    },
    retryOpen: () => selected ? open(selected) : Promise.resolve(),
    downloadSelected,
    close: () => {
      openController.current?.abort();
      openController.current = undefined;
      setOpening(false);
      setPreviewBlocked(false);
      setDownloadError('');
      if (previewUrl) URL.revokeObjectURL(previewUrl);
      setPreviewUrl('');
      setEditing(false);
      setSelected(null);
    },
    create: (name: string, type: 'file' | 'directory') => mutate(() => workspaceApi.create(agentId, {
      path: [cwd, name].filter(Boolean).join('/'), type, ...(type === 'file' ? { content: '' } : {}),
    })),
    save: () => selected ? mutate(async () => { await workspaceApi.write(agentId, selected.path, content); setEditing(false); setSelected(null); }) : Promise.resolve(),
    upload: (file: File) => mutate(() => uploadOne(file, cwd)),
    uploadFiles: (files: File[]) => files.length ? mutate(async () => {
      for (let index = 0; index < files.length; index += 1) await uploadOne(files[index], cwd, index + 1, files.length);
    }) : Promise.resolve(),
    uploadTree: (items: { file: File; relativeDir: string }[]) => items.length ? mutate(async () => {
      const join = (dir: string) => [cwd, dir].filter(Boolean).join('/');
      const dirs = [...new Set(items.map((item) => item.relativeDir).filter(Boolean))]
        .sort((a, b) => a.split('/').length - b.split('/').length);
      for (const dir of dirs) {
        try { await workspaceApi.create(agentId, { path: join(dir), type: 'directory' }); } catch { /* may already exist */ }
      }
      for (let index = 0; index < items.length; index += 1) {
        const item = items[index];
        await uploadOne(item.file, join(item.relativeDir), index + 1, items.length);
      }
    }) : Promise.resolve(),
    remove: (entry: WorkspaceEntry) => mutate(() => workspaceApi.remove(agentId, entry.path)),
  };
}
