import type { ConnectionProvider, ProviderConnectInfo } from '../types';

export function hasUsableProvider(providers: ConnectionProvider[]): boolean {
  return providers.some((provider) => (
    provider.connected || provider.free_models_available === true
  ));
}

export function providerConnectNeedsText(provider: ConnectionProvider, info: ProviderConnectInfo): boolean {
  const action = info.required_client_action.trim().toLowerCase().replaceAll('-', '_');
  if (action === 'submit_response' || action === 'submit_text') return true;
  if (action === 'open_url' || action === 'open_url_and_enter_code') return false;

  const providerKey = `${provider.id} ${provider.provider_type} ${provider.brand}`.toLowerCase();
  return providerKey.includes('claude-code') || providerKey.includes('claude_code');
}
