import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

afterEach(cleanup);
import type { Agent } from '../../types';
import { requestNavigation } from '../../utils/navigationGuard';
import { ContextFilesSection } from './ContextFilesSection';

const agent: Agent = {
  id: 'writer', name: 'writer', title: 'Writer', description: '', status: 'ready',
  provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
  skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations: [],
};

describe('ContextFilesSection dirty guard', () => {
  it('keeps editing by default and confirms close or SPA navigation', async () => {
    const proceed = vi.fn();
    render(<ContextFilesSection agents={[agent]} onLoadFile={vi.fn(async () => '# Original')} onSaveFile={vi.fn()} />);

    fireEvent.click(screen.getAllByRole('button', { name: 'Open SOUL.md' })[0]);
    await screen.findByText('# Original');
    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    const editor = await screen.findByRole('textbox', { name: 'Edit SOUL.md' });
    fireEvent.change(editor, { target: { value: '# Changed' } });
    fireEvent.click(screen.getByTitle('Close'));

    expect(screen.getByRole('alertdialog', { name: 'Discard unsaved changes?' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'common.cancel' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
    expect(screen.getByRole('dialog', { name: 'SOUL.md context file' })).toBeVisible();
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());

    act(() => { expect(requestNavigation(proceed)).toBe(false); });
    expect(proceed).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole('button', { name: 'Discard changes' }));
    await waitFor(() => expect(proceed).toHaveBeenCalledOnce());
  });

  it('confirms before switching context files or agent profiles with a dirty draft', async () => {
    render(<ContextFilesSection agents={[agent, { ...agent, id: 'reviewer', title: 'Reviewer' }]} onLoadFile={vi.fn(async (_id, file) => `# ${file}`)} onSaveFile={vi.fn()} />);

    fireEvent.click(screen.getAllByRole('button', { name: 'Open SOUL.md' })[0]);
    await screen.findByRole('dialog', { name: 'SOUL.md context file' });
    fireEvent.click(screen.getByRole('button', { name: /Edit/ }));
    fireEvent.change(await screen.findByRole('textbox', { name: 'Edit SOUL.md' }), { target: { value: '# Changed' } });

    fireEvent.click(screen.getAllByRole('button', { name: 'Open AGENTS.md' })[0]);
    expect(screen.getByRole('alertdialog', { name: 'Discard unsaved changes?' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'common.cancel' }));
    expect(screen.getByRole('dialog', { name: 'SOUL.md context file' })).toBeVisible();

    fireEvent.change(screen.getAllByLabelText('Agent profile')[0], { target: { value: 'reviewer' } });
    expect(screen.getByRole('alertdialog', { name: 'Discard unsaved changes?' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'SOUL.md context file' })).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByLabelText('Agent profile')[0]).toHaveValue('reviewer'));
  });
});
