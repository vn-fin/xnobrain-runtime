import { useTranslation } from 'react-i18next';
import { ArrowUp, Clock, Cloud, Code2, Cpu, Gauge, HardDrive, Laptop, Plus, RefreshCw, Server, X } from 'lucide-react';
import { StatCard, UsageBar, formatBytes, formatUptime } from './common';
import type { SandboxData } from '../types';
import type { AsyncStatus } from '../types';
import { AsyncState } from './AsyncState';

export function SandboxView({
  data,
  provisioned,
  setupRunning,
  setupProgress,
  status,
  error,
  onCreate,
  onRefresh,
  onClose,
}: {
  data: SandboxData | null;
  provisioned: boolean;
  status: AsyncStatus;
  error: string;
  setupRunning: boolean;
  setupProgress: number;
  onCreate: () => void;
  onRefresh: () => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  if (status === 'loading' && !setupRunning) return <AsyncState status="loading" />;
  if (status === 'error' && !setupRunning) return <AsyncState status="error" error={error} onRetry={onRefresh} />;
  if (!provisioned) {
    return (
      <div className="sandbox-view sandbox-empty-view">
        <header className="sbx-topbar">
          <div className="sbx-title">
            <Server size={18} />
            <div>
              <strong>{t('sandbox.title')}</strong>
              <span className="sbx-vmid">{setupRunning ? t('sandbox.provisioning') : t('sandbox.notProvisioned')}</span>
            </div>
          </div>
          <div className="sbx-actions">
            <button className="icon-button" title={t('common.close')} onClick={onClose}>
              <X size={17} />
            </button>
          </div>
        </header>
        <div className="sbx-create">
          {setupRunning ? (
            <div className="sbx-create-card sbx-setup-card">
              <h2>{t('sandbox.provisioningTitle')}</h2>
              <div className="sbx-progress-track">
                <div className="sbx-progress-fill" style={{ width: `${setupProgress}%` }} />
              </div>
              <span className="sbx-progress-pct">{setupProgress}%</span>
            </div>
          ) : (
            <div className="sbx-create-card">
              <span className="sbx-create-icon">
                <Server size={30} />
              </span>
              <h2>{t('sandbox.noSandbox')}</h2>
              <p>{t('sandbox.noSandboxDesc')}</p>
              <button className="sbx-create-btn" onClick={onCreate}>
                <Plus size={16} />
                {t('sandbox.create')}
              </button>
              <small>{t('sandbox.specs')}</small>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (!data) return <AsyncState status="error" error={error || 'Sandbox data is unavailable'} onRetry={onRefresh} />;
  const { info, metrics, system, health } = data;

  return (
    <div className="sandbox-view">
      <header className="sbx-topbar">
        <div className="sbx-title">
          <Server size={18} />
          <div>
            <strong>{t('sandbox.title')}</strong>
            <span className="sbx-vmid">{info.vmId}</span>
          </div>
          <span className={health.healthy ? 'sbx-status ok' : 'sbx-status down'}>
            <span className="dot" />
            {info.status}
          </span>
        </div>
        <div className="sbx-actions">
          <button title={t('sandbox.refresh')} onClick={onRefresh}>
            <RefreshCw size={14} />
            {t('sandbox.refresh')}
          </button>
          <button className="icon-button" title={t('common.close')} onClick={onClose}>
            <X size={17} />
          </button>
        </div>
      </header>

      <div className="sbx-scroll">
        <div className="sbx-tiles">
          <div className="sbx-tile">
            <span className="sbx-tile-label"><Gauge size={13} /> CPU</span>
            <strong>{metrics.cpuPercent.toFixed(1)}%</strong>
            <small>{info.resources.cpus} vCPU</small>
          </div>
          <div className="sbx-tile">
            <span className="sbx-tile-label"><Server size={13} /> Memory</span>
            <strong>{formatBytes(metrics.memoryBytes)}</strong>
            <small>of {info.resources.memory}</small>
          </div>
          <div className="sbx-tile">
            <span className="sbx-tile-label"><HardDrive size={13} /> Disk</span>
            <strong>{formatBytes(metrics.diskUsageBytes)}</strong>
            <small>of {formatBytes(metrics.diskTotalBytes)}</small>
          </div>
          <div className="sbx-tile">
            <span className="sbx-tile-label"><Clock size={13} /> Uptime</span>
            <strong>{formatUptime(metrics.uptimeSeconds)}</strong>
            <small>{system.processes} processes</small>
          </div>
        </div>

        <div className="sbx-grid">
          <StatCard icon={Gauge} title="Resource usage">
            <UsageBar label="CPU" used={metrics.cpuPercent} total={100} />
            <UsageBar label="Memory" used={metrics.memoryBytes} total={metrics.memoryLimitBytes} unit="bytes" />
            <UsageBar label="Disk" used={metrics.diskUsageBytes} total={metrics.diskTotalBytes} unit="bytes" />
            <div className="sbx-kv-row">
              <div><span>Mem available</span><strong>{formatBytes(metrics.memoryAvailableBytes)}</strong></div>
              <div><span>Disk read</span><strong>{formatBytes(metrics.diskReadBytes)}</strong></div>
              <div><span>Disk write</span><strong>{formatBytes(metrics.diskWriteBytes)}</strong></div>
            </div>
          </StatCard>

          <StatCard icon={Cloud} title="Network I/O">
            <div className="sbx-net">
              <div className="sbx-net-item">
                <ArrowUp size={14} className="rx" style={{ transform: 'rotate(180deg)' }} />
                <div>
                  <small>Received (Rx)</small>
                  <strong>{formatBytes(metrics.netRxBytes)}</strong>
                </div>
              </div>
              <div className="sbx-net-item">
                <ArrowUp size={14} className="tx" />
                <div>
                  <small>Transmitted (Tx)</small>
                  <strong>{formatBytes(metrics.netTxBytes)}</strong>
                </div>
              </div>
            </div>
            <div className="sbx-kv-row">
              <div><span>vCPU time</span><strong>{(metrics.vcpuTimeNs / 1e9).toFixed(0)}s</strong></div>
              <div><span>System CPU</span><strong>{system.cpuPercent.toFixed(1)}%</strong></div>
            </div>
          </StatCard>

          <StatCard icon={Server} title="Sandbox info">
            <dl className="sbx-dl">
              <div><dt>Type</dt><dd>{info.type}</dd></div>
              <div><dt>Image</dt><dd>{info.image}</dd></div>
              <div><dt>IPv4</dt><dd>{info.ipv4}</dd></div>
              <div><dt>IPv6</dt><dd>{info.ipv6}</dd></div>
              <div><dt>Gateway</dt><dd>:{info.gateway.port} · {info.gateway.healthy ? 'healthy' : 'down'}</dd></div>
              <div><dt>Created</dt><dd>{new Date(info.createdAt).toLocaleString()}</dd></div>
              <div><dt>vCPU / Mem</dt><dd>{info.resources.cpus} · {info.resources.memory}</dd></div>
              <div><dt>Root size</dt><dd>{info.resources.rootSize}</dd></div>
            </dl>
          </StatCard>

          <StatCard icon={Laptop} title="Operating system">
            <dl className="sbx-dl">
              <div><dt>Hostname</dt><dd>{system.os.hostname}</dd></div>
              <div><dt>OS</dt><dd>{system.os.os} {system.os.osVersion}</dd></div>
              <div><dt>Kernel</dt><dd>{system.os.kernelVersion}</dd></div>
              <div><dt>FQDN</dt><dd>{system.os.fqdn}</dd></div>
            </dl>
          </StatCard>

          <StatCard icon={HardDrive} title="Storage">
            <UsageBar label={`Pool · ${system.storage.pool}`} used={system.storage.poolUsedBytes} total={system.storage.poolTotalBytes} unit="bytes" />
            <UsageBar label={`Volume · ${system.storage.volumeName} (${system.storage.volumeType})`} used={system.storage.volumeUsedBytes} total={system.storage.volumeTotalBytes} unit="bytes" />
          </StatCard>

          <StatCard icon={Cloud} title="Health">
            <div className="sbx-health">
              <span className={health.healthy ? 'sbx-health-pill ok' : 'sbx-health-pill down'}>
                {health.healthy ? 'Healthy' : 'Unhealthy'}
              </span>
              <span className="sbx-health-code">HTTP {health.statusCode}</span>
            </div>
            <dl className="sbx-dl">
              <div><dt>Endpoint</dt><dd>{health.endpoint}</dd></div>
            </dl>
          </StatCard>

          <StatCard icon={Cpu} title="Top processes">
            <div className="sbx-proc">
              <div className="sbx-proc-head">
                <span>PID</span><span>Command</span><span>CPU</span><span>Mem</span><span>RSS</span>
              </div>
              {system.topProcesses.map((p) => (
                <div className="sbx-proc-row" key={p.pid}>
                  <span>{p.pid}</span>
                  <span className="sbx-proc-cmd">{p.command}</span>
                  <span>{p.cpuPercent.toFixed(1)}%</span>
                  <span>{p.memoryPercent.toFixed(1)}%</span>
                  <span>{formatBytes(p.rssKiB * 1024)}</span>
                </div>
              ))}
            </div>
          </StatCard>
        </div>

        <div className="sbx-footnote">
          <Code2 size={14} />
          <span>Mapped from /sandboxes/v1/me/sandboxes: info, stats, metrics, health and setup.</span>
        </div>
      </div>
    </div>
  );
}
