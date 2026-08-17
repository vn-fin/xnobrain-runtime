import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import '../i18n';
import type { Agent, CronJob } from '../types';
import { CronView } from './CronView';

afterEach(cleanup);

const agent: Agent = {
  id: 'agent-1', name: 'agent-1', title: 'Research Lead', description: '', status: 'ready',
  provider: 'xnobrain', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
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

  it('shows profile loading independently while keeping completed profile stats visible', () => {
    const second = { ...agent, id: 'agent-2', name: 'agent-2', title: 'Writer' };
    render(<CronView agents={[agent, second]} crons={[job]} status="loading" profileStates={{ 'agent-1': 'ready', 'agent-2': 'loading' }} error="" pendingId="" onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} />);

    expect(screen.getByText('Morning summary')).toBeInTheDocument();
    expect(screen.getByText('1 task')).toBeInTheDocument();
    expect(screen.getByText('Loading this profile’s schedules…')).toBeInTheDocument();
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

  it('explains every invalid cron field and focuses the first error', async () => {
    const onCreate = vi.fn().mockResolvedValue(undefined);
    render(<CronView agents={[agent]} crons={[]} status="ready" error="" pendingId="" onCreate={onCreate} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getAllByRole('button', { name: /new cron/i })[0]);
    const name = screen.getByPlaceholderText(/check server status/i);
    const prompt = screen.getByPlaceholderText(/what should run/i);
    fireEvent.change(name, { target: { value: '   ' } });
    fireEvent.change(prompt, { target: { value: '\t' } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: /create cron/i }));

    expect(await screen.findByText('Name is required.')).toBeInTheDocument();
    expect(screen.getByText('Prompt is required.')).toBeInTheDocument();
    expect(screen.getByText('Interval must be at least 1 minute.')).toBeInTheDocument();
    expect(name).toHaveFocus();
    expect(name).toHaveAttribute('aria-invalid', 'true');
    expect(prompt).toHaveAttribute('aria-invalid', 'true');
    expect(onCreate).not.toHaveBeenCalled();

    fireEvent.change(name, { target: { value: 'Weekly report' } });
    fireEvent.change(prompt, { target: { value: 'Summarize updates' } });
    fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '15' } });
    fireEvent.click(screen.getByRole('button', { name: /create cron/i }));
    await waitFor(() => expect(onCreate).toHaveBeenCalledOnce());
  });

  it('does not delete an automation until its named confirmation is accepted', async () => {
    const onDelete = vi.fn().mockResolvedValue(undefined);
    render(<CronView agents={[agent]} crons={[job]} status="ready" error="" pendingId="" onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={onDelete} />);

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    expect(onDelete).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog', { name: 'Delete Morning summary?' })).toHaveTextContent('run history');
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onDelete).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete automation' }));
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith('job-1', 'agent-1'));
  });

  it('opens a job detail view and shows the latest result', () => {
    const onLoadDetail = vi.fn().mockResolvedValue(undefined);
    render(<CronView agents={[agent]} crons={[job]} status="ready" error="" pendingId="" detail={{ job, run: { id: 'run-1', state: 'success', triggeredAt: '2026-07-29T12:00:00Z', completedAt: '2026-07-29T12:01:00Z', output: 'HPG report ready', error: '' } }} onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()} onLoadDetail={onLoadDetail} onCloseDetail={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: /morning summary/i }));
    expect(onLoadDetail).toHaveBeenCalledWith('job-1', 'agent-1');
    expect(screen.getByRole('dialog', { name: /scheduled task details/i })).toBeInTheDocument();
    expect(screen.getByText('HPG report ready')).toBeInTheDocument();
  });

  it('rejects unavailable destinations and confirms delivery-target removal', async () => {
    const onAddTarget = vi.fn().mockResolvedValue(undefined);
    const onRemoveTarget = vi.fn().mockResolvedValue(undefined);
    const detail = {
      job,
      run: null,
      targets: [{ id: 'target-1', targetType: 'file' as const, destination: 'reports/daily.md', available: true }],
    };
    render(<CronView
      agents={[agent]} crons={[job]} status="ready" error="" pendingId="" detail={detail}
      deliveryOptions={[{ targetType: 'email', id: 'email', name: 'Email', available: false, degradedReason: 'Not configured' }]}
      onCreate={vi.fn()} onToggle={vi.fn()} onRun={vi.fn()} onDelete={vi.fn()}
      onLoadDetail={vi.fn()} onAddTarget={onAddTarget} onRemoveTarget={onRemoveTarget}
    />);
    fireEvent.click(screen.getByRole('button', { name: /morning summary/i }));
    fireEvent.click(screen.getByRole('button', { name: /manage delivery targets/i }));
    const composerSelects = screen.getAllByRole('combobox');
    fireEvent.change(composerSelects[composerSelects.length - 1], { target: { value: 'email' } });
    expect(screen.getByRole('button', { name: /add destination/i })).toBeDisabled();
    expect(onAddTarget).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Remove file target' }));
    expect(onRemoveTarget).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog', { name: /remove delivery from morning summary/i })).toHaveTextContent('reports/daily.md');
    fireEvent.click(screen.getByRole('button', { name: 'Remove destination' }));
    await waitFor(() => expect(onRemoveTarget).toHaveBeenCalledWith('job-1', 'target-1', 'agent-1'));
  });

  it('opens and closes a routed job through URL navigation state', async () => {
    const onLoadDetail = vi.fn().mockResolvedValue(undefined);
    const onNavigate = vi.fn();
    const props = {
      agents: [agent], crons: [job], status: 'ready' as const, error: '', pendingId: '',
      detail: { job, run: null }, onCreate: vi.fn(), onToggle: vi.fn(), onRun: vi.fn(),
      onDelete: vi.fn(), onLoadDetail, onCloseDetail: vi.fn(), onNavigate,
    };
    const { rerender } = render(<CronView {...props} routeJobId="" routeAgentId="" />);

    fireEvent.click(screen.getByRole('button', { name: /morning summary/i }));
    expect(onNavigate).toHaveBeenCalledWith('job-1', 'agent-1');

    rerender(<CronView {...props} routeJobId="job-1" routeAgentId="agent-1" />);
    await waitFor(() => expect(onLoadDetail).toHaveBeenCalledWith('job-1', 'agent-1'));
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    expect(onNavigate).toHaveBeenLastCalledWith('', '');
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
