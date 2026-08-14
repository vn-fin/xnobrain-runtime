import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ConnectionProvider } from '../types';
import { mapConnectionProvider } from '../api/mappers/providers';
import '../i18n';
import { ConnectionsView } from './ConnectionsView';

const noop = () => undefined;

afterEach(cleanup);

function openCodeProvider(
  id: 'opencode-go' | 'opencode',
  displayName: string,
): ConnectionProvider {
  return {
    id,
    display_name: displayName,
    description: `${displayName} description`,
    provider_type: id,
    connection_mode: 'api-key',
    brand: 'opencode',
    connected: false,
    status: 'disconnected',
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
          openCodeProvider('opencode', 'OpenCode Zen'),
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
    expect(screen.getByRole('button', { name: 'Add API key' })).toBeInTheDocument();
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

  it('rejects malformed custom base URLs and only accepts deliberately entered keys', () => {
    const onSaveKey = vi.fn();
    const custom: ConnectionProvider = {
      id: 'openai-like',
      display_name: 'OpenAI-compatible',
      description: 'Custom endpoint',
      provider_type: 'openai-like',
      connection_mode: 'api-key',
      brand: 'openai',
      connected: false,
      status: 'disconnected',
      requires_base_url: true,
      base_url: '',
    };
    render(
      <ConnectionsView
        providers={[custom]}
        keyProviderId="openai-like"
        pendingId={null}
        onSelectKeyProvider={noop}
        onConnect={noop}
        onDisconnect={noop}
        onTest={noop}
        onSaveKey={onSaveKey}
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

    const key = screen.getByLabelText('Provider API key');
    const baseUrl = screen.getByLabelText('Provider base URL');
    const save = screen.getByRole('button', { name: 'Save' });
    expect(key).toHaveAttribute('autocomplete', 'new-password');

    fireEvent.change(key, { target: { value: 'browser-filled-login-password' } });
    expect(save).toBeDisabled();

    fireEvent.pointerDown(key);
    fireEvent.change(key, { target: { value: 'qa-key' } });
    fireEvent.change(baseUrl, { target: { value: 'not-a-valid-url' } });
    fireEvent.blur(baseUrl);
    expect(screen.getByRole('alert')).toHaveTextContent('absolute HTTP(S) base URL');
    expect(baseUrl).toHaveAttribute('aria-invalid', 'true');
    expect(save).toBeDisabled();

    fireEvent.change(baseUrl, { target: { value: 'http://localhost:11434/v1' } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    expect(onSaveKey).toHaveBeenCalledWith('openai-like', 'qa-key', 'http://localhost:11434/v1');
  });
});
