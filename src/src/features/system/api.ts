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

export type DeploymentStatus = {
  mode: 'local' | 'cloud';
  gateway_configured: boolean;
  runtime_transport: string;
  managed_cloud: boolean;
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
  missing_environment?: string[];
  credentials_source?: string;
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
  credentials_source?: string;
  environment_filled?: string[];
};

export type BundleTransfer = {
  export_id?: string;
  upload_id?: string;
  filename: string;
  size: number;
  sha256: string;
  chunk_size: number;
  total_parts: number;
  complete?: boolean;
  preview?: BundleDryRun;
};

export type TransferProgress = { loaded: number; total: number; percent: number };

function bundleForm(file: File) {
  const form = new FormData();
  form.set('file', file);
  return form;
}

async function sha256(value: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', value);
  return [...new Uint8Array(digest)].map((item) => item.toString(16).padStart(2, '0')).join('');
}

export const systemApi = {
  deployment: () => request<DeploymentStatus>('/api/v1/system/deployment'),
  device: () => request<DeviceStatus>('/api/v1/device'),
  pair: () => request<{ pairing: boolean }>('/api/v1/device/pair', { method: 'POST' }),
  unpair: () => request<{ unpaired: boolean }>('/api/v1/device/unpair', { method: 'POST' }),
  inspect: (file: File) => requestMultipart<BundleInspection>('/api/v1/bundles/inspect', bundleForm(file)),
  dryRun: (file: File) => requestMultipart<BundleDryRun>('/api/v1/bundles/dry-run', bundleForm(file)),
  apply: (file: File) => requestMultipart<ImportReport>('/api/v1/bundles/apply', bundleForm(file)),
  upload: async (file: File, onProgress?: (progress: TransferProgress) => void): Promise<BundleTransfer> => {
    const transfer = await request<BundleTransfer>('/api/v1/bundles/uploads', {
      method: 'POST',
      body: JSON.stringify({ filename: file.name, size: file.size }),
    });
    try {
      for (let part = 0; part < transfer.total_parts; part += 1) {
        const start = part * transfer.chunk_size;
        const chunk = file.slice(start, Math.min(file.size, start + transfer.chunk_size));
        const partHash = await sha256(await chunk.arrayBuffer());
        await requestRaw(`/api/v1/bundles/uploads/${transfer.upload_id}/parts/${part}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/octet-stream', 'X-Part-SHA256': partHash },
          body: chunk,
        });
        const loaded = Math.min(file.size, start + chunk.size);
        onProgress?.({ loaded, total: file.size, percent: Math.round((loaded / file.size) * 100) });
      }
      return request<BundleTransfer>(`/api/v1/bundles/uploads/${transfer.upload_id}/complete`, {
        method: 'POST', body: JSON.stringify({}),
      });
    } catch (error) {
      if (transfer.upload_id) void request(`/api/v1/bundles/uploads/${transfer.upload_id}`, { method: 'DELETE' }).catch(() => undefined);
      throw error;
    }
  },

  applyUpload: (uploadId: string, environment: Record<string, string> = {}) =>
    request<ImportReport>(`/api/v1/bundles/uploads/${uploadId}/apply`, {
      method: 'POST', body: JSON.stringify({ environment }),
    }),

  cancelUpload: (uploadId: string) => request(`/api/v1/bundles/uploads/${uploadId}`, { method: 'DELETE' }),

  export: async (agentIds: string[], onProgress?: (progress: TransferProgress) => void) => {
    const transfer = await request<BundleTransfer>('/api/v1/bundles/exports', {
      method: 'POST', body: JSON.stringify({ agent_ids: agentIds }),
    });
    const parts: ArrayBuffer[] = [];
    try {
      for (let part = 0; part < transfer.total_parts; part += 1) {
        const response = await requestRaw(`/api/v1/bundles/exports/${transfer.export_id}/parts/${part}`);
        const buffer = await response.arrayBuffer();
        const expected = response.headers.get('X-Part-SHA256');
        if (expected && await sha256(buffer) !== expected) throw new Error(`Downloaded profile part ${part} failed checksum verification.`);
        parts.push(buffer);
        const loaded = Math.min(transfer.size, (part + 1) * transfer.chunk_size);
        onProgress?.({ loaded, total: transfer.size, percent: Math.round((loaded / transfer.size) * 100) });
      }
      return {
        blob: new Blob(parts, { type: 'application/vnd.open-lumora.bundle+zip; version=1' }),
        filename: transfer.filename,
      };
    } finally {
      if (transfer.export_id) void request(`/api/v1/bundles/exports/${transfer.export_id}`, { method: 'DELETE' }).catch(() => undefined);
    }
  },
};
