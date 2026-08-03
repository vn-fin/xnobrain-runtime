import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import '../i18n';
import type { Agent, ConversationUsage } from '../types';
import { ChatArea, ContextGauge } from './ChatArea';

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

describe('conversation overflow picker', () => {
  it('uses a searchable dropdown instead of expanding every session tab', () => {
    const conversations = Array.from({ length: 205 }, (_, index) => ({
      id: `session-${index + 1}`,
      title: index === 198 ? 'Quarterly risk review' : `Session ${index + 1}`,
      updated: 'now',
      messages: index,
    }));
    const agent: Agent = {
      id: 'agent-one', name: 'agent-one', title: 'Research', description: '', status: 'ready',
      provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
      skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations,
    };
    const onSelectConversation = vi.fn();
    render(<ChatArea
      agent={agent} agents={[agent]} activeConversation={conversations[0]} providers={[]}
      runs={[]} messages={[]} usage={null} chatStatus="ready" chatError="" streaming={false} canStop={false}
      onSend={vi.fn()} onStop={vi.fn()} onResolveRunApproval={vi.fn()} onRetry={vi.fn()}
      onSelectModel={vi.fn()} onOpenSettings={vi.fn()} onOpenRuntime={vi.fn()} onSelectAgent={vi.fn()}
      onTestAgent={vi.fn()} onDeleteAgent={vi.fn()} onSelectConversation={onSelectConversation}
      onCreateConversation={vi.fn()} onDeleteConversation={vi.fn()} onRenameConversation={vi.fn()}
      onOpenFile={vi.fn()}
    />);

    fireEvent.click(screen.getByRole('button', { name: '+202' }));
    const list = screen.getByRole('listbox', { name: 'Show all sessions' });
    expect(list).toBeVisible();
    expect(screen.queryByText('Show less')).not.toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Search sessions…'), { target: { value: 'quarterly risk' } });
    const options = within(list).getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Quarterly risk review');
    fireEvent.click(options[0]);
    expect(onSelectConversation).toHaveBeenCalledWith('session-199');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});
