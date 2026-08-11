import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ConfirmDialog, PromptDialog } from './modals';

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
});
