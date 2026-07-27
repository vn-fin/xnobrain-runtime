import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { UsageSummary } from '../api/analytics';
import type { AnalyticsState } from '../hooks/useAnalytics';
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
  source: { kind: 'nine_router', durable: true, label: '', message: '' },
  request_status: { total: 2, successful: 2, failed: 0, success_rate: 100 },
  attribution: {
    live_totals: totals,
    attributed_tokens: 1500,
    unattributed_tokens: 0,
    coverage_percent: 100,
    deleted_usage_included: true,
  },
};

describe('AnalyticsView chart', () => {
  afterEach(cleanup);

  it('shows exact bucket details when a bar is hovered', () => {
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

    fireEvent.mouseEnter(container.querySelector('.analytics-chart-bar') as SVGRectElement);

    expect(screen.getByRole('status')).toHaveTextContent('2026-07-27T10');
    expect(screen.getByRole('status')).toHaveTextContent('1,500');
    expect(screen.getByRole('status')).toHaveTextContent('1,200');
    expect(screen.getByRole('status')).toHaveTextContent('$0.2500');
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
