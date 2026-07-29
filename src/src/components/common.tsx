import type { ReactNode } from 'react';
import { File, FileText, Folder, FolderOpen, Server } from 'lucide-react';
import type { ProviderBrand, WorkspaceEntry } from '../types';

export function ExternalLinkIcon() {
  return <span className="external-mark">↗</span>;
}

export function CircleSpinner() {
  return <span className="circle-spinner" aria-hidden="true" />;
}

export function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / 1024 ** i;
  return `${value.toFixed(value >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function formatUptime(seconds: number): string {
  const d = Math.floor(seconds / 86400);
  const h = Math.floor((seconds % 86400) / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (d > 0) return `${d}d ${h}h ${m}m`;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

export function UsageBar({ label, used, total, unit, tone }: { label: string; used: number; total: number; unit?: string; tone?: string }) {
  const pct = total > 0 ? Math.min(100, (used / total) * 100) : 0;
  const barTone = tone ?? (pct >= 90 ? 'danger' : pct >= 70 ? 'warn' : 'ok');
  return (
    <div className="usage-bar">
      <div className="usage-bar-head">
        <span>{label}</span>
        <span className="usage-bar-value">
          {unit === 'bytes' ? `${formatBytes(used)} / ${formatBytes(total)}` : `${used.toFixed(1)}${unit ?? '%'}`}
          <em>{pct.toFixed(0)}%</em>
        </span>
      </div>
      <div className="usage-track">
        <div className={`usage-fill ${barTone}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function StatCard({ icon: Icon, title, children }: { icon: typeof Server; title: string; children: ReactNode }) {
  return (
    <section className="sbx-card">
      <div className="sbx-card-head">
        <Icon size={15} />
        <span>{title}</span>
      </div>
      {children}
    </section>
  );
}

export function ProviderBrandIcon({ brand }: { brand: ProviderBrand }) {
  return (
    <span className={`brand-icon ${brand}`}>
      <img src={`/providers/${brand}.svg`} alt="" aria-hidden="true" width={16} height={16} />
    </span>
  );
}

export function TreeIcon({ entry, size = 15 }: { entry: WorkspaceEntry; size?: number }) {
  if (entry.type === 'directory') {
    const Icon = entry.open ? FolderOpen : Folder;
    return <Icon className="tree-icon folder" size={size} />;
  }
  const badge = (className: string, label: string, ratio = 0.46) => (
    <span
      className={className}
      style={{ width: size, height: size, flexBasis: size, fontSize: Math.max(6, Math.round(size * ratio)), borderRadius: Math.max(2, Math.round(size * 0.13)) }}
    >
      {label}
    </span>
  );
  const extension = entry.name.split('.').pop()?.toLowerCase() ?? '';
  if (entry.language === 'python') return badge('file-badge python', 'py');
  if (entry.language === 'notebook') return badge('file-badge notebook', 'nb');
  if (entry.language === 'markdown') return badge('file-badge markdown', 'M', 0.6);
  if (entry.language === 'image') return badge('file-badge image', 'img', 0.38);
  if (entry.language === 'pdf') return badge('file-badge pdf', 'pdf', 0.34);
  if (entry.language === 'document') return badge('file-badge document', extension === 'odt' ? 'odt' : 'W', extension === 'odt' ? 0.3 : 0.6);
  if (entry.language === 'spreadsheet') return badge(
    'file-badge spreadsheet',
    extension === 'csv' ? 'csv' : extension === 'ods' ? 'ods' : 'X',
    extension === 'csv' || extension === 'ods' ? 0.3 : 0.6,
  );
  if (entry.language === 'presentation') return badge('file-badge presentation', extension === 'odp' ? 'odp' : 'P', extension === 'odp' ? 0.3 : 0.6);
  if (entry.language === 'html') return badge('file-badge html', '<>', 0.42);
  if (entry.language === 'json') return badge('file-badge json', '{}');
  if (['zip', 'tar', 'gz', 'tgz', 'rar', '7z'].includes(extension)) return badge('file-badge archive', 'zip', 0.32);
  if (['mp3', 'wav', 'ogg', 'flac'].includes(extension)) return badge('file-badge audio', '♪', 0.62);
  if (['mp4', 'mov', 'avi', 'mkv', 'webm'].includes(extension)) return badge('file-badge video', '▶', 0.52);
  if (entry.language === 'binary') return <FileText className="tree-icon binary" size={Math.round(size * 0.93)} />;
  return <File className="tree-icon text" size={Math.round(size * 0.93)} />;
}
