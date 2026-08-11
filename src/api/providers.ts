import { request } from './client';
import type {
  ProviderConnectInfoDTO,
  ProviderConnectorDTO,
  ProviderModelReasoningResponseDTO,
  ProviderModelsResponseDTO,
} from './contracts/agentGateway';
import { mapConnectionProvider } from './mappers/providers';
import type { ConnectionMode, ConnectionProvider, ProviderConnectInfo } from '../types';

const ROOT = '/xnobrain/api/runtime/v1/providers';
const encoded = (value: string) => encodeURIComponent(value);

function mode(value?: string): ConnectionMode {
  if (value === 'api_key' || value === 'api-key') return 'api-key';
  if (value === 'device_code' || value === 'device-code') return 'device-code';
  if (value === 'no_auth' || value === 'no-auth') return 'no-auth';
  return 'cli';
}

function mapConnectInfo(dto: ProviderConnectInfoDTO): ProviderConnectInfo {
  return {
    connection_mode: mode(dto.connection_mode),
    required_client_action: dto.required_client_action ?? '',
    login_url: dto.login_url ?? '',
    ...(dto.verification_url ? { verification_url: dto.verification_url } : {}),
    ...(dto.user_code ? { user_code: dto.user_code } : {}),
    instructions: dto.instructions ?? '',
    text_label: dto.text_label ?? '',
    status: dto.status ?? '',
  };
}

export type ProviderUpdateInput = {
  api_key?: string;
  base_url?: string;
  default_model?: string;
  display_name?: string;
  status?: string;
};

/** One account for a provider. Never carries key/token material. */
export type ProviderConnection = {
  id: string;
  provider: string;
  auth_type: string;
  name: string;
  email: string;
  active: boolean;
  priority: number;
  default_model: string;
  test_status: string;
  last_error: string;
};

export type ConnectionQuota = {
  name: string;
  used: number;
  total: number;
  remaining_percent: number;
  reset_at: string;
  unlimited: boolean;
};

export type ConnectionUsage = {
  connection_id: string;
  available: boolean;
  plan: string;
  message: string;
  quotas: ConnectionQuota[];
};

export type ConnectionTestResult = {
  connection_id?: string;
  healthy?: boolean;
  status?: string;
  message?: string;
};

// Response payload of POST .../test (the envelope's `data` block).
export type ProviderTestResult = {
  provider_id?: string;
  status?: string;
  healthy?: boolean;
  message?: string;
  capabilities?: string[];
};

// Shape of GET .../connect (data), used to read available models for a
// connected provider. The provider fields may be at the top level of `data`
// or nested under `data.provider` depending on the endpoint version.
type ProviderConnectFields = {
  connected?: boolean;
  status?: string;
  default_model?: string;
  available_models?: string[];
};
type ProviderConnectStatusDTO = ProviderConnectFields & {
  provider?: ProviderConnectFields;
};

export const providersApi = {
  async list(): Promise<ConnectionProvider[]> {
    const data = await request<ProviderConnectorDTO[]>(ROOT);
    return (Array.isArray(data) ? data : []).map(mapConnectionProvider);
  },

  async connect(id: string): Promise<ProviderConnectInfo> {
    return mapConnectInfo(await request<ProviderConnectInfoDTO>(`${ROOT}/${encoded(id)}/connect`, { method: 'POST' }));
  },

  async connectStatus(id: string): Promise<ProviderConnectInfo> {
    return mapConnectInfo(await request<ProviderConnectInfoDTO>(`${ROOT}/${encoded(id)}/connect`));
  },

  async submitAuth(id: string, text: string): Promise<ProviderConnectInfo> {
    return mapConnectInfo(
      await request<ProviderConnectInfoDTO>(`${ROOT}/${encoded(id)}/connect`, {
        method: 'PUT',
        body: JSON.stringify({ text }),
      }),
    );
  },

  async update(id: string, input: ProviderUpdateInput): Promise<void> {
    await request<unknown>(`${ROOT}/${encoded(id)}/update`, { method: 'PATCH', body: JSON.stringify(input) });
  },

  saveKey(id: string, apiKey: string, baseUrl?: string): Promise<void> {
    return providersApi.update(id, { api_key: apiKey, ...(baseUrl?.trim() ? { base_url: baseUrl.trim() } : {}) });
  },

  async disconnect(id: string): Promise<void> {
    await request<unknown>(`${ROOT}/${encoded(id)}/disconnect`, { method: 'POST' });
  },

  test(id: string): Promise<ProviderTestResult> {
    return request(`${ROOT}/${encoded(id)}/test`, { method: 'POST' });
  },

  models(id: string): Promise<ProviderModelsResponseDTO> {
    return request(`${ROOT}/${encoded(id)}/models`);
  },

  /**
   * Read the models available for a connected provider from its connect
   * status (GET .../connect). The provider fields may be at the top level of
   * `data` or nested under `data.provider`; sorted by name.
   */
  async connectModels(id: string): Promise<{ defaultModel: string; models: string[] }> {
    const data = await request<ProviderConnectStatusDTO>(`${ROOT}/${encoded(id)}/connect`);
    const source = data?.provider ?? data ?? {};
    const models = [...(source.available_models ?? [])]
      .filter(Boolean)
      .sort((a, b) => a.localeCompare(b));
    return { defaultModel: source.default_model ?? '', models };
  },

  reasoning(id: string, model: string): Promise<ProviderModelReasoningResponseDTO> {
    return request(`${ROOT}/${encoded(id)}/models/${encoded(model)}/reasoning`);
  },

  // --- multi-account connections ---

  async listConnections(id: string): Promise<ProviderConnection[]> {
    const data = await request<{ connections: ProviderConnection[] }>(`${ROOT}/${encoded(id)}/connections`);
    return data?.connections ?? [];
  },

  async addConnection(id: string, input: { api_key: string; name?: string; default_model?: string }): Promise<ProviderConnection> {
    const data = await request<{ connection: ProviderConnection }>(`${ROOT}/${encoded(id)}/connections`, {
      method: 'POST',
      body: JSON.stringify(input),
    });
    return data.connection;
  },

  async patchConnection(id: string, connectionId: string, input: { active?: boolean; priority?: number }): Promise<ProviderConnection> {
    const data = await request<{ connection: ProviderConnection }>(
      `${ROOT}/${encoded(id)}/connections/${encoded(connectionId)}`,
      { method: 'PATCH', body: JSON.stringify(input) },
    );
    return data.connection;
  },

  testConnection(id: string, connectionId: string): Promise<ConnectionTestResult> {
    return request(`${ROOT}/${encoded(id)}/connections/${encoded(connectionId)}/test`, { method: 'POST' });
  },

  async deleteConnection(id: string, connectionId: string): Promise<void> {
    await request<unknown>(`${ROOT}/${encoded(id)}/connections/${encoded(connectionId)}`, { method: 'DELETE' });
  },

  connectionUsage(id: string, connectionId: string): Promise<ConnectionUsage> {
    return request(`${ROOT}/${encoded(id)}/connections/${encoded(connectionId)}/usage`);
  },
};
