import type { ProviderConnectorDTO } from '../contracts/agentGateway';
import type { ConnectionMode, ConnectionProvider, ProviderBrand, ProviderConnector } from '../../types';

function connectionMode(value?: string): ConnectionMode {
  if (value === 'api_key' || value === 'api-key') return 'api-key';
  if (value === 'device_code' || value === 'device-code') return 'device-code';
  if (value === 'no_auth' || value === 'no-auth') return 'no-auth';
  return 'cli';
}

function providerBrand(dto: ProviderConnectorDTO): ProviderBrand {
  const value = `${dto.id ?? ''} ${dto.provider_type ?? ''}`.toLowerCase();
  if (value.includes('opencode')) return 'opencode';
  if (value.includes('deepseek')) return 'deepseek';
  if (value.includes('moonshot') || value.includes('kimi')) return 'moonshot';
  if (value.includes('qwen')) return 'qwen';
  if (value.includes('openai-like') || value.includes('openai-compatible')) return 'openai-like';
  if (value.includes('openrouter')) return 'openrouter';
  if (value.includes('gemini') || value.includes('google') || value.includes('antigravity')) return 'gemini';
  if (value.includes('claude-code')) return 'claude';
  if (value.includes('anthropic') || value.includes('claude')) return 'anthropic';
  return 'openai';
}

export function mapConnectionProvider(dto: ProviderConnectorDTO): ConnectionProvider {
  return {
    id: dto.id ?? '',
    display_name: dto.display_name ?? dto.id ?? '',
    description: dto.description ?? '',
    provider_type: dto.provider_type ?? '',
    connection_mode: connectionMode(dto.connection_mode),
    ...(dto.environment_variable ? { environment_variable: dto.environment_variable } : {}),
    brand: providerBrand(dto),
    connected: dto.connected ?? false,
    ...(dto.free_models_available !== undefined ? { free_models_available: dto.free_models_available } : {}),
    status: dto.status ?? 'not connected',
    ...(dto.last_test_status ? { last_test_status: dto.last_test_status } : {}),
    ...(dto.default_model ? { default_model: dto.default_model } : {}),
    ...(dto.available_models ? { available_models: dto.available_models } : {}),
    ...(typeof dto.connection_count === 'number' ? { connection_count: dto.connection_count } : {}),
    ...(dto.base_url !== undefined ? { base_url: dto.base_url } : {}),
    ...(dto.requires_base_url !== undefined ? { requires_base_url: dto.requires_base_url } : {}),
  };
}

export function mapProviderConnector(dto: ProviderConnectorDTO): ProviderConnector {
  return {
    id: dto.id ?? '',
    display_name: dto.display_name ?? dto.id ?? '',
    provider_type: dto.provider_type ?? '',
    connected: dto.connected ?? false,
    connection_mode: connectionMode(dto.connection_mode),
    default_model: dto.default_model ?? '',
    status: dto.status ?? 'not connected',
  };
}
