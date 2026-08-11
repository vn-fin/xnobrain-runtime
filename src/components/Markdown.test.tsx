import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Markdown } from './Markdown';

const mocks = vi.hoisted(() => ({
  getBoards: vi.fn(),
  getTask: vi.fn(),
  listAgents: vi.fn(),
}));

vi.mock('../api/agents', () => ({
  agentsApi: { list: mocks.listAgents },
}));

vi.mock('../api/kanban', () => ({
  kanbanApi: {
    getBoards: mocks.getBoards,
    getTask: mocks.getTask,
  },
}));

describe('Markdown Kanban task links', () => {
  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
  });

  it('links a plain task ID and opens a compact task summary', async () => {
    mocks.getBoards.mockResolvedValue([{ id: 'default', tasks: [{ id: 't_70141301' }] }]);
    mocks.listAgents.mockResolvedValue([{ id: 'big-brother', name: 'big-brother', title: 'Big Brother' }]);
    mocks.getTask.mockResolvedValue({
      id: 't_70141301',
      title: 'Summarize the news',
      description: `Collect the requested headlines and prepare a concise summary. ${'Detailed briefing context. '.repeat(12)} Artifact: /home/brain4all/${'nested-directory/'.repeat(20)}result.txt`,
      nativeStatus: 'blocked',
      priority: 'medium',
      assignees: ['big-brother'],
      progress: 35,
      updated: '4m ago',
      runs: [{ startedAt: '2026-08-05T08:00:00Z', endedAt: '2026-08-05T08:02:00Z' }],
      schedule: null,
      team: null,
      result: `Waiting for the requested topic and time window. ${'Additional result detail. '.repeat(12)} ${'unbroken-result-token-'.repeat(30)}`,
    });
    render(<Markdown content="Work kanban task t_70141301 now." />);

    const taskLink = screen.getByRole('link', { name: 't_70141301' });
    expect(taskLink).toHaveAttribute('href', '/kanban/tasks/t_70141301');
    fireEvent.click(taskLink);

    expect(await screen.findByRole('dialog', { name: 'Summarize the news' })).toBeInTheDocument();
    expect(screen.getByText('Big Brother')).toBeVisible();
    expect(screen.getByText('BB')).toHaveClass('kb-avatar');
    expect(screen.getByText('Blocked')).toHaveClass('kb-substate', 'native-blocked');
    expect(screen.getByText('Medium')).toBeVisible();
    expect(screen.getByText('35%')).toBeVisible();
    expect(screen.getByRole('progressbar', { name: 'Task progress' })).toHaveAttribute('aria-valuenow', '35');
    expect(screen.getByText('Updated 4m ago')).toBeVisible();
    expect(screen.getByText(/^Started /)).toBeVisible();
    expect(screen.getByText(/^Finished /)).toBeVisible();
    expect(screen.getByText(/Collect the requested headlines/)).toHaveTextContent('…');
    expect(screen.getByText(/Waiting for the requested topic/)).toHaveTextContent('…');

    fireEvent.click(screen.getByRole('button', { name: 'Show full task brief' }));
    expect(screen.getByText(/Collect the requested headlines/)).toHaveTextContent('Detailed briefing context.');
    expect(screen.getByText(/Collect the requested headlines/)).toHaveClass('kb-long-text');
    fireEvent.click(screen.getByRole('button', { name: 'Show full results' }));
    expect(screen.getByText(/Waiting for the requested topic/)).toHaveTextContent('Additional result detail.');
    expect(screen.getByRole('link', { name: /Open in Kanban/ })).toHaveAttribute(
      'href',
      '/kanban/tasks/t_70141301',
    );
    await waitFor(() => expect(mocks.getTask).toHaveBeenCalledWith('default', 't_70141301'));
  });

  it('does not link task-like text in code or non-task identifiers', () => {
    render(<Markdown content={'`t_56aa5fcf` and t_factory are not task links.'} />);

    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.getByText('t_56aa5fcf').closest('code')).toBeInTheDocument();
  });
});

describe('Markdown URL labels', () => {
  afterEach(cleanup);

  it('shows only the final path segment for a bare URL while preserving its destination', () => {
    const href = 'https://hermes-agent.nousresearch.com/docs/user-guide/features/context-files';
    render(<Markdown content={href} />);

    const link = screen.getByText('context-files');
    expect(link).toHaveAttribute('href', href);
    expect(link).toHaveAttribute('title', href);
    expect(link).toHaveAttribute('aria-label', href);
  });

  it('uses the hostname for a root URL and preserves an explicit Markdown label', () => {
    render(<Markdown content={'https://example.com/ and [Configuration guide](https://example.com/docs/configuration)'} />);

    expect(screen.getByText('example.com')).toHaveAttribute('href', 'https://example.com/');
    expect(screen.getByRole('link', { name: 'Configuration guide' })).toHaveAttribute(
      'href',
      'https://example.com/docs/configuration',
    );
  });
});

describe('Markdown currency and math', () => {
  afterEach(cleanup);

  it('keeps two dollar-denominated amounts as readable prose', () => {
    const content = "Strategy unveiled a $15B bitcoin-backed preferred stock plan and its CEO says they'll keep buying through the year; losses hit $361M (Yahoo, Fox, CoinDesk).";
    const { container } = render(<Markdown content={content} />);

    expect(container).toHaveTextContent(content);
    expect(container.querySelector('.katex')).not.toBeInTheDocument();
  });

  it('continues to render genuine inline equations with KaTeX', () => {
    const { container } = render(<Markdown content={'Einstein wrote $E = mc^2$ and $x^2 + y^2 = z^2$.'} />);

    expect(container.querySelectorAll('.katex')).toHaveLength(2);
  });

  it('tolerates double-dollar inline math emitted inside prose', () => {
    const { container } = render(
      <Markdown content={'The definition gives $$1 := S(0)$$ and then the proof continues.'} />,
    );

    expect(container.querySelector('.katex-error')).not.toBeInTheDocument();
    expect(container.querySelectorAll('.katex')).toHaveLength(1);
    expect(container.querySelector('.katex-display')).not.toBeInTheDocument();
    expect(container).toHaveTextContent('and then the proof continues.');
  });

  it('renders an indented standalone double-dollar expression as display math', () => {
    const { container } = render(<Markdown content={'  $$1 := S(0)$$\n\nFollowing prose.'} />);

    expect(container.querySelector('.katex-error')).not.toBeInTheDocument();
    expect(container.querySelectorAll('.katex-display')).toHaveLength(1);
    expect(container).toHaveTextContent('Following prose.');
  });

  it('renders compact multiline LaTeX without swallowing the following Markdown', () => {
    const content = String.raw`## The Proof

$$\begin{aligned}
1 + 1 &= 1 + S(0) &&\text{(definition)} \\[4pt]
      &= S(1) &&\text{(addition)}
\end{aligned}$$

$$\boxed{1 + 1 = 2} \qquad \blacksquare$$

## Why Each Line Is Valid

| Step | Rule |
| --- | --- |
| $1 + S(0)$ | Addition |

This is the modern, compact version.`;
    const { container } = render(<Markdown content={content} />);

    expect(container.querySelector('.katex-error')).not.toBeInTheDocument();
    expect(container.querySelectorAll('.katex-display')).toHaveLength(2);
    expect(screen.getByRole('heading', { name: 'Why Each Line Is Valid' })).toBeVisible();
    expect(screen.getByRole('table')).toBeVisible();
    expect(screen.getByText('This is the modern, compact version.')).toBeVisible();
  });

  it('does not rewrite display delimiters inside fenced code', () => {
    const source = '```latex\n$$x + y$$\n```';
    const { container } = render(<Markdown content={source} />);

    expect(container.querySelector('.katex')).not.toBeInTheDocument();
    expect(screen.getByText('$$x + y$$')).toBeVisible();
  });
});
