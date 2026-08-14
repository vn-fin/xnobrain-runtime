import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { WorkspaceRowMenu } from './WorkspaceRowMenu';

const entry = { name: 'notes.md', path: 'docs/notes.md', type: 'file' as const, level: 0, size: '12', modified: '' };
afterEach(cleanup);

describe('WorkspaceRowMenu', () => {
  it('opens accessibly without activating the row and exposes safe file actions', () => {
    const open = vi.fn();
    const history = vi.fn();
    render(<WorkspaceRowMenu entry={entry} historyEnabled onOpen={open} onDownload={vi.fn()} onHistory={history} onEnableHistory={vi.fn()} onRename={vi.fn()} onCopy={vi.fn()} onDelete={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Actions for notes.md' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    fireEvent.click(trigger);
    expect(open).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('menuitem', { name: 'Version history' }));
    expect(history).toHaveBeenCalledOnce();
  });

  it('supports keyboard close and returns focus to the trigger', () => {
    render(<WorkspaceRowMenu entry={entry} historyEnabled={false} onOpen={vi.fn()} onDownload={vi.fn()} onHistory={vi.fn()} onEnableHistory={vi.fn()} onRename={vi.fn()} onCopy={vi.fn()} onDelete={vi.fn()} />);
    const trigger = screen.getByRole('button', { name: 'Actions for notes.md' });
    fireEvent.keyDown(trigger, { key: 'Enter' });
    fireEvent.click(trigger);
    const menu = screen.getByRole('menu');
    expect(screen.getByRole('menuitem', { name: 'Enable version history…' })).toBeInTheDocument();
    fireEvent.keyDown(menu, { key: 'Escape' });
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
