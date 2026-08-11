import { describe, expect, it } from 'vitest';
import type { ConnectionProvider } from '../types';
import { providerUsesAuthFlow, providerUsesInlineApiKey } from './providers';

function provider(id: string): ConnectionProvider {
  return {
    id,
    display_name: id,
    description: '',
    provider_type: id,
    connection_mode: 'api-key',
    brand: 'opencode',
    connected: false,
    status: 'disconnected',
  };
}

describe('provider connection presentation', () => {
  it('presents OpenCode Go as guided authentication', () => {
    const go = provider('opencode-go');

    expect(providerUsesAuthFlow(go)).toBe(true);
    expect(providerUsesInlineApiKey(go)).toBe(false);
  });

  it('keeps OpenCode Zen in the direct API-key flow', () => {
    const zen = provider('opencode');

    expect(providerUsesAuthFlow(zen)).toBe(false);
    expect(providerUsesInlineApiKey(zen)).toBe(true);
  });
});
