import { useCallback, useEffect, useState } from 'react';
import {
  blendsApi,
  type Blend,
  type BlendCreateInput,
  type BlendModel,
  type BlendPatchInput,
} from '../api/blends';
import { ApiError } from '../api/client';

export type BlendsStatus = 'idle' | 'loading' | 'ready' | 'error';

export type BlendsState = {
  blends: Blend[];
  status: BlendsStatus;
  error: string | null;
  unavailable: boolean;
  refresh: () => Promise<void>;
  createBlend: (input: BlendCreateInput) => Promise<void>;
  updateBlend: (id: string, input: BlendPatchInput) => Promise<void>;
  deleteBlend: (id: string) => Promise<void>;
  availableModels: () => Promise<BlendModel[]>;
};

export function useBlends(active = true): BlendsState {
  const [blends, setBlends] = useState<Blend[]>([]);
  const [status, setStatus] = useState<BlendsStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  const refresh = useCallback(async () => {
    setStatus((current) => (current === 'ready' ? 'ready' : 'loading'));
    setError(null);
    try {
      setBlends(await blendsApi.list());
      setUnavailable(false);
      setStatus('ready');
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 503) setUnavailable(true);
      setError(cause instanceof Error ? cause.message : 'Could not load blends');
      setStatus('error');
    }
  }, []);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  // create/update/delete let errors propagate so the editor can show them.
  const createBlend = useCallback(
    async (input: BlendCreateInput) => { await blendsApi.create(input); await refresh(); },
    [refresh]);
  const updateBlend = useCallback(
    async (id: string, input: BlendPatchInput) => { await blendsApi.update(id, input); await refresh(); },
    [refresh]);
  const deleteBlend = useCallback(
    async (id: string) => { await blendsApi.remove(id); await refresh(); },
    [refresh]);
  const availableModels = useCallback(() => blendsApi.availableModels(), []);

  return { blends, status, error, unavailable, refresh, createBlend, updateBlend, deleteBlend, availableModels };
}
