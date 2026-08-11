import { StrictMode } from 'react';
import { render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Agent } from '../../types';
import { McpSection } from './McpSection';

const mocks = vi.hoisted(() => ({
  getMcp: vi.fn(async () => ({ servers: {} })),
}));

vi.mock('../../api/agents', () => ({
  agentsApi: {
    getMcp: mocks.getMcp,
  },
}));

const agent: Agent = {
  id: 'agent-one',
  title: 'Agent One',
  name: 'agent-one',
  description: '',
  status: 'active',
  provider: 'nine-router',
  model: 'auto',
  reasoningEffort: 'medium',
  approvalMode: 'on',
  skillsWriteApproval: true,
  memoryWriteApproval: true,
  workspace: '',
  conversations: [],
  skills: [],
};

describe('McpSection', () => {
  afterEach(() => vi.clearAllMocks());

  it('deduplicates its initial configuration load under StrictMode', async () => {
    render(<StrictMode><McpSection agents={[agent]} /></StrictMode>);

    await waitFor(() => expect(mocks.getMcp).toHaveBeenCalledTimes(1));
    expect(mocks.getMcp).toHaveBeenCalledWith('agent-one');
  });
});
