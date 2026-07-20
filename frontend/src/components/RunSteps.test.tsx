import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ChatRun } from '../types';
import { RunSteps } from './RunSteps';

function run(overrides: Partial<ChatRun> = {}): ChatRun {
  return {
    id: 'run-42',
    status: 'completed',
    steps: [],
    assistantContent: 'The same text is still a received reasoning event.',
    ...overrides,
  };
}

describe('RunSteps', () => {
  it('renders every reasoning event even when it matches the answer', () => {
    render(<RunSteps run={run({ reasoning: ['The same text is still a received reasoning event.'] })} />);

    fireEvent.click(screen.getByRole('button', { name: /Thought/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Reasoning' }));

    expect(screen.getByText('The same text is still a received reasoning event.')).toBeInTheDocument();
  });

  it('submits an approval choice for a waiting run', async () => {
    const resolve = vi.fn().mockResolvedValue(undefined);
    render(
      <RunSteps
        run={run({
          status: 'waiting_for_approval',
          approval: {
            command: 'Save to memory: apply 1 op(s) to user profile',
            description: 'Memory write requires approval',
            choices: ['once', 'session', 'deny'],
            allowPermanent: false,
          },
        })}
        onResolveApproval={resolve}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('run-42', 'once'));
  });
});
