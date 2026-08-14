import { request } from './client';
import type { Checkpoint, CheckpointDiff, CheckpointStatus, FileVersion } from '../types';

const root = (agentId: string) => `/xnobrain/api/runtime/v1/agents/${encodeURIComponent(agentId)}/checkpoints`;

type StatusDTO = { agent_id: string; enabled: boolean; available: boolean; unavailable_reason?: string; checkpoint_count: number; retained_bytes?: number; max_snapshots: number; max_file_size_bytes: number };
type CheckpointDTO = { id: string; short_id: string; created_at: string; reason: string; trigger: string; files_changed: number; insertions: number; deletions: number };
type DiffDTO = { checkpoint_id: string; short_id: string; files: Array<{ path: string; status: 'added' | 'modified' | 'deleted'; insertions: number; deletions: number; binary: boolean }>; patch: string; truncated: boolean; total_patch_bytes: number };
type VersionsDTO = { path: string; current: { exists: boolean; size?: number; modified_at?: string }; items: Array<{ checkpoint_id: string; short_id: string; created_at: string; reason: string; exists: boolean; size?: number }> };

const checkpoint = (item: CheckpointDTO): Checkpoint => ({
  id: item.id, shortId: item.short_id, createdAt: item.created_at, reason: item.reason,
  trigger: item.trigger, filesChanged: item.files_changed, insertions: item.insertions, deletions: item.deletions,
});

export const checkpointsApi = {
  async status(agentId: string): Promise<CheckpointStatus> {
    const value = await request<StatusDTO>(`${root(agentId)}/status`);
    return { agentId: value.agent_id, enabled: value.enabled, available: value.available, unavailableReason: value.unavailable_reason, checkpointCount: value.checkpoint_count, retainedBytes: value.retained_bytes, maxSnapshots: value.max_snapshots, maxFileSizeBytes: value.max_file_size_bytes };
  },
  async list(agentId: string): Promise<Checkpoint[]> {
    const value = await request<{ items: CheckpointDTO[] }>(root(agentId));
    return value.items.map(checkpoint);
  },
  async diff(agentId: string, id: string): Promise<CheckpointDiff> {
    const value = await request<DiffDTO>(`${root(agentId)}/${encodeURIComponent(id)}/diff`);
    return { checkpointId: value.checkpoint_id, shortId: value.short_id, files: value.files, patch: value.patch, truncated: value.truncated, totalPatchBytes: value.total_patch_bytes };
  },
  async versions(agentId: string, path: string): Promise<{ path: string; current: VersionsDTO['current']; items: FileVersion[] }> {
    const value = await request<VersionsDTO>(`${root(agentId)}/file-versions?path=${encodeURIComponent(path)}`);
    return { path: value.path, current: value.current, items: value.items.map((item) => ({ checkpointId: item.checkpoint_id, shortId: item.short_id, createdAt: item.created_at, reason: item.reason, exists: item.exists, size: item.size })) };
  },
  restore: (agentId: string, id: string, path?: string) => request(`${root(agentId)}/${encodeURIComponent(id)}/restore`, { method: 'POST', body: JSON.stringify(path ? { path } : {}) }),
};
