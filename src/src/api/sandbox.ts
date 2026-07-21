import { request, requestRaw } from './client';
import { readSSE, type SSEEvent } from './stream';
import type { SandboxDetailDTO } from './contracts/sandboxes';
import { mapSandboxData } from './mappers/sandbox';
import type { SandboxData } from '../types';

export type SandboxResult = { provisioned: boolean; data: SandboxData | null };
const SANDBOX_BASE = '/sandboxes/v1/me/sandboxes';

export const sandboxApi = {
  async get(): Promise<SandboxResult> {
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
