import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { WorkspaceEntry } from '../types';

export function WorkspaceRowMenu({ entry, historyEnabled, onOpen, onDownload, onHistory, onEnableHistory, onRename, onCopy, onDelete }: {
  entry: WorkspaceEntry;
  historyEnabled: boolean;
  onOpen: () => void;
  onDownload: () => void;
  onHistory: () => void;
  onEnableHistory: () => void;
  onRename: () => void;
  onCopy: () => void;
  onDelete: () => void;
}) {
  const { t } = useTranslation();
  const menuId = useId();
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<CSSProperties>();
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const closeOther = (event: Event) => {
      if ((event as CustomEvent<string>).detail !== menuId) setOpen(false);
    };
    window.addEventListener('workspace-row-menu-open', closeOther);
    return () => window.removeEventListener('workspace-row-menu-open', closeOther);
  }, [menuId]);
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node) && !buttonRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    menuRef.current?.querySelector<HTMLButtonElement>('button')?.focus();
    return () => document.removeEventListener('mousedown', close);
  }, [open]);
  useLayoutEffect(() => {
    if (!open || !buttonRef.current || !menuRef.current) return;
    const anchor = buttonRef.current.getBoundingClientRect();
    const menu = menuRef.current.getBoundingClientRect();
    const left = Math.max(8, Math.min(anchor.right - menu.width, window.innerWidth - menu.width - 8));
    const below = anchor.bottom + menu.height + 8 <= window.innerHeight;
    setPosition({ position: 'fixed', left, top: below ? anchor.bottom + 2 : Math.max(8, anchor.top - menu.height - 2) });
  }, [open]);
  const act = (fn: () => void) => { setOpen(false); fn(); };
  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('button') ?? []);
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') { event.preventDefault(); setOpen(false); buttonRef.current?.focus(); }
    if (event.key === 'ArrowDown') { event.preventDefault(); items[(index + 1) % items.length]?.focus(); }
    if (event.key === 'ArrowUp') { event.preventDefault(); items[(index - 1 + items.length) % items.length]?.focus(); }
  };
  return (
    <div className="workspace-row-menu-wrap">
      <button ref={buttonRef} className="gd-more" aria-haspopup="menu" aria-expanded={open} aria-label={`Actions for ${entry.name}`} onClick={(event) => { event.stopPropagation(); setOpen((value) => { if (!value) window.dispatchEvent(new CustomEvent('workspace-row-menu-open', { detail: menuId })); return !value; }); }}>
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div ref={menuRef} className="workspace-row-menu" role="menu" style={position} onKeyDown={keyDown}>
          <button role="menuitem" onClick={() => act(onOpen)}>{entry.type === 'file' ? t('checkpoints.open') : t('common.open', 'Open')}</button>
          {entry.type === 'file' && <button role="menuitem" onClick={() => act(onDownload)}>{t('checkpoints.download')}</button>}
          {entry.type === 'file' && <button role="menuitem" onClick={() => act(historyEnabled ? onHistory : onEnableHistory)}>{historyEnabled ? t('checkpoints.versionHistory') : t('checkpoints.enableHistory')}</button>}
          <span className="workspace-menu-separator" />
          <button role="menuitem" onClick={() => act(onRename)}>{t('checkpoints.rename')}</button>
          <button role="menuitem" onClick={() => act(onCopy)}>{t('checkpoints.copyPath')}</button>
          <span className="workspace-menu-separator" />
          <button role="menuitem" className="danger" onClick={() => act(onDelete)}>{entry.type === 'directory' ? t('checkpoints.deleteFolder', 'Delete folder…') : t('checkpoints.delete')}</button>
        </div>
      )}
    </div>
  );
}
