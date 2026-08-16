import { lazy, Suspense, useEffect, useState } from 'react';
import { FilePenLine, FileText, LoaderCircle, Pencil, X } from 'lucide-react';
import type { Agent } from '../../types';
import { TreeIcon } from '../../components/common';
import { ConfirmDialog } from '../../components/modals';
import { NAVIGATION_REQUEST_EVENT, type NavigationRequestDetail } from '../../utils/navigationGuard';

const CodeViewer = lazy(() => import('../../components/CodeViewer'));
const NumberedTextEditor = lazy(() => import('../../components/CodeViewer').then((module) => ({
  default: module.NumberedTextEditor,
})));

export type ContextFileName = 'SOUL.md' | 'AGENTS.md';

type ContextFilesSectionProps = {
  agents: Agent[];
  onLoadFile: (agentId: string, file: ContextFileName) => Promise<string | null>;
  onSaveFile: (agentId: string, file: ContextFileName, content: string) => Promise<void>;
};

const FILES: Array<{ name: ContextFileName; title: string; description: string }> = [
  {
    name: 'SOUL.md',
    title: 'Agent personality',
    description: 'Identity, tone, and behavioral principles for the selected agent.',
  },
  {
    name: 'AGENTS.md',
    title: 'Workspace instructions',
    description: 'Instructions for working with files and deliverables in the agent workspace.',
  },
];

export function ContextFilesSection({ agents, onLoadFile, onSaveFile }: ContextFilesSectionProps) {
  const [agentId, setAgentId] = useState(agents[0]?.id ?? '');
  const [activeFile, setActiveFile] = useState<ContextFileName | null>(null);
  const [content, setContent] = useState('');
  const [draft, setDraft] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [discardOpen, setDiscardOpen] = useState(false);
  const [pendingNavigation, setPendingNavigation] = useState<(() => void) | null>(null);

  const dirty = editing && (creating || draft !== content);

  useEffect(() => {
    if (!dirty) return undefined;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [dirty]);

  useEffect(() => {
    if (!dirty) return undefined;
    const guardNavigation = (rawEvent: Event) => {
      const event = rawEvent as CustomEvent<NavigationRequestDetail>;
      event.preventDefault();
      setPendingNavigation(() => event.detail.proceed);
      setDiscardOpen(true);
    };
    window.addEventListener(NAVIGATION_REQUEST_EVENT, guardNavigation);
    return () => window.removeEventListener(NAVIGATION_REQUEST_EVENT, guardNavigation);
  }, [dirty]);

  useEffect(() => {
    if (!agents.some((agent) => agent.id === agentId)) setAgentId(agents[0]?.id ?? '');
  }, [agentId, agents]);

  const contextEntry = activeFile ? {
    name: activeFile,
    path: activeFile,
    type: 'file' as const,
    level: 0,
    language: 'markdown' as const,
    size: String(content.length),
    modified: '',
  } : null;

  const loadFile = async (file: ContextFileName) => {
    if (!agentId) return;
    setActiveFile(file);
    setEditing(false);
    setCreating(false);
    setStatus('loading');
    setError('');
    try {
      const value = await onLoadFile(agentId, file);
      const missing = value === null;
      const nextContent = value ?? '';
      setContent(nextContent);
      setDraft(nextContent);
      setCreating(missing);
      setEditing(missing);
      setStatus('ready');
    } catch (value) {
      setError(value instanceof Error ? value.message : `Could not load ${file}.`);
      setStatus('error');
    }
  };

  const requestLoad = (file: ContextFileName) => {
    if (saving) return;
    if (dirty) {
      setPendingNavigation(() => () => void loadFile(file));
      setDiscardOpen(true);
      return;
    }
    void loadFile(file);
  };

  const requestAgentChange = (nextAgentId: string) => {
    if (saving || nextAgentId === agentId) return;
    const change = () => {
      setAgentId(nextAgentId);
      setActiveFile(null);
      setEditing(false);
      setCreating(false);
      setStatus('idle');
      setError('');
    };
    if (dirty) {
      setPendingNavigation(() => change);
      setDiscardOpen(true);
      return;
    }
    change();
  };

  const discardAndClose = () => {
    if (saving) return;
    setDiscardOpen(false);
    setActiveFile(null);
    setEditing(false);
    setCreating(false);
    setStatus('idle');
    setError('');
    const proceed = pendingNavigation;
    setPendingNavigation(null);
    if (proceed) window.setTimeout(proceed, 0);
  };

  const close = () => {
    if (saving) return;
    if (dirty) { setDiscardOpen(true); return; }
    discardAndClose();
  };

  useEffect(() => {
    if (!activeFile || discardOpen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      close();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  });

  const save = async () => {
    if (!activeFile || !agentId || saving || (!creating && draft === content)) return;
    setSaving(true);
    setError('');
    try {
      await onSaveFile(agentId, activeFile, draft);
      setContent(draft);
      setCreating(false);
      setEditing(false);
    } catch (value) {
      setError(value instanceof Error ? value.message : `Could not save ${activeFile}.`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="system-card context-files-card">
      <div className="system-card-title">
        <FilePenLine size={18} />
        <div>
          <strong>Context files</strong>
          <small>Preview or edit the selected agent&apos;s Markdown context.</small>
        </div>
      </div>

      <label className="context-agent-picker">
        <span>Agent profile</span>
        <select
          aria-label="Agent profile"
          value={agentId}
          disabled={agents.length === 0}
          onChange={(event) => requestAgentChange(event.target.value)}
        >
          {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.title}</option>)}
        </select>
      </label>

      {agents.length === 0 ? (
        <div className="context-files-empty">Create an agent before editing context files.</div>
      ) : (
        <div className="context-file-grid">
          {FILES.map((file) => (
            <button
              key={file.name}
              type="button"
              className="context-file-card"
              aria-label={`Open ${file.name}`}
              onClick={() => requestLoad(file.name)}
            >
              <FileText size={20} />
              <span><strong>{file.name}</strong><small>{file.title}</small></span>
              <small>{file.description}</small>
            </button>
          ))}
        </div>
      )}

      {activeFile && (
        <div className="workspace-editor-backdrop" onClick={close}>
          <div
            className="workspace-editor"
            role="dialog"
            aria-modal="true"
            aria-label={`${activeFile} context file`}
            onClick={(event) => event.stopPropagation()}
          >
            <header>
              <span className="gd-editor-title">
                {contextEntry && <TreeIcon entry={contextEntry} size={18} />}
                <strong>{activeFile}</strong>
              </span>
              <div className="gd-editor-actions">
                {status === 'ready' && !editing && (
                  <button className="conn-btn ghost" onClick={() => { setDraft(content); setEditing(true); }}>
                    <Pencil size={14} />Edit
                  </button>
                )}
                <button className="gd-editor-close" title="Close" onClick={close}><X size={16} /></button>
              </div>
            </header>

            <div className="gd-viewer">
              {status === 'loading' && <div className="context-file-viewer-state context-status" role="status">Loading {activeFile}…</div>}
              {status === 'error' && (
                <div className="context-file-viewer-state context-error" role="alert">
                  <span>{error}</span>
                  <button className="conn-btn ghost" onClick={() => void loadFile(activeFile)}>Try again</button>
                </div>
              )}
              {status === 'ready' && (
                <>
                  {error && <div className="context-error" role="alert">{error}</div>}
                  {editing ? (
                    <Suspense fallback={<div className="context-file-viewer-state context-status">Opening editor…</div>}>
                      <NumberedTextEditor
                        ariaLabel={`Edit ${activeFile}`}
                        autoFocus
                        value={draft}
                        disabled={saving}
                        onChange={setDraft}
                      />
                    </Suspense>
                  ) : (
                    <Suspense fallback={<div className="context-file-viewer-state context-status">Opening {activeFile}…</div>}>
                      <CodeViewer content={content} path={activeFile} fillViewport />
                    </Suspense>
                  )}
                </>
              )}
            </div>

            {status === 'ready' && editing && (
              <footer>
                {saving && <span className="gd-saving"><LoaderCircle className="run-step-spin" size={14} />Saving…</span>}
                <button
                  className="conn-btn ghost"
                  disabled={saving}
                  onClick={() => {
                    if (creating) close();
                    else { setDraft(content); setEditing(false); setError(''); }
                  }}
                >
                  Cancel
                </button>
                <button
                  className="conn-btn primary"
                  disabled={saving || (!creating && draft === content)}
                  onClick={() => void save()}
                >
                  {creating ? 'Create' : 'Save'}
                </button>
              </footer>
            )}
          </div>
        </div>
      )}

      {discardOpen && activeFile && (
        <ConfirmDialog
          title="Discard unsaved changes?"
          message={`Your unsaved changes to ${activeFile} will be lost.`}
          confirmLabel="Discard changes"
          danger
          onConfirm={discardAndClose}
          onCancel={() => { setDiscardOpen(false); setPendingNavigation(null); }}
        />
      )}
    </section>
  );
}
