import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useKanban } from '../hooks/useKanban';
import { KanbanView } from './KanbanView';

function TestBoard() {
  const state = useKanban();
  return <KanbanView agents={[]} state={state} onClose={vi.fn()} />;
}

describe('KanbanView', () => {
  afterEach(cleanup);

  beforeEach(() => {
    window.localStorage.clear();
  });

  it('shows the available boards and switches the active task list', async () => {
    const user = userEvent.setup();
    render(<TestBoard />);

    const boardPicker = await screen.findByLabelText('Select task board');
    await waitFor(() => expect(boardPicker.querySelectorAll('option')).toHaveLength(3));

    await user.selectOptions(boardPicker, 'provider-rollout');

    expect(screen.getByRole('button', { name: 'Open P-201: Verify provider OAuth callback' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Open T-1042: Design multi-agent board shell' })).toBeNull();
  });

  it('filters tasks and keeps task details available in the board', async () => {
    const user = userEvent.setup();
    render(<TestBoard />);

    const designTask = await screen.findByRole('button', {
      name: 'Open T-1042: Design multi-agent board shell',
    });
    await user.selectOptions(screen.getByLabelText('Filter by priority'), 'low');

    expect(designTask).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open T-1051: Draft cron digest report template' })).toBeVisible();

    await user.click(screen.getByRole('button', { name: 'Clear' }));
    const restoredTask = screen.getByRole('button', {
      name: 'Open T-1042: Design multi-agent board shell',
    });
    await user.click(restoredTask);

    const drawer = screen.getByRole('dialog');
    expect(within(drawer).getByRole('heading', { name: 'Design multi-agent board shell' })).toBeVisible();
    expect(within(drawer).getByText('Update the task status without leaving the board.')).toBeVisible();
  });
});
