import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import i18n from '../i18n';
import type { AgentSkill } from '../types';
import { SkillsView } from './SkillsView';

const skill: AgentSkill = {
  skill_id: 'writer',
  name: 'writer',
  category: 'office',
  description: 'Writes documents',
  enabled: true,
  installed: true,
  path: '/skills/writer',
};

describe('SkillsView default profile controls', () => {
  beforeEach(async () => {
    await i18n.changeLanguage('en');
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
});
