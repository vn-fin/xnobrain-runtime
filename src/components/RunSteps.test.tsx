import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ChatRun } from '../types';
import { RunActivityBar, RunSteps } from './RunSteps';

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

  it('shows a live parallel delegation in the terminal card and can stop it', () => {
    const stop = vi.fn();
    render(<RunSteps run={run({
      status: 'running',
      assistantContent: '',
      steps: [{
        id: 'delegate-1',
        toolName: 'delegate_task',
        preview: 'delegating 2 tasks',
        args: { tasks: [
          { goal: 'Inspect the runtime delegation path.' },
          { goal: 'Verify the UI behavior.' },
        ] },
        status: 'running',
      }],
    })} onStop={stop} />);

    expect(screen.getByRole('region', { name: 'Delegation activity' })).toBeVisible();
    expect(screen.getByText('RUNNING')).toBeVisible();
    expect(screen.getByText('2 running · 0 queued · 2 slots')).toBeVisible();
    expect(screen.getByText(/Inspect the runtime delegation path/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel all' }));
    expect(stop).toHaveBeenCalledOnce();
  });

  it('shows partial delegation results and worker details', () => {
    render(<RunSteps run={run({
      steps: [{
        id: 'delegate-1',
        toolName: 'delegate_task',
        preview: 'delegating 2 tasks',
        args: { tasks: [
          { goal: 'Inspect the runtime.' },
          { goal: 'Run focused tests.' },
        ] },
        output: JSON.stringify({
          results: [
            { task_index: 0, status: 'completed', summary: 'Runtime inspected.', duration_seconds: 2.2 },
            { task_index: 1, status: 'error', error: 'Worker session failed.', duration_seconds: 1.1 },
          ],
          total_duration_seconds: 2.3,
        }),
        status: 'completed',
        durationSec: 2.3,
      }],
    })} />);

    fireEvent.click(screen.getByRole('button', { name: /Worked/ }));
    expect(screen.getByText('PARTIAL FAILURE')).toBeVisible();
    expect(screen.getByText(/1\/2 results returned/)).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /02 Run focused tests/ }));
    expect(screen.getByText('Worker session failed.')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Close worker activity' }));
    fireEvent.click(screen.getByRole('button', { name: /01 Inspect the runtime/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Result' }));
    expect(screen.getByText('Runtime inspected.')).toBeVisible();
  });

  it('opens a worker modal that updates with live logs and exact metrics', () => {
    render(<RunSteps run={run({
      status: 'running',
      assistantContent: '',
      steps: [{
        id: 'delegate-live',
        toolName: 'delegate_task',
        preview: 'five research tasks',
        args: { tasks: Array.from({ length: 5 }, (_, index) => ({ goal: `Research conference ${index + 1} with a deliberately long description.` })) },
        status: 'running',
        delegation: {
          concurrency: 3,
          workers: Array.from({ length: 5 }, (_, index) => ({
            index,
            goal: `Research conference ${index + 1} with a deliberately long description.`,
            status: index < 2 ? 'completed' : index < 4 ? 'running' : 'queued',
            queuePosition: index === 4 ? 1 : undefined,
            toolCount: index === 2 ? 3 : 0,
            steps: index < 2 ? 2 : undefined,
            inputTokens: index < 2 ? 1_200 : undefined,
            outputTokens: index < 2 ? 300 : undefined,
            logs: index === 2 ? [{ id: 'log-1', timestamp: 1_000, kind: 'tool', tool: 'web_search', message: 'ICML proceedings' }] : [],
          })),
        },
      }],
    })} />);

    expect(screen.getByText('2 running · 1 queued · 3 slots')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /03 Research conference 3/ }));
    const dialog = screen.getByRole('dialog', { name: 'Worker 3 activity' });
    expect(dialog).toBeVisible();
    expect(within(dialog).getByText(/3 tools/)).toBeVisible();
    expect(within(dialog).getByText(/web_search/)).toBeVisible();
    expect(within(dialog).getByText('● streaming updates')).toBeVisible();
  });

  it('shows the live agent plan and task progress', () => {
    render(<RunSteps run={run({
      status: 'running',
      assistantContent: '',
      todos: [
        { id: 'one', content: 'Inspect the current implementation', status: 'completed' },
        { id: 'two', content: 'Build the plan panel', status: 'in_progress' },
        { id: 'three', content: 'Verify reload behavior', status: 'pending' },
      ],
    })} />);

    expect(screen.getByRole('region', { name: 'Agent plan' })).toBeVisible();
    expect(screen.getByText('Build the plan panel')).toBeVisible();
    expect(screen.getByText('1 / 3')).toBeVisible();
    expect(screen.getByText('Current')).toBeVisible();
  });

  it('collapses an entirely completed plan by default', () => {
    render(<RunSteps run={run({
      todos: [
        { id: 'one', content: 'Inspect the implementation', status: 'completed' },
        { id: 'two', content: 'Verify the result', status: 'completed' },
      ],
    })} />);

    fireEvent.click(screen.getByRole('button', { name: /Worked/ }));
    expect(screen.getByRole('button', { name: /Plan/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Inspect the implementation')).not.toBeInTheDocument();
  });

  it('adds todo progress to the persistent activity bar', () => {
    render(<RunActivityBar run={run({
      status: 'running',
      todos: [
        { id: 'one', content: 'Plan', status: 'completed' },
        { id: 'two', content: 'Build', status: 'in_progress' },
      ],
    })} onViewActivity={() => undefined} />);

    expect(screen.getByText('1/2 tasks')).toBeVisible();
  });

  it('renders repeated plan updates in chronological activity order', () => {
    const first = [
      { id: 'one', content: 'First snapshot current task', status: 'in_progress' as const },
      { id: 'two', content: 'Prepare the answer', status: 'pending' as const },
    ];
    const second = [
      { id: 'one', content: 'First snapshot current task', status: 'completed' as const },
      { id: 'two', content: 'Second snapshot current task', status: 'in_progress' as const },
    ];
    const { container } = render(<RunSteps run={run({
      status: 'running',
      assistantContent: '',
      todos: second,
      reasoning: ['Research finished; moving to the answer.'],
      timeline: [
        { kind: 'todos', todos: first },
        { kind: 'reasoning', text: 'Research finished; moving to the answer.' },
        { kind: 'todos', todos: second },
      ],
    })} />);

    expect(screen.getAllByRole('region', { name: 'Agent plan' })).toHaveLength(2);
    const text = container.textContent ?? '';
    expect(text.indexOf('First snapshot current task')).toBeLessThan(text.indexOf('Research finished; moving to the answer.'));
    expect(text.indexOf('Research finished; moving to the answer.')).toBeLessThan(text.indexOf('Second snapshot current task'));
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
