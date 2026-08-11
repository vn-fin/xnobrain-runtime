import type { ConnectionProvider, ProviderConnectInfo } from '../types';

export function hasUsableProvider(providers: ConnectionProvider[]): boolean {
  return providers.some((provider) => (
    provider.connected || provider.free_models_available === true
  ));
}

/**
 * OpenCode Go issues an API key after the user signs in to OpenCode. Treat it
 * as a guided authentication flow in the UI instead of a generic key field.
 * The backend still stores the issued key through provider runtime's API-key contract.
 */
export function providerUsesAuthFlow(provider: ConnectionProvider): boolean {
  return provider.connection_mode !== 'api-key' || provider.id === 'opencode-go';
}

export function providerUsesInlineApiKey(provider: ConnectionProvider): boolean {
  return provider.connection_mode === 'api-key' && !providerUsesAuthFlow(provider);
}

export function providerConnectNeedsText(provider: ConnectionProvider, info: ProviderConnectInfo): boolean {
  const action = info.required_client_action.trim().toLowerCase().replaceAll('-', '_');
  if (action === 'submit_response' || action === 'submit_text') return true;
  if (action === 'open_url' || action === 'open_url_and_enter_code') return false;

  const providerKey = `${provider.id} ${provider.provider_type} ${provider.brand}`.toLowerCase();
  return providerKey.includes('claude-code') || providerKey.includes('claude_code');
}
