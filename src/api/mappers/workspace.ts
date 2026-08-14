import type { WorkspaceEntryDTO } from '../contracts/agentGateway';
import type { WorkspaceEntry } from '../../types';

const BINARY_EXTS = [
  'zip', 'tar', 'gz', 'tgz', 'rar', '7z', 'bin', 'exe', 'dll', 'so', 'dylib',
  'mp3', 'wav', 'ogg', 'flac', 'mp4', 'mov', 'avi', 'mkv', 'webm',
  'woff', 'woff2', 'ttf', 'otf', 'eot', 'wasm', 'parquet', 'sqlite', 'db',
];

const CODE_BY_EXT: Record<string, NonNullable<WorkspaceEntry['language']>> = {
  py: 'python', pyw: 'python',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', mts: 'typescript', cts: 'typescript', tsx: 'typescript',
  vue: 'xml', svelte: 'xml', astro: 'xml',
  sh: 'shell', bash: 'shell', zsh: 'shell', fish: 'shell', ps1: 'shell', bat: 'shell', cmd: 'shell',
  css: 'css', scss: 'css', sass: 'css', less: 'css',
  sql: 'sql',
  yaml: 'yaml', yml: 'yaml',
  xml: 'xml', xsl: 'xml', xslt: 'xml', svg: 'image',
  toml: 'toml',
  ini: 'ini', cfg: 'ini', conf: 'ini', env: 'ini', properties: 'ini',
  go: 'go',
  rs: 'rust',
  java: 'java',
  c: 'c', h: 'c',
  cc: 'cpp', cpp: 'cpp', cxx: 'cpp', hpp: 'cpp', hxx: 'cpp',
  cs: 'csharp',
  rb: 'ruby',
  php: 'php',
  swift: 'swift',
  kt: 'kotlin', kts: 'kotlin',
  dart: 'dart',
  lua: 'lua',
  pl: 'perl', pm: 'perl',
  r: 'r',
  graphql: 'graphql', gql: 'graphql',
  diff: 'diff', patch: 'diff',
};

const TEXT_EXTS = new Set([
  'txt', 'text', 'log', 'rst', 'adoc',
  'csv', 'tsv',
  'gitignore', 'gitattributes', 'editorconfig',
]);

const SPECIAL_FILE_LANGUAGES: Record<string, NonNullable<WorkspaceEntry['language']>> = {
  dockerfile: 'dockerfile',
  containerfile: 'dockerfile',
  makefile: 'makefile',
  gnumakefile: 'makefile',
  rakefile: 'ruby',
  gemfile: 'ruby',
};

export function detectLanguage(path: string): WorkspaceEntry['language'] {
  const basename = path.split('/').filter(Boolean).pop()?.toLowerCase() ?? '';
  if (SPECIAL_FILE_LANGUAGES[basename]) return SPECIAL_FILE_LANGUAGES[basename];
  if (basename.startsWith('dockerfile.')) return 'dockerfile';
  if (basename.startsWith('makefile.')) return 'makefile';
  const ext = path.split('.').pop()?.toLowerCase();
  if (ext === 'ipynb') return 'notebook';
  if (ext === 'md' || ext === 'mdx') return 'markdown';
  if (ext === 'html' || ext === 'htm') return 'html';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif'].includes(ext ?? '')) return 'image';
  if (ext === 'pdf') return 'pdf';
  if (['doc', 'docx', 'odt', 'rtf'].includes(ext ?? '')) return 'document';
  if (['csv', 'xls', 'xlsx', 'xlsm', 'ods'].includes(ext ?? '')) return 'spreadsheet';
  if (['ppt', 'pptx', 'odp'].includes(ext ?? '')) return 'presentation';
  if (ext === 'json') return 'json';
  if (BINARY_EXTS.includes(ext ?? '')) return 'binary';
  if (ext && CODE_BY_EXT[ext]) return CODE_BY_EXT[ext];
  if (ext && TEXT_EXTS.has(ext)) return 'text';
  // An unknown extension is not evidence that a file is safe UTF-8 text.
  // Keep extensionless files editable, but route unfamiliar formats through
  // the bounded download fallback instead of silently decoding their bytes.
  return ext && ext !== basename ? 'binary' : 'text';
}

export function highlightLanguageForPath(path: string): string {
  const language = detectLanguage(path);
  const aliases: Partial<Record<NonNullable<WorkspaceEntry['language']>, string>> = {
    html: 'xml',
    shell: 'bash',
    dockerfile: 'dockerfile',
    makefile: 'makefile',
    toml: 'ini',
    css: 'css',
    text: 'plaintext',
  };
  return aliases[language ?? 'text'] ?? language ?? 'plaintext';
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
