import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Blend } from '../../api/blends';
import { BlendEditorDialog } from './BlendEditorDialog';

afterEach(cleanup);

const blend: Blend = {
  id: 'cmb-smart',
  name: 'smart-work',
  display_name: 'smart-work',
  system: false,
  read_only: false,
  models: ['cx/fast', 'cx/normal', 'cx/deep', 'cx/deeper'],
  strategy: 'smart-route',
  judge_model: null,
  sticky_limit: null,
  sticky_limit_scope: 'global',
  smart_route: {
    quick: [{ model: 'cx/fast', reasoning: 'low', context_length: 32_000 }],
    normal: [{ model: 'cx/normal', reasoning: 'auto', context_length: 128_000 }],
    difficult: [
      { model: 'cx/deep', reasoning: 'high', context_length: 200_000 },
      { model: 'cx/deeper', reasoning: 'auto', context_length: 256_000 },
    ],
    uncertain_tier: 'difficult',
  },
  guaranteed_context: 32_000,
  maximum_context: 256_000,
  created_at: '',
  updated_at: '',
};

describe('BlendEditorDialog Smart route', () => {
  it('shows grouped models and saves each model thinking level', async () => {
    const save = vi.fn(async () => undefined);
    render(
      <BlendEditorDialog
        blend={blend}
        loadModels={async () => [
          { id: 'cx/fast', provider: 'codex', name: 'Fast', context_length: 32_000, reasoning_levels: ['low', 'medium'] },
          { id: 'cx/normal', provider: 'codex', name: 'Normal', context_length: 128_000, reasoning_levels: ['low', 'medium', 'high'] },
          { id: 'cx/deep', provider: 'codex', name: 'Deep', context_length: 200_000, reasoning_levels: ['medium', 'high'] },
          { id: 'cx/deeper', provider: 'codex', name: 'Deeper', context_length: 256_000, reasoning_levels: ['low', 'medium', 'high'] },
        ]}
        onSave={save}
        onClose={vi.fn()}
      />,
    );

    expect(screen.getByText('Quick tasks')).toBeInTheDocument();
    expect(screen.getByText('Normal tasks')).toBeInTheDocument();
    expect(screen.getByText('Difficult tasks')).toBeInTheDocument();
    expect(screen.getByText(/guaranteed 32K · largest 256K/)).toBeInTheDocument();

    fireEvent.change(await screen.findByLabelText('cx/normal thinking'), {
      target: { value: 'medium' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0]).toMatchObject({
      name: 'smart-work',
      strategy: 'smart-route',
      models: ['cx/fast', 'cx/normal', 'cx/deep', 'cx/deeper'],
      smart_route: {
        normal: [{ model: 'cx/normal', reasoning: 'medium' }],
        uncertain_tier: 'difficult',
      },
    });
  });

  it('allows one model in different task tiers and submits unique combo models', async () => {
    const save = vi.fn(async () => undefined);
    render(
      <BlendEditorDialog
        blend={blend}
        loadModels={async () => [
          { id: 'cx/fast', provider: 'codex', name: 'Fast', context_length: 32_000, reasoning_levels: ['low', 'medium'] },
          { id: 'cx/normal', provider: 'codex', name: 'Normal', context_length: 128_000, reasoning_levels: ['low', 'medium', 'high'] },
          { id: 'cx/deep', provider: 'codex', name: 'Deep', context_length: 200_000, reasoning_levels: ['medium', 'high'] },
          { id: 'cx/deeper', provider: 'codex', name: 'Deeper', context_length: 256_000, reasoning_levels: ['low', 'medium', 'high'] },
        ]}
        onSave={save}
        onClose={vi.fn()}
      />,
    );

    fireEvent.change(await screen.findByLabelText('Add model to normal tasks'), {
      target: { value: 'cx/fast' },
    });
    fireEvent.click(screen.getAllByRole('button', { name: 'Add' })[1]);
    fireEvent.change(screen.getAllByLabelText('cx/fast thinking')[1], {
      target: { value: 'medium' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(save).toHaveBeenCalledOnce());
    expect(save.mock.calls[0][0].models).toEqual([
      'cx/fast', 'cx/normal', 'cx/deep', 'cx/deeper',
    ]);
    expect(save.mock.calls[0][0].smart_route).toMatchObject({
      quick: [{ model: 'cx/fast', reasoning: 'low' }],
      normal: [
        { model: 'cx/normal', reasoning: 'auto' },
        { model: 'cx/fast', reasoning: 'medium' },
      ],
    });
  });
});
