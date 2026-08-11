import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ConnectionProvider } from '../types';
import { mapConnectionProvider } from '../api/mappers/providers';
import '../i18n';
import { ConnectionsView } from './ConnectionsView';

const noop = () => undefined;

function openCodeProvider(
  id: 'opencode-go' | 'opencode',
  displayName: string,
  freeModelsAvailable = false,
): ConnectionProvider {
  return {
    id,
    display_name: displayName,
    description: `${displayName} description`,
    provider_type: id,
    connection_mode: 'api-key',
    brand: 'opencode',
    connected: false,
    free_models_available: freeModelsAvailable,
    status: freeModelsAvailable ? 'available' : 'disconnected',
  };
}

function compatibleProvider(id: 'xai' | 'openrouter' | 'groq', displayName: string): ConnectionProvider {
  return mapConnectionProvider({
    id,
    display_name: displayName,
    description: `${displayName} through 9router`,
    provider_type: id,
    connection_mode: 'api-key',
    connected: false,
    status: 'disconnected',
  });
}

describe('ConnectionsView OpenCode providers', () => {
  it('guides Go through authentication and keeps Zen in the direct-key form', () => {
    const onConnect = vi.fn();
    render(
      <ConnectionsView
        providers={[
          openCodeProvider('opencode-go', 'OpenCode Go'),
          openCodeProvider('opencode', 'OpenCode Zen', true),
        ]}
        keyProviderId="opencode"
        pendingId={null}
        onSelectKeyProvider={noop}
        onConnect={onConnect}
        onDisconnect={noop}
        onTest={noop}
        onSaveKey={noop}
        onClose={noop}
        connectionsByProvider={{}}
        usageByConnection={{}}
        rowPendingId={null}
        onLoadConnections={noop}
        onAddAccount={noop}
        onSetAccountActive={noop}
        onReorderAccount={noop}
        onTestAccount={noop}
        onRemoveAccount={noop}
        onLoadAccountUsage={noop}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Authenticate' }));
    expect(onConnect).toHaveBeenCalledWith('opencode-go');

    const providerSelect = screen.getByRole('combobox');
    const options = within(providerSelect).getAllByRole('option');
    expect(options.map((option) => option.textContent)).toEqual(['OpenCode Zen']);
    expect(screen.getByText('Free models available without an API key.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Subscriptions' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'API keys' })).toBeInTheDocument();
  });
});

describe('ConnectionsView OpenAI-compatible presets', () => {
  it('shows xAI, OpenRouter, and Groq with their local brand icons', () => {
    render(
      <ConnectionsView
        providers={[
          compatibleProvider('xai', 'xAI'),
          compatibleProvider('openrouter', 'OpenRouter'),
          compatibleProvider('groq', 'Groq'),
        ]}
        keyProviderId="xai"
        pendingId={null}
        onSelectKeyProvider={noop}
        onConnect={noop}
        onDisconnect={noop}
        onTest={noop}
        onSaveKey={noop}
        onClose={noop}
        connectionsByProvider={{}}
        usageByConnection={{}}
        rowPendingId={null}
        onLoadConnections={noop}
        onAddAccount={noop}
        onSetAccountActive={noop}
        onReorderAccount={noop}
        onTestAccount={noop}
        onRemoveAccount={noop}
        onLoadAccountUsage={noop}
      />,
    );

    for (const [name, icon] of [
      ['xAI', '/providers/xai.svg'],
      ['OpenRouter', '/providers/openrouter.svg'],
      ['Groq', '/providers/groq.svg'],
    ]) {
      const card = screen.getByText(name, { selector: '.conn-card-head > strong' }).closest('article');
      expect(card).not.toBeNull();
      expect(card?.querySelector('img')).toHaveAttribute('src', icon);
    }
  });
});
