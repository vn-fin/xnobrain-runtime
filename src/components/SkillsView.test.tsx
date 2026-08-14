import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import type { Agent, AgentSkill } from '../types';
import { SkillsView, skillSourceError } from './SkillsView';

const skill: AgentSkill = {
  skill_id: 'writer',
  name: 'writer',
  category: 'office',
  description: 'Writes documents',
  enabled: true,
  installed: true,
  path: '/skills/writer',
};

const agents: Agent[] = ['one', 'two', 'three', 'four'].map((id) => ({
  id,
  name: id,
  title: id,
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
}));

describe('SkillsView default profile controls', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
  });

  it('validates skill URLs and hub identifiers', () => {
    expect(skillSourceError('')).toContain('required');
    expect(skillSourceError('not a valid skill identifier ???')).toContain('HTTP(S)');
    expect(skillSourceError('skills-sh/anthropics/skills/pdf')).toBe('');
    expect(skillSourceError('https://example.com/SKILL.md')).toBe('');
  });

  it('shows a card skeleton while the initial skills snapshot is loading', () => {
    const { container } = render(
      <SkillsView
        library={[]}
        agents={agents}
        agentSkills={{}}
        loading
        search=""
        onSearch={vi.fn()}
        groupFilter="all"
        onGroupFilter={vi.fn()}
        onInstall={vi.fn().mockResolvedValue(true)}
        onSetDefaultEnabled={vi.fn().mockResolvedValue(true)}
        installPending={false}
        installError=""
        onInstallExisting={vi.fn()}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByRole('status', { name: 'Loading skills' })).toBeVisible();
    expect(container.querySelectorAll('.skv-card-skeleton')).toHaveLength(8);
    expect(screen.queryByText('No skills found')).not.toBeInTheDocument();
  });

  it('lets the user disable an installed default skill', async () => {
    const setDefaultEnabled = vi.fn().mockResolvedValue(true);
    render(
      <SkillsView
        library={[skill]}
        agents={[]}
        agentSkills={{}}
        search=""
        onSearch={vi.fn()}
        groupFilter="all"
        onGroupFilter={vi.fn()}
        onInstall={vi.fn().mockResolvedValue(true)}
        onSetDefaultEnabled={setDefaultEnabled}
        installPending={false}
        installError=""
        onInstallExisting={vi.fn()}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('checkbox', { name: 'Use writer in the default profile' }));

    await waitFor(() => expect(setDefaultEnabled).toHaveBeenCalledWith('writer', false));
  });

  it('shows total and active skill counts instead of duplicate install counts', () => {
    const disabled = { ...skill, skill_id: 'reader', name: 'reader', enabled: false };
    const { container } = render(
      <SkillsView
        library={[skill, disabled]}
        agents={[]}
        agentSkills={{}}
        search=""
        onSearch={vi.fn()}
        groupFilter="all"
        onGroupFilter={vi.fn()}
        onInstall={vi.fn().mockResolvedValue(true)}
        onSetDefaultEnabled={vi.fn().mockResolvedValue(true)}
        installPending={false}
        installError=""
        onInstallExisting={vi.fn()}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    const stats = [...container.querySelectorAll('.skv-stat')];
    expect(within(stats[0] as HTMLElement).getByText('Total skills')).toBeVisible();
    expect(within(stats[0] as HTMLElement).getByText('2')).toBeVisible();
    expect(within(stats[1] as HTMLElement).getByText('Active skills')).toBeVisible();
    expect(within(stats[1] as HTMLElement).getByText('1')).toBeVisible();
    expect(screen.queryByText('Installed')).not.toBeInTheDocument();
  });

  it('shows enabled agents over the total agent count for each skill', () => {
    const { container } = render(
      <SkillsView
        library={[skill]}
        agents={agents}
        agentSkills={{
          one: { writer: true },
          two: { writer: true },
          three: { writer: true },
          four: { writer: false },
        }}
        search=""
        onSearch={vi.fn()}
        groupFilter="all"
        onGroupFilter={vi.fn()}
        onInstall={vi.fn().mockResolvedValue(true)}
        onSetDefaultEnabled={vi.fn().mockResolvedValue(true)}
        installPending={false}
        installError=""
        onInstallExisting={vi.fn()}
        onApply={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('3/4 agents')).toBeInTheDocument();
    expect(container.querySelector('[role="tooltip"]')).toHaveTextContent('Writes documents');
    expect(screen.queryByText('/skills/writer')).not.toBeInTheDocument();
  });

  it('previews changes before confirming a multi-agent skill sync', async () => {
    const preview = vi.fn().mockResolvedValue({
      source_revision: 'revision-1',
      common: { enabled: 1, disabled: 0 },
      totals: { added: 1, updated: 0, removed: 0, preserved: 1, unchanged: 0 },
      agents: [{
        agent_id: 'one',
        added: ['writer'],
        updated: [],
        removed: [],
        preserved: ['private-skill'],
        unchanged: [],
      }],
    });
    const sync = vi.fn().mockResolvedValue({
      source_revision: 'revision-1',
      status: 'completed',
      agents: [{
        agent_id: 'one', status: 'completed', added: ['writer'], updated: [], removed: [], preserved: ['private-skill'], unchanged: [],
      }],
      completed: 1,
      failed: 0,
    });
    render(
      <SkillsView
        library={[skill]}
        agents={agents}
        agentSkills={{}}
        search=""
        onSearch={vi.fn()}
        groupFilter="all"
        onGroupFilter={vi.fn()}
        onInstall={vi.fn().mockResolvedValue(true)}
        onSetDefaultEnabled={vi.fn().mockResolvedValue(true)}
        installPending={false}
        installError=""
        onInstallExisting={vi.fn()}
        onApply={vi.fn()}
        onPreviewSync={preview}
        onSync={sync}
        onClose={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Sync agents' }));
    fireEvent.click(screen.getAllByText('one')[0]);
    fireEvent.click(screen.getByRole('button', { name: 'Review changes (1)' }));
    await screen.findByText('+1 added');
    expect(sync).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Confirm sync' }));
    await screen.findByText('1 agents synchronized.');
    expect(preview).toHaveBeenCalledWith(['one']);
    expect(sync).toHaveBeenCalledWith(['one'], 'revision-1');
  });
});
