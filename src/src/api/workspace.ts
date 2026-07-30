import { request, requestMultipartWithProgress, requestRaw, type UploadProgress } from './client';
import type { WorkspaceFileDTO, WorkspaceListDTO } from './contracts/agentGateway';
import { mapWorkspaceEntry } from './mappers/workspace';

const root = (agentId: string) => `/api/brain/v1/agents-workspaces/${encodeURIComponent(agentId)}`;
const UPLOAD_CHUNK_BYTES = 768 * 1024;

const MIME_BY_EXT: Record<string, string> = {
  pdf: 'application/pdf',
  png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif',
  webp: 'image/webp', svg: 'image/svg+xml', bmp: 'image/bmp', ico: 'image/x-icon', avif: 'image/avif',
  doc: 'application/msword',
  docx: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  xls: 'application/vnd.ms-excel',
  xlsx: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  xlsm: 'application/vnd.ms-excel.sheet.macroEnabled.12',
  ppt: 'application/vnd.ms-powerpoint',
  pptx: 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  zip: 'application/zip', csv: 'text/csv', txt: 'text/plain',
  mp3: 'audio/mpeg', wav: 'audio/wav', mp4: 'video/mp4', webm: 'video/webm',
};

function mimeFor(path: string, fallback?: string): string {
  const ext = path.split('.').pop()?.toLowerCase();
  return (ext && MIME_BY_EXT[ext]) || fallback || 'application/octet-stream';
}

const GENERIC_MIME = new Set(['', 'application/octet-stream', 'binary/octet-stream', 'application/binary']);

/** Prefer a specific, previewable MIME type: fall back to the extension when the server sends a generic one. */
function preferMime(serverMime: string | undefined, path: string): string {
  const server = (serverMime ?? '').toLowerCase();
  if (server && !GENERIC_MIME.has(server)) return serverMime as string;
  return mimeFor(path, serverMime);
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

function blobOf(value: WorkspaceFileDTO | string, path: string): Blob {
  if (typeof value === 'string') return new Blob([value], { type: mimeFor(path, 'text/plain') });
  const mime = preferMime(value.mime_type, path);
  if (typeof value.content_base64 === 'string') {
    return new Blob([base64ToBytes(value.content_base64) as BlobPart], { type: mime });
  }
  if (typeof value.content === 'string') return new Blob([value.content], { type: mime });
  return new Blob([], { type: mime });
}

function newUploadId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function uploadProgress(loaded: number, total: number): UploadProgress {
  if (total <= 0) return { loaded, total, percent: 100 };
  const bounded = Math.min(total, loaded);
  return {
    loaded: bounded,
    total,
    percent: Math.min(100, Math.round((bounded / total) * 100)),
  };
}

async function uploadFileInChunks(
  agentId: string,
  path: string,
  file: File,
  onProgress?: (progress: UploadProgress) => void,
) {
  const uploadId = newUploadId();
  const totalChunks = Math.max(1, Math.ceil(file.size / UPLOAD_CHUNK_BYTES));
  let result: unknown;
  let confirmedBytes = 0;

  onProgress?.(uploadProgress(0, file.size));
  for (let chunkIndex = 0; chunkIndex < totalChunks; chunkIndex += 1) {
    const start = chunkIndex * UPLOAD_CHUNK_BYTES;
    const end = Math.min(file.size, start + UPLOAD_CHUNK_BYTES);
    const chunk = file.slice(start, end || start, file.type || 'application/octet-stream');
    const form = new FormData();
    form.set('path', path);
    form.set('upload_id', uploadId);
    form.set('file_name', file.name);
    form.set('chunk_index', String(chunkIndex));
    form.set('total_chunks', String(totalChunks));
    form.set('total_size', String(file.size));
    form.set('chunk', chunk, file.name);

    result = await requestMultipartWithProgress(`${root(agentId)}/upload/chunk`, form, (progress) => {
      const inFlightBytes = Math.min(chunk.size, progress.loaded);
      onProgress?.(uploadProgress(confirmedBytes + inFlightBytes, file.size));
    });
    confirmedBytes = end;
    onProgress?.(uploadProgress(confirmedBytes, file.size));
  }
  return result;
}

export const workspaceApi = {
  async list(agentId: string, path = '', signal?: AbortSignal) {
    const value = await request<WorkspaceListDTO>(`${root(agentId)}?path=${encodeURIComponent(path)}`, { signal });
    const entries = Array.isArray(value) ? value : value.entries ?? value.files ?? [];
    return entries.map(mapWorkspaceEntry);
  },
  async view(agentId: string, path: string, signal?: AbortSignal) {
    // Fetch the raw response so binary files (PDF, images, …) aren't corrupted by
    // text decoding. The gateway may return either the file bytes directly or a
    // JSON payload with base64/text content — handle both.
    const response = await requestRaw(`${root(agentId)}/file?path=${encodeURIComponent(path)}`, {
      signal,
      headers: { Accept: '*/*' },
    });
    const contentType = (response.headers.get('content-type') ?? '').toLowerCase();
    if (contentType.includes('json') || contentType.includes('text/plain')) {
      const text = await response.text();
      let parsed: unknown;
      try { parsed = JSON.parse(text); } catch { return blobOf(text, path); }
      // Unwrap a { success, data } envelope when present, then build the blob
      // from the DTO's content / content_base64 (or a raw string body).
      const dto = parsed && typeof parsed === 'object' && 'data' in (parsed as Record<string, unknown>)
        ? (parsed as { data: unknown }).data
        : parsed;
      return blobOf(dto as WorkspaceFileDTO | string, path);
    }
    const blob = await response.blob();
    // Re-type generic blobs so the browser previews them inline instead of downloading.
    if (GENERIC_MIME.has(blob.type.toLowerCase())) {
      return blob.slice(0, blob.size, mimeFor(path, blob.type || undefined));
    }
    return blob;
  },
  async size(agentId: string, path: string, signal?: AbortSignal) {
    const response = await requestRaw(`${root(agentId)}/file?path=${encodeURIComponent(path)}`, {
      signal,
      headers: { Accept: '*/*', Range: 'bytes=0-0' },
    });
    const contentRange = response.headers.get('content-range');
    const rangeTotal = contentRange?.match(/\/(\d+)$/)?.[1];
    const value = Number(rangeTotal ?? response.headers.get('content-length'));
    await response.body?.cancel();
    return Number.isFinite(value) && value >= 0 ? value : undefined;
  },
  async download(agentId: string, path: string, signal?: AbortSignal) {
    const response = await requestRaw(`${root(agentId)}/file?path=${encodeURIComponent(path)}`, {
      signal,
      headers: { Accept: 'application/octet-stream, */*' },
    });
    return response.blob();
  },
  async preview(agentId: string, path: string, signal?: AbortSignal) {
    const response = await requestRaw(`${root(agentId)}/preview?path=${encodeURIComponent(path)}`, {
      signal,
      headers: { Accept: 'application/pdf' },
    });
    const blob = await response.blob();
    return blob.type === 'application/pdf'
      ? blob
      : blob.slice(0, blob.size, 'application/pdf');
  },
  async workbook(agentId: string, path: string, signal?: AbortSignal) {
    const response = await requestRaw(`${root(agentId)}/workbook?path=${encodeURIComponent(path)}`, {
      signal,
      headers: { Accept: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' },
    });
    const blob = await response.blob();
    const xlsxMime = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
    return blob.type === xlsxMime ? blob : blob.slice(0, blob.size, xlsxMime);
  },
  async read(agentId: string, path: string, signal?: AbortSignal) {
    const response = await requestRaw(`${root(agentId)}/file?path=${encodeURIComponent(path)}`, {
      signal,
      headers: { Accept: 'text/plain, text/*;q=0.9, */*;q=0.1' },
    });
    if (!response.body) return response.text();
    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let content = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      content += decoder.decode(value, { stream: true });
    }
    return content + decoder.decode();
  },
  create: (agentId: string, input: { path: string; type: 'file' | 'directory'; content?: string }) =>
    request(`${root(agentId)}/create`, { method: 'POST', body: JSON.stringify(input) }),
  write: (agentId: string, path: string, content: string) =>
    request(`${root(agentId)}/write`, { method: 'POST', body: JSON.stringify({ path, content }) }),
  upload: (agentId: string, path: string, file: File, onProgress?: (progress: UploadProgress) => void) =>
    uploadFileInChunks(agentId, path, file, onProgress),
  remove: (agentId: string, path: string) =>
    request(`${root(agentId)}/delete`, { method: 'POST', body: JSON.stringify({ path }) }),
};
