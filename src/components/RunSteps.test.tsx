import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
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
  afterEach(() => {
    cleanup();
    vi.useRealTimers();
  });

  it('updates the compact Worked duration while a run is active', () => {
    vi.useFakeTimers();
    vi.setSystemTime(1_015_000);
    render(<RunSteps run={run({
      status: 'running',
      startedAt: 1_000,
      endedAt: undefined,
      assistantContent: '',
    })} />);

    expect(screen.getByRole('button', { name: /Worked for 15s/ })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(60_000));
    expect(screen.getByRole('button', { name: /Worked for 1m 15s/ })).toBeInTheDocument();
  });

  it('renders received reasoning when no visible answer context is provided', () => {
    render(<RunSteps run={run({ reasoning: ['The same text is still a received reasoning event.'] })} />);

    fireEvent.click(screen.getByRole('button', { name: /Worked/ }));
    expect(screen.getByText('The same text is still a received reasoning event.')).toBeInTheDocument();
  });

  it('keeps a restored completed run collapsed until the user opens it', () => {
    render(<RunSteps run={run({ reasoning: ['Inspect the available evidence first.'] })} />);

    const toggle = screen.getByRole('button', { name: /Worked.*0 steps/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Reasoning' })).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(screen.getByRole('button', { name: 'Reasoning' })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Inspect the available evidence first.')).toBeVisible();
  });

  it('keeps the total step count visible after completion', () => {
    render(<RunSteps run={run({
      steps: [
        { id: 'tool-1', toolName: 'search_files', preview: '*.tsx', status: 'completed' },
        { id: 'tool-2', toolName: 'terminal', preview: 'npm test', status: 'completed' },
      ],
    })} />);

    expect(screen.getByRole('button', { name: /Worked.*2 steps/ })).toBeVisible();
  });

  it('shows reasoning deltas while the reasoning phase is streaming', () => {
    const { rerender } = render(<RunSteps run={run({
      status: 'running',
      assistantContent: '',
      reasoning: ['DuckDuckGo returned'],
      reasoningStreaming: true,
      timeline: [{ kind: 'reasoning', text: 'DuckDuckGo returned' }],
    })} />);

    expect(screen.getByRole('button', { name: 'Thinking…' })).toBeInTheDocument();
    expect(screen.getByText('DuckDuckGo returned')).toBeVisible();

    rerender(<RunSteps run={run({
      status: 'running',
      assistantContent: '',
      reasoning: ['DuckDuckGo returned a challenge page. Try another source.'],
      reasoningStreaming: true,
      timeline: [{ kind: 'reasoning', text: 'DuckDuckGo returned a challenge page. Try another source.' }],
    })} />);

    expect(screen.getByText('DuckDuckGo returned a challenge page. Try another source.')).toBeVisible();

    rerender(<RunSteps run={run({
      status: 'running',
      assistantContent: 'Here is the verified result.',
      reasoning: ['DuckDuckGo returned a challenge page. Try another source.'],
      reasoningStreaming: false,
      timeline: [{ kind: 'reasoning', text: 'DuckDuckGo returned a challenge page. Try another source.' }],
    })} />);

    expect(screen.getByRole('button', { name: /Worked/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('button', { name: 'Reasoning' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Worked/ }));
    expect(screen.getByRole('button', { name: 'Reasoning' })).toBeInTheDocument();
    expect(screen.getByText(/DuckDuckGo returned/)).toBeVisible();
  });

  it('hides a reasoning event that repeats the visible answer', () => {
    const repeated = 'This opening section is repeated verbatim in the final response shown to the user.';
    render(<RunSteps
      run={run({
        steps: [{ id: 'tool-1', toolName: 'terminal', preview: 'curl news', status: 'completed' }],
        reasoning: [repeated],
        timeline: [
          { kind: 'tools', stepIds: ['tool-1'] },
          { kind: 'reasoning', text: repeated },
        ],
      })}
      answerContent={`${repeated} The rest of the final response follows here.`}
    />);

    fireEvent.click(screen.getByRole('button', { name: /Worked/ }));

    expect(screen.queryByText(repeated)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Ran commands/ })).toBeInTheDocument();
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
            choices: ['once', 'always', 'deny'],
            allowPermanent: true,
          },
        })}
        onResolveApproval={resolve}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }));
    await waitFor(() => expect(resolve).toHaveBeenCalledWith('run-42', 'once'));
    expect(screen.queryByRole('button', { name: 'Allow and remember' })).not.toBeInTheDocument();
  });
});
