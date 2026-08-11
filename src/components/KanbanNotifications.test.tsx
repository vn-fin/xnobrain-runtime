import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { KanbanEvent } from '../types';
import { KanbanNotifications } from './KanbanNotifications';

const event: KanbanEvent = {
  id: 42,
  taskId: 'task-42',
  title: 'Prepare the daily report',
  kind: 'completed',
  createdAt: new Date().toISOString(),
  assignee: 'news-agent',
  nativeStatus: 'done',
  status: 'done',
};

describe('KanbanNotifications', () => {
  afterEach(cleanup);

  it('shows new default-board activity and opens its task', () => {
    const openTask = vi.fn();
    render(<KanbanNotifications events={[event]} liveStatus="live" onOpenTask={openTask} />);

    expect(screen.getByText('Prepare the daily report')).toBeVisible();
    expect(screen.getByText(/Task completed/)).toBeVisible();

    fireEvent.click(screen.getByText('Prepare the daily report'));
    expect(openTask).toHaveBeenCalledWith('task-42');
  });

  it('keeps a notification history behind the bell', () => {
    render(<KanbanNotifications events={[event]} liveStatus="live" onOpenTask={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Task notifications' }));

    expect(screen.getByRole('region', { name: 'Task notifications' })).toBeVisible();
    expect(screen.getByText('task-42')).toBeVisible();
    expect(screen.getByText('Live')).toBeVisible();
  });

  it('ignores routine worker heartbeat events', () => {
    render(<KanbanNotifications
      events={[{ ...event, id: 43, kind: 'heartbeat', nativeStatus: 'running', status: 'running' }]}
      liveStatus="live"
      onOpenTask={vi.fn()}
    />);

    expect(screen.queryByText('Prepare the daily report')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Task notifications' }));
    expect(screen.getByText('No new task activity')).toBeVisible();
  });
});
