import { request, requestRaw } from './client';
import { readSSE, type SSEEvent } from './stream';
import type { DefaultSandboxDTO, DefaultSandboxStatsDTO, SandboxHealthDTO, SandboxMetricsDTO } from './contracts/sandboxes';
import { mapSandboxData } from './mappers/sandbox';
import type { SandboxData } from '../types';

export type SandboxResult = { provisioned: boolean; data: SandboxData | null };
const SANDBOX_BASE = '/sandboxes/v1/me/sandboxes';

export const sandboxApi = {
  async get(): Promise<SandboxResult> {
    const info = await request<DefaultSandboxDTO>(`${SANDBOX_BASE}/info`).catch((error: unknown) => {
      if ((error as { status?: number } | null)?.status === 404) return null;
      throw error;
    });
    if (!info) return { provisioned: false, data: null };

    const [metrics, stats, health] = await Promise.all([
      request<SandboxMetricsDTO>(`${SANDBOX_BASE}/metrics`),
      request<DefaultSandboxStatsDTO>(`${SANDBOX_BASE}/stats`),
      request<SandboxHealthDTO>(`${SANDBOX_BASE}/health`),
    ]);
    return {
      provisioned: true,
      data: mapSandboxData(info, metrics, stats.system ?? {}, health),
    };
  },

  setup: () => request<unknown>(`${SANDBOX_BASE}/setup`, { method: 'POST' }),

  /**
   * Provision the sandbox while streaming progress. The endpoint emits SSE
   * frames whose data payload is a completion percentage (e.g. `data: 30`),
   * ending at `100`. Each parsed percentage is reported via `onProgress`.
   */
  async setupStream(onProgress: (percent: number) => void, signal?: AbortSignal): Promise<void> {
    const response = await requestRaw(`${SANDBOX_BASE}/setup?stream=true`, {
      method: 'POST',
      headers: { Accept: 'text/event-stream' },
      body: '',
      signal,
    });
    await readSSE(
      response,
      (event: SSEEvent) => {
        const value = typeof event.data === 'number' ? event.data : Number(event.data);
        if (Number.isFinite(value)) onProgress(Math.max(0, Math.min(100, value)));
      },
      signal,
    );
  },
};
