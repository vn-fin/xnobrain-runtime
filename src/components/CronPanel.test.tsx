import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '../i18n';
import type { CronJob } from '../types';
import { CronPanel } from './CronPanel';

const job: CronJob = { id: 'job-1', agentId: 'agent-1', name: 'Morning review', state: 'scheduled', schedule: '@every 120m', intervalMinutes: 120, nextRun: '2026-07-29T12:00:00Z', prompt: 'Review the inbox' };

describe('CronPanel', () => {
  it('creates an interval job from the visible form', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(<CronPanel crons={[]} status="ready" error="" pendingId="" onCreate={onCreate} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /new cron/i }));
    fireEvent.change(screen.getByPlaceholderText(/check server status/i), { target: { value: 'Health check' } });
    fireEvent.change(screen.getByPlaceholderText(/what should run/i), { target: { value: 'Check the service' } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: /create cron/i }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith({ name: 'Health check', prompt: 'Check the service', intervalMinutes: 30 }));
  });

  it('renders gateway status and exposes run, pause, and delete actions', async () => {
    const onToggle = vi.fn().mockResolvedValue(undefined);
    const onRun = vi.fn().mockResolvedValue(undefined);
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<CronPanel crons={[job]} status="ready" error="" pendingId="" onCreate={vi.fn()} onToggle={onToggle} onRun={onRun} onDelete={onDelete} />);
    expect(screen.getByText('scheduled')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /run now/i }));
    fireEvent.click(screen.getByRole('button', { name: /stop/i }));
    fireEvent.click(screen.getByRole('button', { name: /delete/i }));
    await waitFor(() => {
      expect(onRun).toHaveBeenCalledWith('job-1');
      expect(onToggle).toHaveBeenCalledWith('job-1');
      expect(onDelete).toHaveBeenCalledWith('job-1');
    });
  });
});
