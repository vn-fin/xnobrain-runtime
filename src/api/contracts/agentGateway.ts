export type AgentConfigDTO = {
  approval_mode?: string;
  base_url?: string;
  config?: Record<string, unknown>;
  effort?: string;
  language?: string;
  model?: string;
  memory_write_approval?: boolean;
  provider?: string;
  reasoning?: boolean;
  reasoning_effort?: string;
  soul?: string;
  stream_output?: boolean;
  skills_write_approval?: boolean;
  system_prompt?: string;
};

export type AgentDTO = {
  api_server?: { host?: string; port?: number };
  config?: AgentConfigDTO;
  created_at?: string;
  deleted_at?: string;
  description?: string;
  display_name?: string;
  id?: string;
  metadata?: { config?: AgentConfigDTO };
  name?: string;
  soul?: string;
  status?: string;
  title?: string;
  updated_at?: string;
  user_id?: string;
};

export type AgentCreateRequestDTO = { display_name: string; description: string };
export type AgentMetadataUpdateRequestDTO = { display_name?: string; title?: string; description?: string; metadata?: AgentDTO['metadata'] };
export type AgentConfigUpdateRequestDTO = AgentConfigDTO;
export type AgentTestResponseDTO = { agent_id?: string; healthy?: boolean; message?: string; status?: string };

export type AgentSkillDTO = {
  skill_id?: string;
  name?: string;
  category?: string;
  description?: string;
  enabled?: boolean;
  installed?: boolean;
  path?: string;
  version?: string;
};

export type AgentSkillListResponseDTO = {
  agent_id?: string;
  object?: string;
  skills?: AgentSkillDTO[];
};

export type AgentSkillsOverviewResponseDTO = {
  skills?: AgentSkillDTO[];
  agents?: Record<string, AgentSkillDTO[]>;
};

export type AgentSkillInstallRequestDTO = {
  skill_id?: string;
  name?: string;
  category?: string;
  content?: string;
  enable: boolean;
  force?: boolean;
  source?: string;
  timeout_seconds?: number;
};

export type ProviderConnectorDTO = {
  id?: string;
  display_name?: string;
  description?: string;
  provider_type?: string;
  connection_mode?: string;
  environment_variable?: string;
  connected?: boolean;
  status?: string;
  last_test_status?: string;
  default_model?: string;
  available_models?: string[];
  connection_count?: number;
  base_url?: string;
  requires_base_url?: boolean;
  capabilities?: string[];
  requires_restart?: boolean;
};

export type ProviderConnectInfoDTO = {
  auth_code?: string;
  connection_mode?: string;
  instructions?: string;
  login_url?: string;
  provider?: ProviderConnectorDTO;
  provider_id?: string;
  provider_type?: string;
  required_client_action?: string;
  status?: string;
  text_label?: string;
  user_code?: string;
  verification_url?: string;
};

export type ProviderModelsResponseDTO = {
  default_model?: string;
  models?: Array<{ id?: string; reasoning?: string[] }>;
  provider_id?: string;
  provider_type?: string;
};

export type ProviderModelReasoningResponseDTO = {
  model?: string;
  provider_id?: string;
  provider_type?: string;
  reasoning?: string[];
};

export type WorkspaceEntryDTO = {
  name?: string;
  path?: string;
  type?: string;
  is_dir?: boolean;
  size?: string | number;
  size_bytes?: number;
  modified?: string;
  modified_at?: string;
  updated_at?: string;
  language?: string;
};

export type WorkspaceListDTO = WorkspaceEntryDTO[] | { entries?: WorkspaceEntryDTO[]; files?: WorkspaceEntryDTO[] };
export type WorkspaceFileDTO = { path?: string; content?: string; content_base64?: string; mime_type?: string; type?: string };
