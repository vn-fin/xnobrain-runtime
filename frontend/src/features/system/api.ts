import { request, requestMultipart, requestRaw } from '../../api/client';

export type DeviceStatus = {
  enabled: boolean;
  connected: boolean;
  device_id?: string;
  endpoint?: string;
  last_connected_at?: string;
  last_error_code?: string;
  queued_commands: number;
  recovery_code?: string;
  claimed: boolean;
};

export type BundleInspection = {
  manifest: { export_id: string; source_version: string; agents: Array<{ id: string; name: string }>; teams?: Array<{ id: string; name: string }> };
  files: number;
  expanded_bytes: number;
  warnings: string[];
};

export type BundleDryRun = {
  inspection: BundleInspection;
  collisions: string[];
  approval_resets: number;
  paused_cron_jobs: number;
  providers_reset: number;
  quarantined_code: string[];
  storage_required: number;
  team_collisions?: string[];
};

export type ImportReport = {
  export_id: string;
  agent_id_mappings: Record<string, string>;
  team_id_mappings?: Record<string, string>;
  disabled_team_members?: number;
  paused_cron_jobs: number;
  approval_resets: number;
  providers_reset: number;
  quarantined_code: string[];
  warnings: string[];
};

function bundleForm(file: File) {
  const form = new FormData();
  form.set('file', file);
  return form;
}

export const systemApi = {
  device: () => request<DeviceStatus>('/api/v1/device'),
  pair: () => request<{ pairing: boolean }>('/api/v1/device/pair', { method: 'POST' }),
  unpair: () => request<{ unpaired: boolean }>('/api/v1/device/unpair', { method: 'POST' }),
  inspect: (file: File) => requestMultipart<BundleInspection>('/api/v1/bundles/inspect', bundleForm(file)),
  dryRun: (file: File) => requestMultipart<BundleDryRun>('/api/v1/bundles/dry-run', bundleForm(file)),
  apply: (file: File) => requestMultipart<ImportReport>('/api/v1/bundles/apply', bundleForm(file)),
  export: async (agentIds: string[]) => {
    const response = await requestRaw('/api/v1/bundles/export', { method: 'POST', body: JSON.stringify({ agent_ids: agentIds }) });
    return response.blob();
  },
};
