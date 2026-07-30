import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import '../i18n';
import type { Agent, CronJob } from '../types';
import { CronView } from './CronView';

afterEach(cleanup);

const agent: Agent = {
  id: 'agent-1', name: 'agent-1', title: 'Research Lead', description: '', status: 'ready',
  provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
  skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations: [],
};

const job: CronJob = {
  id: 'job-1', agentId: 'agent-1', name: 'Morning summary', state: 'scheduled',
  schedule: '@every 60m', intervalMinutes: 60, nextRun: '2026-07-29T12:00:00Z', prompt: 'Prepare a summary',
};

describe('CronView', () => {
  it('shows a friendly agent-scoped card and filters by assistant', () => {
    render(<CronView agents={[agent]} crons={[job]} status="ready" error="" pendingId="" onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByRole('heading', { name: 'Scheduled tasks' })).toBeInTheDocument();
    expect(screen.getByText('Morning summary')).toBeInTheDocument();
    expect(screen.getByText('Every 1 hour')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'agent-1' } });
    expect(screen.getAllByText('Research Lead').length).toBeGreaterThan(0);
  });

  it('opens the simple scheduling form and submits an assistant selection', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(<CronView agents={[agent]} crons={[]} status="ready" error="" pendingId="" onCreate={onCreate} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getAllByRole('button', { name: /new cron/i })[0]);
    fireEvent.change(screen.getByPlaceholderText(/check server status/i), { target: { value: 'Weekly report' } });
    fireEvent.change(screen.getByPlaceholderText(/what should run/i), { target: { value: 'Summarize updates' } });
    fireEvent.click(screen.getByRole('button', { name: /create cron/i }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith(expect.objectContaining({ agentId: 'agent-1', intervalMinutes: 60 })));
  });

  it('opens a job detail view and shows the latest result', () => {
    const onLoadDetail = vi.fn().mockResolvedValue(undefined);
    render(<CronView agents={[agent]} crons={[job]} status="ready" error="" pendingId="" detail={{ job, run: { id: 'run-1', state: 'success', triggeredAt: '2026-07-29T12:00:00Z', completedAt: '2026-07-29T12:01:00Z', output: 'HPG report ready', error: '' } }} onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} onLoadDetail={onLoadDetail} onCloseDetail={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /morning summary/i }));
    expect(onLoadDetail).toHaveBeenCalledWith('job-1');
    expect(screen.getByRole('dialog', { name: /scheduled task details/i })).toBeInTheDocument();
    expect(screen.getByText('HPG report ready')).toBeInTheDocument();
  });

  it('creates an automation from the blueprint gallery', async () => {
    const onInstantiateBlueprint = vi.fn().mockResolvedValue(job);
    render(<CronView agents={[agent]} crons={[]} status="ready" error="" pendingId="" blueprints={[{ key: 'morning-brief', title: 'Morning briefing', description: 'Start the day informed', category: 'daily', tags: [], schedule: '0 8 * * *', scheduleHuman: 'daily at 08:00', fields: [{ name: 'time', type: 'time', label: 'What time?', default: '08:00' }] }]} onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} onInstantiateBlueprint={onInstantiateBlueprint} />);
    fireEvent.click(screen.getByRole('button', { name: 'Blueprints' }));
    fireEvent.click(screen.getByRole('button', { name: /use blueprint/i }));
    fireEvent.click(screen.getByRole('button', { name: /create automation/i }));
    await waitFor(() => expect(onInstantiateBlueprint).toHaveBeenCalledWith(expect.objectContaining({ blueprint: 'morning-brief', agentId: 'agent-1' })));
  });
});
