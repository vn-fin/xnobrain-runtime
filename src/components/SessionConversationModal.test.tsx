import { cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { workspaceApi } from '../api/workspace';
import { useConversation } from '../hooks/useConversation';
import type { Agent, ChatRun } from '../types';
import { SessionConversationModal } from './SessionConversationModal';

vi.mock('../hooks/useConversation', () => ({
  useConversation: vi.fn(),
}));

vi.mock('../api/workspace', () => ({
  workspaceApi: {
    create: vi.fn(),
    upload: vi.fn(),
  },
}));

const agent: Agent = {
  id: 'research-agent',
  title: 'Research Agent',
  name: 'Research Agent',
  description: 'Finds useful evidence',
  status: 'active',
  provider: 'openai',
  model: 'gpt-test',
  reasoningEffort: 'medium',
  approvalMode: 'auto',
  skillsWriteApproval: true,
  memoryWriteApproval: true,
  workspace: '',
  skills: [],
  conversations: [],
};

const run: ChatRun = {
  id: 'run-1',
  insertBeforeMessageId: 'assistant-1',
  status: 'completed',
  steps: [{
    id: 'step-1',
    toolName: 'web_search',
    preview: 'Search the web',
    status: 'completed',
  }],
  assistantContent: 'I found the answer.',
};

const sendMessage = vi.fn(async () => undefined);
const stopStream = vi.fn(async () => undefined);

function liveConversation(overrides: Record<string, unknown> = {}) {
  return {
    messages: [
      { id: 'user-1', role: 'user', content: 'Find the evidence.' },
      { id: 'assistant-1', role: 'assistant', content: 'I found the answer.', finishReason: 'stop' },
    ],
    queuedMessages: [],
    usage: {
      conversationId: 'session-1',
      messages: 2,
      steps: 1,
      executionSeconds: 2.5,
      apiCalls: 1,
      model: 'gpt-test',
      totalTokens: 321,
      totalCostUsd: 0,
      provider: 'openai',
      plan: '',
      quotaAvailable: true,
      quotaMessage: '',
      limits: [],
    },
    usageStatus: 'ready',
    usageError: '',
    status: 'ready',
    error: '',
    streaming: false,
    streamEvents: [],
    runs: [run],
    loadSignal: undefined,
    canStop: false,
    refresh: vi.fn(),
    requestUsage: vi.fn(),
    sendMessage,
    stopStream,
    resolveRunApproval: vi.fn(),
    removeQueuedMessage: vi.fn(),
    editQueuedMessage: vi.fn(),
    moveQueuedMessage: vi.fn(),
    ...overrides,
  };
}

function renderModal(overrides: Partial<Parameters<typeof SessionConversationModal>[0]> = {}) {
  return render(<SessionConversationModal
    agent={agent}
    conversationId="session-1"
    title="Prepare the weekly report"
    subtitle="Research Agent · In Progress"
    statusTone="running"
    onClose={vi.fn()}
    {...overrides}
  />);
}

describe('SessionConversationModal', () => {
  beforeEach(() => {
    vi.mocked(useConversation).mockReturnValue(liveConversation());
    vi.mocked(workspaceApi.create).mockResolvedValue(undefined as never);
    vi.mocked(workspaceApi.upload).mockResolvedValue(undefined as never);
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('shows the full trace and continues the linked session', async () => {
    const user = userEvent.setup();
    renderModal();

    expect(useConversation).toHaveBeenCalledWith('research-agent', 'session-1', 'gpt-test', false);
    expect(screen.getByText('I found the answer.')).toBeVisible();
    await user.click(screen.getByRole('button', { name: /Worked.*1 step/ }));
    expect(screen.getByText('Searched Search the web')).toBeVisible();

    await user.type(screen.getByPlaceholderText('Message Research Agent…'), 'Continue the analysis');
    await user.click(screen.getByRole('button', { name: 'Send message to Research Agent' }));

    expect(sendMessage).toHaveBeenCalledWith('Continue the analysis');
  });

  it('keeps the transcript in its own grid row below session metrics', () => {
    const { container } = renderModal();
    const transcript = container.querySelector('.team-conversation-canvas');

    expect(transcript).not.toBeNull();
    expect((transcript as HTMLElement).style.gridRow).toBe('auto');
  });

  it('stops an active response', async () => {
    const user = userEvent.setup();
    vi.mocked(useConversation).mockReturnValue(liveConversation({ streaming: true, canStop: true }));
    renderModal();

    await user.click(screen.getByRole('button', { name: 'Stop Research Agent' }));

    expect(stopStream).toHaveBeenCalledTimes(1);
  });

  it('uploads a workspace file and includes its path in the next message', async () => {
    const user = userEvent.setup();
    renderModal();
    const file = new File(['brief'], 'brief.pdf', { type: 'application/pdf' });

    await user.upload(screen.getByLabelText('Upload files to Research Agent workspace'), file);
    await waitFor(() => expect(workspaceApi.upload).toHaveBeenCalledWith(
      'research-agent', '', file, expect.any(Function),
    ));
    await user.type(screen.getByPlaceholderText('Message Research Agent…'), 'Summarize this');
    await user.click(screen.getByRole('button', { name: 'Send message to Research Agent' }));

    expect(sendMessage).toHaveBeenCalledWith('`brief.pdf`\n\nSummarize this');
  });

  it('disables continuation when the linked session is unavailable', () => {
    renderModal({ agent: undefined, conversationId: '' });

    expect(screen.getByRole('textbox')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send message to Prepare the weekly report' })).toBeDisabled();
  });
});
