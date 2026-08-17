import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { UsageSummary } from '../api/analytics';
import type { AnalyticsState } from '../hooks/useAnalytics';
import type { WorkspaceAnalyticsData } from './AnalyticsView';
import { AnalyticsView } from './AnalyticsView';

const totals = {
  input_tokens: 1200,
  output_tokens: 300,
  total_tokens: 1500,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  reasoning_tokens: 0,
  estimated_cost_usd: 0.25,
  actual_cost_usd: 0,
  cost_usd: 0.25,
  cost_basis: 'estimated' as const,
  sessions: 1,
  api_calls: 2,
};

const summary: UsageSummary = {
  range_from: 0,
  range_to: 1,
  period_days: 1,
  bucket: 'hour',
  agents_selected: [],
  agents_available: 0,
  generated_at: '2026-07-27T10:00:00Z',
  timezone: 'UTC',
  totals,
  agents: [],
  by_model: [],
  by_provider: [],
  series: [{
    bucket: '2026-07-27T10',
    input_tokens: 1200,
    output_tokens: 300,
    total_tokens: 1500,
    cost_usd: 0.25,
    cost_basis: 'estimated',
    sessions: 2,
  }],
  quota: { available: false, provider: '', model: '', plan: '', message: '', quotas: [] },
  source: { kind: 'provider_runtime', durable: true, label: '', message: '' },
  request_status: { total: 2, successful: 2, failed: 0, success_rate: 100 },
  attribution: {
    live_totals: totals,
    attributed_tokens: 1500,
    unattributed_tokens: 0,
    coverage_percent: 100,
    deleted_usage_included: true,
  },
};

const workspace: WorkspaceAnalyticsData = {
  agents: [
    {
      id: 'agent-1',
      title: 'Builder',
      name: 'builder',
      description: '',
      status: 'ready',
      provider: 'openai',
      model: 'gpt-5',
      reasoningEffort: 'medium',
      approvalMode: 'auto',
      skillsWriteApproval: true,
      memoryWriteApproval: true,
      workspace: '',
      skills: [],
      conversations: [],
    },
  ],
  teams: [
    {
      id: 'team-1',
      name: 'Delivery',
      orchestrator_id: 'agent-1',
      members: [{ agent_id: 'agent-1', role: 'builder', allowed_tools: [], enabled: true }],
      workflow: [{ id: 'build', task: 'Build', agent_id: 'agent-1' }],
      shared_workspace: true,
      max_parallel: 1,
      max_depth: 1,
      enabled: true,
    },
  ],
  teamStatus: 'ready',
  boards: [
    {
      id: 'default',
      name: 'Tasks',
      description: '',
      color: '#4f8cff',
      statuses: [],
      tasks: [
        {
          id: 'task-1',
          title: 'Ship dashboard',
          description: '',
          status: 'running',
          nativeStatus: 'running',
          allowedStatuses: ['done'],
          priority: 'high',
          assignees: ['agent-1'],
          tags: [],
          skills: [],
          deps: [],
          comments: [],
          events: [],
          runs: [],
          workerActivity: null,
          conversation: null,
          progress: 50,
          updated: 'now',
          schedule: null,
          team: null,
        },
      ],
    },
  ],
  kanbanStatus: 'ready',
};

describe('AnalyticsView chart', () => {
  afterEach(cleanup);

  it('shows exact bucket details when its full-height chart region is hovered', () => {
    const state = {
      controls: { agents: [], days: 1, bucket: 'hour' },
      setControls: vi.fn(),
      available: [],
      summary,
      status: 'ready',
      error: null,
      generatedAt: summary.generated_at,
      refresh: vi.fn(),
      setBudget: vi.fn(),
    } as AnalyticsState;
    const { container } = render(<AnalyticsView state={state} onClose={vi.fn()} />);

    const hitTarget = container.querySelector('.analytics-chart-hit-target') as SVGRectElement;
    expect(hitTarget).toHaveAttribute('height', '193');
    fireEvent.mouseEnter(hitTarget);

    expect(screen.getByRole('status')).toHaveTextContent('2026-07-27T10');
    expect(screen.getByRole('status')).toHaveTextContent('1,500');
    expect(screen.getByRole('status')).toHaveTextContent('1,200');
    expect(screen.getByRole('status')).toHaveTextContent('$0.2500');
  });

  it('puts the trend in its own row and token, model, and provider panels together', () => {
    const state = {
      controls: { agents: [], days: 1, bucket: 'hour' },
      setControls: vi.fn(),
      available: [],
      summary,
      status: 'ready',
      error: null,
      generatedAt: summary.generated_at,
      refresh: vi.fn(),
      setBudget: vi.fn(),
    } as AnalyticsState;
    const { container } = render(<AnalyticsView state={state} onClose={vi.fn()} />);

    const trendGrid = container.querySelector('.analytics-trend-grid');
    const breakdownGrid = container.querySelector('.analytics-breakdown-grid');
    expect(trendGrid?.querySelectorAll(':scope > .analytics-panel')).toHaveLength(1);
    expect(breakdownGrid?.querySelectorAll(':scope > .analytics-panel')).toHaveLength(3);
    expect(breakdownGrid).toHaveTextContent('Token mix');
    expect(breakdownGrid).toHaveTextContent('Models');
    expect(breakdownGrid).toHaveTextContent('Providers');
  });

  it('summarizes agents, Kanban tasks, and team workflows from their tab data', () => {
    const state = {
      controls: { agents: [], days: 1, bucket: 'hour' },
      setControls: vi.fn(),
      available: [],
      summary,
      status: 'ready',
      error: null,
      generatedAt: summary.generated_at,
      refresh: vi.fn(),
      setBudget: vi.fn(),
    } as AnalyticsState;
    const onNavigate = vi.fn();
    render(<AnalyticsView state={state} workspace={workspace} onNavigate={onNavigate} onClose={vi.fn()} />);

    expect(screen.getByRole('heading', { name: 'Operations at a glance' })).toBeVisible();
    expect(screen.getByText('configured profiles')).toBeVisible();
    expect(screen.getByText('tasks across 1 board')).toBeVisible();
    expect(screen.getByText('saved workflows')).toBeVisible();

    fireEvent.click(screen.getByRole('button', { name: 'Open Kanban' }));
    expect(onNavigate).toHaveBeenCalledWith('kanban');
  });

  it('blocks the Analytics surface and shows API progress while loading', () => {
    const state = {
      controls: { agents: [], days: 30, bucket: 'day' },
      setControls: vi.fn(),
      available: [],
      summary,
      status: 'loading',
      progress: { completed: 2, total: 3 },
      error: null,
      generatedAt: summary.generated_at,
      refresh: vi.fn(),
      setBudget: vi.fn(),
    } as AnalyticsState;
    const { container } = render(<AnalyticsView state={state} onClose={vi.fn()} />);

    expect(container.querySelector('.analytics-page')).toHaveAttribute('aria-busy', 'true');
    expect(container.querySelector('.analytics-content')).toHaveAttribute('inert');
    expect(screen.getByText('Loading analytics').closest('[role="status"]')).toBeVisible();
    expect(screen.getByRole('progressbar', { name: 'Analytics API loading progress' })).toHaveAttribute('aria-valuenow', '2');
  });
});
