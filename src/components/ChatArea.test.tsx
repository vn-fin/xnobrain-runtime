import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import type { ConversationUsage } from '../types';
import { ContextGauge } from './ChatArea';

const usage = {
  conversationId: 'session-one',
  messages: 2,
  apiCalls: 1,
  model: 'cx/gpt-5.6-luna',
  totalTokens: 42_000,
  contextUsed: 10_000,
  contextLimit: 200_000,
  contextPercent: 5,
  totalCostUsd: 0,
  provider: '',
  plan: '',
  quotaAvailable: false,
  quotaMessage: '',
  limits: [],
} satisfies ConversationUsage;

describe('ContextGauge', () => {
  afterEach(cleanup);

  it('renders current conversation context as a model-window percentage', () => {
    render(<ContextGauge usage={usage} model="cx/gpt-5.6-luna" />);

    const gauge = screen.getByRole('img', { name: '10K / 200K context (5%)' });
    expect(gauge).toHaveStyle({ '--context-percent': '5%' });
    expect(gauge).not.toHaveClass('unknown');
  });

  it('does not invent a context limit for Auto', () => {
    render(<ContextGauge usage={{ ...usage, contextLimit: undefined, contextPercent: undefined }} model="auto" />);

    expect(screen.getByRole('img')).toHaveClass('unknown');
    expect(screen.getByRole('img')).toHaveAttribute(
      'aria-label',
      '10K context used; Auto model context limit is unavailable',
    );
  });
});
