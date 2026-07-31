import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import type { Agent } from '../types';
import { Sidebar } from './Sidebar';

vi.mock('../theme', () => ({
  useTheme: () => ({ preference: 'dark', setPreference: vi.fn(), resolved: 'dark' }),
}));

const agent: Agent = {
  id: 'a1b2c3',
  name: 'a1b2c3',
  title: 'Research Lead',
  description: '',
  status: 'ready',
  provider: 'nine-router',
  model: 'auto',
  reasoningEffort: 'medium',
  approvalMode: 'manual',
  skillsWriteApproval: true,
  memoryWriteApproval: true,
  workspace: '',
  skills: [],
  conversations: [],
};

describe('Sidebar assistant actions', () => {
  beforeEach(async () => {
    localStorage.clear();
    await i18n.changeLanguage('en');
  });

  it('shows simplified actions and requests delete from the three-dot menu', () => {
    const requestDelete = vi.fn();
    render(
      <Sidebar
        agents={[agent]}
        activeAgent={agent}
        centerView="chat"
        agentSearch=""
        onAgentSearch={vi.fn()}
        onNavigate={vi.fn()}
        onSelectAgent={vi.fn()}
        onRenameAgent={vi.fn()}
        onExportAgent={vi.fn()}
        onRequestDeleteAgent={requestDelete}
        onNewAgent={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Agent options' }));

    const menu = screen.getByRole('menu');
    expect(menu).toHaveTextContent('Rename');
    expect(menu).toHaveTextContent('Export');
    const deleteButton = screen.getByRole('button', { name: 'Delete' });
    expect(deleteButton).toHaveClass('danger');

    fireEvent.click(deleteButton);
    expect(requestDelete).toHaveBeenCalledWith('a1b2c3');
  });

  it('opens the skills view from the left navigation', () => {
    const navigate = vi.fn();
    const { container } = render(
      <Sidebar
        agents={[agent]}
        activeAgent={agent}
        centerView="chat"
        agentSearch=""
        onAgentSearch={vi.fn()}
        onNavigate={navigate}
        onSelectAgent={vi.fn()}
        onRenameAgent={vi.fn()}
        onExportAgent={vi.fn()}
        onRequestDeleteAgent={vi.fn()}
        onNewAgent={vi.fn()}
      />,
    );

    fireEvent.click(within(container).getByRole('button', { name: 'Workspace' }));
    fireEvent.click(within(container).getByRole('button', { name: 'Skills' }));
    expect(navigate).toHaveBeenCalledWith('skills');
  });

  it('opens account details from the left navigation for an active session', () => {
    const openAccount = vi.fn();
    const requestSignOut = vi.fn();
    const { container } = render(
      <Sidebar
        agents={[agent]}
        activeAgent={agent}
        centerView="chat"
        agentSearch=""
        onAgentSearch={vi.fn()}
        onNavigate={vi.fn()}
        onSelectAgent={vi.fn()}
        onRenameAgent={vi.fn()}
        onExportAgent={vi.fn()}
        onRequestDeleteAgent={vi.fn()}
        onNewAgent={vi.fn()}
        user={{ userId: 'user-1', email: 'kim@example.com', displayName: 'Kim', tenantId: 'tenant-1', planId: 'pro', roles: ['owner'] }}
        edition="pro"
        loginEnabled
        sessionActive
        onOpenAccount={openAccount}
        onSignOut={requestSignOut}
      />,
    );

    fireEvent.click(within(container).getByRole('button', { name: 'Workspace' }));
    fireEvent.click(within(container).getByRole('button', { name: /Accountpro/i }));
    expect(openAccount).toHaveBeenCalledOnce();

    fireEvent.click(within(container).getByRole('button', { name: 'Sign out' }));
    expect(requestSignOut).toHaveBeenCalledOnce();
  });

  it('keeps a stable full agent list with Big Brother first', () => {
    const agents = Array.from({ length: 6 }, (_, index) => ({
      ...agent,
      id: index === 5 ? 'big-brother' : `agent-${index}`,
      name: index === 5 ? 'big-brother' : `agent-${index}`,
      title: index === 5 ? 'Big Brother' : `Agent ${index + 1}`,
    }));
    const { container } = render(
      <Sidebar
        agents={agents}
        activeAgent={agents[0]}
        centerView="chat"
        agentSearch=""
        onAgentSearch={vi.fn()}
        onNavigate={vi.fn()}
        onSelectAgent={vi.fn()}
        onRenameAgent={vi.fn()}
        onExportAgent={vi.fn()}
        onRequestDeleteAgent={vi.fn()}
        onNewAgent={vi.fn()}
      />,
    );

    const rows = within(container).getAllByRole('button').filter((button) => button.classList.contains('agent-row-main'));
    expect(rows.map((row) => row.textContent)).toEqual([
      'Big Brotherauto', 'Agent 1auto', 'Agent 2auto', 'Agent 3auto', 'Agent 4auto', 'Agent 5auto',
    ]);

    fireEvent.click(within(container).getByRole('button', { name: 'Open in Library' }));
    expect(screen.getByRole('dialog', { name: 'Agent Library' })).toBeVisible();
    expect(screen.getAllByText('Big Brother')).toHaveLength(2);
  });
});
