import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import '../i18n';
import type { Agent } from '../types';
import { RightPanel } from './RightPanel';

const agent: Agent = {
  id: 'agent-1',
  name: 'agent-1',
  title: 'Approval agent',
  description: 'Tests provider settings',
  status: 'ready',
  provider: 'openai',
  model: 'gpt-5',
  reasoningEffort: 'medium',
  approvalMode: 'manual',
  skillsWriteApproval: true,
  memoryWriteApproval: true,
  workspace: '',
  skills: [],
  conversations: [],
};

describe('RightPanel Runtime navigation', () => {
  it('exposes agent settings through a visible Runtime tab', () => {
    const onOpenSettings = vi.fn();
    render(
      <RightPanel
        open
        onOpen={vi.fn()}
        onClose={vi.fn()}
        rightView="runtime"
        onRightView={vi.fn()}
        agent={agent}
        library={[]}
        agentSkills={{}}
        defaultConfig={{ provider: 'openai', model: 'gpt-5', skillsWriteApproval: true, memoryWriteApproval: true }}
        onToggleSkill={vi.fn()}
        onSetSkillEnabled={vi.fn()}
        onUpdateWriteApprovals={vi.fn()}
        onLoadSkillsPage={vi.fn()}
        crons={[]}
        cronStatus="ready"
        cronError=""
        cronPendingId=""
        onCreateCron={vi.fn()}
        onToggleCron={vi.fn()}
        onRunCron={vi.fn()}
        onDeleteCron={vi.fn()}
        onCreateAgent={vi.fn()}
        onOpenSettings={onOpenSettings}
        onDeleteAgent={vi.fn()}
        workspace={{} as never}
        width={330}
        onResize={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Runtime' })).toHaveClass('active');
    fireEvent.click(screen.getByRole('button', { name: 'Agent settings' }));
    expect(onOpenSettings).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: 'Memory' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Test' })).not.toBeInTheDocument();
  });
});
