import { fireEvent, render, screen, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import type { Agent } from '../types';
import { MobileManageDrawer, Sidebar } from './Sidebar';

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
    const deleteButton = screen.getByRole('menuitem', { name: 'Delete' });
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

    fireEvent.click(within(container).getByRole('button', { name: 'Manage' }));
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

    fireEvent.click(within(container).getByRole('button', { name: 'Manage' }));
    fireEvent.click(within(container).getByRole('button', { name: /Accountpro/i }));
    expect(openAccount).toHaveBeenCalledOnce();

    fireEvent.click(within(container).getByRole('button', { name: 'Open preferences for Kim' }));
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
      'Big BrotherNo description', 'Agent 1No description', 'Agent 2No description',
      'Agent 3No description', 'Agent 4No description', 'Agent 5No description',
    ]);

    fireEvent.click(within(container).getByRole('button', { name: 'Open in Library' }));
    expect(screen.getByRole('dialog', { name: 'Agent Library' })).toBeVisible();
    expect(screen.getAllByText('Big Brother')).toHaveLength(2);
  });

  it('moves pinned agents to the top and shows their purpose', () => {
    const pinned = { ...agent, id: 'pinned', title: 'Pinned agent', description: 'Monitors production incidents' };
    localStorage.setItem('brain4all.pinnedAssistants', JSON.stringify([pinned.id]));
    const { container } = render(
      <Sidebar
        agents={[agent, pinned]}
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
      />,
    );

    const rows = within(container).getAllByRole('button').filter((button) => button.classList.contains('agent-row-main'));
    expect(rows[0]).toHaveTextContent('Pinned agentMonitors production incidents');
  });
});

describe('MobileManageDrawer', () => {
  it('shows only manage destinations and closes after navigation', () => {
    const navigate = vi.fn();
    const close = vi.fn();
    render(
      <MobileManageDrawer
        open
        centerView="chat"
        onOpen={vi.fn()}
        onClose={close}
        onNavigate={navigate}
      />,
    );

    const drawer = screen.getByRole('dialog', { name: 'Manage' });
    expect(within(drawer).queryByText('Research Lead')).not.toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole('button', { name: 'Skills' }));
    expect(navigate).toHaveBeenCalledWith('skills');
    expect(close).toHaveBeenCalledOnce();
  });

  it('opens from a left-edge swipe and closes with Escape', () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: vi.fn().mockReturnValue({ matches: true }),
    });
    const open = vi.fn();
    const close = vi.fn();
    const { rerender } = render(
      <MobileManageDrawer
        open={false}
        centerView="chat"
        onOpen={open}
        onClose={close}
        onNavigate={vi.fn()}
      />,
    );

    fireEvent.pointerDown(document, { clientX: 5, clientY: 100 });
    fireEvent.pointerMove(document, { clientX: 85, clientY: 108 });
    expect(open).toHaveBeenCalledOnce();

    rerender(
      <MobileManageDrawer
        open
        centerView="chat"
        onOpen={open}
        onClose={close}
        onNavigate={vi.fn()}
      />,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(close).toHaveBeenCalledOnce();
  });
});
