import { request, requestRaw } from './client';
import { readSSE, type SSEEvent } from './stream';
import type { SandboxDetailDTO } from './contracts/sandboxes';
import { mapSandboxData } from './mappers/sandbox';
import type { SandboxData } from '../types';
import { brain4AllRuntime } from '../runtime';

export type SandboxResult = { provisioned: boolean; data: SandboxData | null };
export type SandboxSetupProgress = { percent: number; message: string };
const SANDBOX_BASE = '/api/brain/v1/sandboxes';
const CONTROL_BASE = brain4AllRuntime.api.controlBaseUrl.replace(/\/+$/, '');
const MANAGED_WORKSPACE = brain4AllRuntime.edition === 'cloud' && CONTROL_BASE.length > 0;
const WORKSPACE_CURRENT = `${CONTROL_BASE}/api/brain-control/v1/workspace/current`;
const WORKSPACE_CREATE = `${CONTROL_BASE}/api/brain-control/v1/workspace/create`;

type ManagedWorkspace = {
  status?: string;
};

export const sandboxApi = {
  managed: MANAGED_WORKSPACE,

  async get(): Promise<SandboxResult> {
    if (MANAGED_WORKSPACE) {
      const workspace = await request<ManagedWorkspace>(WORKSPACE_CURRENT);
      return {
        provisioned: workspace.status === 'ready',
        data: null,
      };
    }
    const detail = await request<SandboxDetailDTO>(`${SANDBOX_BASE}/detail`).catch((error: unknown) => {
      if ((error as { status?: number } | null)?.status === 404) return null;
      throw error;
    });
    if (!detail) return { provisioned: false, data: null };
    return {
      provisioned: true,
      data: mapSandboxData(detail),
    };
  },

  async stream(onDetail: (result: SandboxResult) => void, signal?: AbortSignal): Promise<void> {
    if (MANAGED_WORKSPACE) {
      await new Promise<void>((resolve) => {
        if (signal?.aborted) {
          resolve();
          return;
        }
        signal?.addEventListener('abort', () => resolve(), { once: true });
      });
      return;
    }
    const response = await requestRaw(`${SANDBOX_BASE}/detail/stream`, {
      headers: { Accept: 'text/event-stream' },
      signal,
    });
    await readSSE(
      response,
      (event: SSEEvent) => {
        if (event.event !== 'stats' && event.event !== 'message') return;
        if (!event.data || typeof event.data !== 'object') return;
        onDetail({ provisioned: true, data: mapSandboxData(event.data as SandboxDetailDTO) });
      },
      signal,
    );
  },

  setup: () => request<unknown>(`${SANDBOX_BASE}/setup`, { method: 'POST' }),

  /**
   * Provision the sandbox while streaming progress. The endpoint emits SSE
   * frames whose data payload contains a completion percentage and message.
   * Numeric events from older sandbox APIs remain supported.
   */
  async setupStream(onProgress: (progress: SandboxSetupProgress) => void, signal?: AbortSignal): Promise<void> {
    const response = await requestRaw(MANAGED_WORKSPACE ? WORKSPACE_CREATE : `${SANDBOX_BASE}/setup?stream=true`, {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      body: '',
      signal,
    });
    await readSSE(
      response,
      (event: SSEEvent) => {
        if (event.data && typeof event.data === 'object') {
          const value = event.data as Record<string, unknown>;
          const percent = Number(value.percent);
          const message = String(value.message ?? '').trim();
          if (event.event === 'error') throw new Error(message || 'VM provisioning failed');
          if (Number.isFinite(percent)) {
            onProgress({
              percent: Math.max(0, Math.min(100, percent)),
              message: message || 'Provisioning VM',
            });
          }
          return;
        }
        const percent = typeof event.data === 'number' ? event.data : Number(event.data);
        if (Number.isFinite(percent)) {
          if (percent < 0) throw new Error('VM provisioning failed');
          onProgress({
            percent: Math.max(0, Math.min(100, percent)),
            message: percent >= 100 ? 'VM is ready' : 'Provisioning VM',
          });
        }
      },
      signal,
    );
  },
};
