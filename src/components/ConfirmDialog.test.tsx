import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConfirmDialog, PromptDialog } from './modals';

afterEach(cleanup);

describe('ConfirmDialog', () => {
  it('uses the shared styled alert dialog and runs the selected action', () => {
    const confirm = vi.fn();
    const cancel = vi.fn();
    render(
      <ConfirmDialog
        title="Sign out?"
        message="Are you sure you want to sign out?"
        confirmLabel="Sign out"
        danger
        onConfirm={confirm}
        onCancel={cancel}
      />,
    );

    const dialog = screen.getByRole('alertdialog', { name: 'Sign out?' });
    expect(dialog).toHaveClass('app-modal', 'confirm-modal', 'danger');

    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(confirm).toHaveBeenCalledOnce();
    expect(cancel).not.toHaveBeenCalled();
  });

  it('focuses the safe action, traps focus, and cancels with Escape', () => {
    const cancel = vi.fn();
    render(<ConfirmDialog title="Delete?" message="Permanent." confirmLabel="Delete" onConfirm={vi.fn()} onCancel={cancel} danger />);

    const dialog = screen.getByRole('alertdialog');
    const cancelButton = screen.getByRole('button', { name: 'common.cancel' });
    const confirmButton = screen.getByRole('button', { name: 'Delete' });
    expect(cancelButton).toHaveFocus();
    confirmButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(screen.getByTitle('common.close')).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(cancel).toHaveBeenCalledOnce();
  });

  it('uses the shared modal styling for text prompts', () => {
    const confirm = vi.fn();
    render(
      <PromptDialog
        title="Create folder"
        message="Create a folder in the workspace."
        label="Folder name"
        confirmLabel="Create"
        onConfirm={confirm}
        onCancel={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText('Folder name'), { target: { value: 'reports' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));

    expect(confirm).toHaveBeenCalledWith('reports');
  });

  it('shows an associated required-field error instead of silently doing nothing', () => {
    render(<PromptDialog title="Create file" label="File name" confirmLabel="Create" onConfirm={vi.fn()} onCancel={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    expect(screen.getByRole('alert')).toHaveTextContent('File name is required.');
    expect(screen.getByLabelText('File name')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('File name')).toHaveFocus();
  });
});
