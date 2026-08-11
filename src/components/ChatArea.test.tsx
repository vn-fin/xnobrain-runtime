import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import '../i18n';
import type { Agent, ChatRun, Conversation, ConversationUsage } from '../types';
import { ChatArea, ContextGauge } from './ChatArea';
import { UserMessage } from './UserMessage';

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

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  document.head.querySelectorAll('style[data-chat-area-test]').forEach((element) => element.remove());
});

describe('ContextGauge', () => {
  it('renders current conversation context as a model-window percentage', () => {
    render(<ContextGauge usage={usage} model="cx/gpt-5.6-luna" />);

    const gauge = screen.getByRole('img', { name: '10K / 200K model context (5%)' });
    expect(gauge).toHaveStyle({ '--context-percent': '5%' });
    expect(gauge).not.toHaveClass('unknown');
    expect(gauge).toHaveTextContent('10K');
  });

  it('adapts its compact label and color as compaction approaches', () => {
    const { rerender } = render(<ContextGauge usage={{
      ...usage,
      contextUsed: 67_000,
      contextThreshold: 100_000,
      contextPressurePercent: 67,
      contextAutoCompaction: true,
    }} model="cx/gpt-5.6-luna" />);

    expect(screen.getByRole('img')).toHaveClass('elevated');
    expect(screen.getByRole('img')).toHaveTextContent('67%');
    expect(screen.getByText('67K / 100K to compaction')).toBeInTheDocument();

    rerender(<ContextGauge usage={{
      ...usage,
      contextUsed: 88_000,
      contextThreshold: 100_000,
      contextPressurePercent: 88,
      contextAutoCompaction: true,
    }} model="cx/gpt-5.6-luna" />);
    expect(screen.getByRole('img')).toHaveClass('critical');
    expect(screen.getByRole('img')).toHaveTextContent('88% · Soon');
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

describe('manual context compaction', () => {
  it('confirms an in-place session action with optional preservation guidance', async () => {
    const user = userEvent.setup();
    const conversation: Conversation = {
      id: 'session-one', title: 'Research', preview: '', model: 'auto', messages: 4, tools: 0,
    };
    const agent: Agent = {
      id: 'agent-one', name: 'agent-one', title: 'Research', description: '', status: 'ready',
      provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
      skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations: [conversation],
    };
    const onCompactContext = vi.fn().mockResolvedValue({
      conversationId: 'session-one', beforeTokens: 84_000, afterTokens: 29_000,
      messagesBefore: 4, messagesAfter: 2,
    });
    render(<ChatArea
      agent={agent} agents={[agent]} activeConversation={conversation} providers={[]}
      runs={[]} messages={[
        { id: 1, role: 'user', content: 'one' },
        { id: 2, role: 'assistant', content: 'two' },
        { id: 3, role: 'user', content: 'three' },
        { id: 4, role: 'assistant', content: 'four' },
      ]}
      usage={{ ...usage, messages: 4, contextThreshold: 100_000, contextPressurePercent: 10 }}
      chatStatus="ready" chatError="" streaming={false} canStop={false}
      onCompactContext={onCompactContext}
      onSend={vi.fn()} onStop={vi.fn()} onResolveRunApproval={vi.fn()} onRetry={vi.fn()}
      onSelectModel={vi.fn()} onSelectAgent={vi.fn()} onTestAgent={vi.fn()}
      onSelectConversation={vi.fn()} onCreateConversation={vi.fn()} onDeleteConversation={vi.fn()}
      onRenameConversation={vi.fn()} onOpenFile={vi.fn()}
    />);

    await user.click(screen.getByRole('button', { name: /Session context.*10K tokens used/ }));
    expect(screen.getByRole('dialog', { name: 'Session context' })).toBeVisible();
    await user.click(screen.getByRole('button', { name: 'Compact context' }));
    expect(screen.getByText('Compact session context?')).toBeVisible();
    await user.type(screen.getByLabelText(/Preserve specific details/), 'API decisions and unresolved bugs');
    await user.click(screen.getByRole('button', { name: 'Compact context' }));

    expect(onCompactContext).toHaveBeenCalledWith('API decisions and unresolved bugs');
    expect(screen.queryByRole('dialog', { name: 'Session context' })).not.toBeInTheDocument();
  });
});

describe('user message layout', () => {
  it('collapses and expands a long user prompt', async () => {
    const user = userEvent.setup();
    const longMessage = `Review this request: ${'very-long-content '.repeat(20)}`;
    const { container } = render(<UserMessage content={longMessage} />);

    expect(container.querySelector('.user-bubble')).toHaveClass('collapsed');
    const toggle = screen.getByRole('button', { name: 'Expand message' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');

    await user.click(toggle);
    expect(container.querySelector('.user-bubble')).not.toHaveClass('collapsed');
    expect(screen.getByRole('button', { name: 'Collapse message' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('wraps a long unbroken user message inside the chat viewport', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8');
    const styleElement = document.createElement('style');
    styleElement.dataset.chatAreaTest = '';
    styleElement.textContent = [...styles.matchAll(/^\.user-bubble\s*\{[^}]*\}/gm)]
      .map((match) => match[0])
      .join('\n');
    document.head.append(styleElement);
    const longPath = '/home/user/workspaces/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/SCRATCHPAD.md';
    const agent: Agent = {
      id: 'agent-one', name: 'agent-one', title: 'Research', description: '', status: 'ready',
      provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
      skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations: [],
    };
    render(<ChatArea
      agent={agent} agents={[agent]} activeConversation={undefined} providers={[]}
      runs={[]} messages={[{ id: 'long-path', role: 'user', content: longPath, timestamp: 1 }]}
      usage={null} chatStatus="ready" chatError="" streaming={false} canStop={false}
      onSend={vi.fn()} onStop={vi.fn()} onResolveRunApproval={vi.fn()} onRetry={vi.fn()}
      onSelectModel={vi.fn()} onSelectAgent={vi.fn()} onTestAgent={vi.fn()}
      onSelectConversation={vi.fn()} onCreateConversation={vi.fn()} onDeleteConversation={vi.fn()}
      onRenameConversation={vi.fn()} onOpenFile={vi.fn()}
    />);

    const bubble = screen.getByText(longPath).closest('.user-bubble');
    expect(bubble).not.toBeNull();
    const style = getComputedStyle(bubble as Element);
    expect(style.minWidth).toBe('0px');
    expect(style.maxWidth).toBe('min(520px, 70%)');
    expect(style.overflowWrap).toBe('anywhere');
    expect(style.wordBreak).toBe('break-word');
  });
});

describe('conversation picker', () => {
  it('keeps one active-session control and puts search and creation in the bounded list', () => {
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
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
    const onCreateConversation = vi.fn();
    const onNewAgent = vi.fn();
    const onOpenManage = vi.fn();
    render(<ChatArea
      agent={agent} agents={[agent]} activeConversation={conversations[198]} providers={[]}
      runs={[]} messages={[]} usage={null} chatStatus="ready" chatError="" streaming={false} canStop={false}
      onSend={vi.fn()} onStop={vi.fn()} onResolveRunApproval={vi.fn()} onRetry={vi.fn()}
      onSelectModel={vi.fn()} onOpenSettings={vi.fn()} onOpenRuntime={vi.fn()} onSelectAgent={vi.fn()}
      onNewAgent={onNewAgent} onOpenManage={onOpenManage}
      onTestAgent={vi.fn()} onDeleteAgent={vi.fn()} onSelectConversation={onSelectConversation}
      onCreateConversation={onCreateConversation} onDeleteConversation={vi.fn()} onRenameConversation={vi.fn()}
      onOpenFile={vi.fn()}
    />);

    expect(screen.queryByText('Session 2')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Manage' }));
    expect(onOpenManage).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: /Research/ }));
    fireEvent.click(screen.getByRole('button', { name: 'New agent' }));
    expect(onNewAgent).toHaveBeenCalledOnce();

    fireEvent.click(screen.getByRole('button', { name: 'Show all sessions: Quarterly risk review' }));
    const list = screen.getByRole('listbox', { name: 'Show all sessions' });
    expect(list).toBeVisible();
    expect(screen.getByRole('button', { name: 'Create session' })).toBeVisible();
    expect(within(list).getAllByRole('option')).toHaveLength(205);
    expect(within(list).getByRole('option', { name: /Quarterly risk review/ })).toHaveAttribute('aria-selected', 'true');
    expect(scrollIntoView).toHaveBeenCalledWith({ block: 'center' });

    fireEvent.change(screen.getByPlaceholderText('Search sessions…'), { target: { value: 'quarterly risk' } });
    const options = within(list).getAllByRole('option');
    expect(options).toHaveLength(1);
    expect(options[0]).toHaveTextContent('Quarterly risk review');
    fireEvent.click(options[0]);
    expect(onSelectConversation).toHaveBeenCalledWith('session-199');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all sessions: Quarterly risk review' }));
    fireEvent.click(screen.getByRole('button', { name: 'Create session' }));
    expect(onCreateConversation).toHaveBeenCalledOnce();
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
  });
});

describe('live run activity', () => {
  it('keeps elapsed time and step count above the composer and reveals the active work log', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_015_000);
    const scrollIntoView = vi.fn();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoView,
    });
    const conversation: Conversation = {
      id: 'session-one',
      title: 'Live session',
      preview: '',
      startedAt: '2026-08-11T09:08:27Z',
      model: 'auto',
      messages: 1,
      tools: 2,
    };
    const agent: Agent = {
      id: 'agent-one', name: 'agent-one', title: 'Research', description: '', status: 'ready',
      provider: 'nine-router', model: 'auto', reasoningEffort: 'medium', approvalMode: 'manual',
      skillsWriteApproval: true, memoryWriteApproval: true, workspace: '', skills: [], conversations: [conversation],
    };
    const activeRun: ChatRun = {
      id: 'run-live',
      insertBeforeMessageId: 'assistant-live',
      status: 'running',
      startedAt: 1_000,
      steps: [
        { id: 'step-1', toolName: 'search_files', preview: '*.tsx', status: 'completed' },
        { id: 'step-2', toolName: 'terminal', preview: 'npm test', status: 'running' },
      ],
      assistantContent: 'I found the relevant component.',
    };

    render(<ChatArea
      agent={agent} agents={[agent]} activeConversation={conversation} providers={[]}
      runs={[activeRun]}
      queuedMessages={[{ id: 'queued-1', content: 'Summarize the sources next' }]}
      messages={[{ id: 'assistant-live', role: 'assistant', content: activeRun.assistantContent, streaming: true }]}
      usage={null} chatStatus="ready" chatError="" streaming canStop
      onSend={vi.fn()} onStop={vi.fn()} onResolveRunApproval={vi.fn()} onRetry={vi.fn()}
      onSelectModel={vi.fn()} onSelectAgent={vi.fn()} onTestAgent={vi.fn()}
      onSelectConversation={vi.fn()} onCreateConversation={vi.fn()} onDeleteConversation={vi.fn()}
      onRenameConversation={vi.fn()} onOpenFile={vi.fn()}
    />);

    expect(screen.getByLabelText('Agent is working for 15s, 2 steps')).toBeVisible();
    expect(screen.getByText('View activity')).toBeVisible();
    const activity = screen.getByLabelText('Agent is working for 15s, 2 steps');
    const queue = screen.getByLabelText('1 queued message');
    expect(activity.compareDocumentPosition(queue) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const workLog = screen.getByRole('button', { name: /Worked for 15s.*2 steps/ });
    expect(workLog).toHaveAttribute('aria-expanded', 'false');

    fireEvent.click(screen.getByRole('button', { name: 'View agent activity' }));
    act(() => vi.advanceTimersByTime(0));

    expect(workLog).toHaveAttribute('aria-expanded', 'true');
    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: 'smooth', block: 'start' });

    act(() => vi.advanceTimersByTime(1_000));
    expect(screen.getByLabelText('Agent is working for 16s, 2 steps')).toBeVisible();
  });
});
