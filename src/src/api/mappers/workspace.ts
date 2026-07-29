import type { WorkspaceEntryDTO } from '../contracts/agentGateway';
import type { WorkspaceEntry } from '../../types';

const BINARY_EXTS = [
  'zip', 'tar', 'gz', 'tgz', 'rar', '7z', 'bin', 'exe', 'dll', 'so', 'dylib',
  'mp3', 'wav', 'ogg', 'flac', 'mp4', 'mov', 'avi', 'mkv', 'webm',
  'woff', 'woff2', 'ttf', 'otf', 'eot', 'wasm', 'parquet', 'sqlite', 'db',
];

export function detectLanguage(path: string): WorkspaceEntry['language'] {
  const ext = path.split('.').pop()?.toLowerCase();
  if (ext === 'py') return 'python';
  if (ext === 'ipynb') return 'notebook';
  if (ext === 'md' || ext === 'mdx') return 'markdown';
  if (ext === 'html' || ext === 'htm') return 'html';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif'].includes(ext ?? '')) return 'image';
  if (ext === 'pdf') return 'pdf';
  if (['doc', 'docx', 'odt', 'rtf'].includes(ext ?? '')) return 'document';
  if (['csv', 'xls', 'xlsx', 'ods'].includes(ext ?? '')) return 'spreadsheet';
  if (['ppt', 'pptx', 'odp'].includes(ext ?? '')) return 'presentation';
  if (ext === 'json') return 'json';
  if (BINARY_EXTS.includes(ext ?? '')) return 'binary';
  return 'text';
}

export function mapWorkspaceEntry(dto: WorkspaceEntryDTO): WorkspaceEntry {
  const path = dto.path ?? dto.name ?? '';
  const name = dto.name ?? path.split('/').filter(Boolean).pop() ?? path;
  const type = dto.type === 'directory' || dto.type === 'dir' || dto.is_dir ? 'directory' : 'file';
  return {
    name,
    path,
    type,
    level: Math.max(0, path.split('/').filter(Boolean).length - 1),
    ...(type === 'file' ? { language: detectLanguage(path) } : {}),
    size: String(dto.size ?? dto.size_bytes ?? ''),
    modified: dto.modified ?? dto.modified_at ?? dto.updated_at ?? '',
  };
}
