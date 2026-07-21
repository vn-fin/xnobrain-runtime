import { useTranslation } from 'react-i18next';
import { ArrowUp, Clock, Cloud, Gauge, HardDrive, Plus, RefreshCw, Server, X } from 'lucide-react';
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
              <strong>{t('nav.runtime', { defaultValue: 'Runtime' })}</strong>
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
            <strong>{t('nav.runtime', { defaultValue: 'Runtime' })}</strong>
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
            <small>live updates every second</small>
          </div>
        </div>

        <div className="sbx-grid">
          <StatCard icon={Gauge} title="Resource usage">
            <UsageBar label="CPU" used={metrics.cpuPercent} total={100} />
            <UsageBar label="Memory" used={metrics.memoryBytes} total={metrics.memoryLimitBytes} unit="bytes" />
            <UsageBar label="Disk" used={metrics.diskUsageBytes} total={metrics.diskTotalBytes} unit="bytes" />
            <div className="sbx-kv-row">
              <div><span>Mem available</span><strong>{formatBytes(metrics.memoryAvailableBytes)}</strong></div>
              <div><span>Last sample</span><strong>{data.updatedAt ? new Date(data.updatedAt).toLocaleTimeString() : '—'}</strong></div>
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
              <div><dt>API</dt><dd>:{info.gateway.port} · {info.gateway.healthy ? 'healthy' : 'down'}</dd></div>
              <div><dt>Started</dt><dd>{new Date(info.createdAt).toLocaleString()}</dd></div>
              <div><dt>vCPU / Mem</dt><dd>{info.resources.cpus} · {info.resources.memory}</dd></div>
              <div><dt>Root size</dt><dd>{info.resources.rootSize}</dd></div>
            </dl>
          </StatCard>

          <StatCard icon={Server} title="Operating system">
            <dl className="sbx-dl">
              <div><dt>Hostname</dt><dd>{system.os.hostname}</dd></div>
              <div><dt>OS</dt><dd>{system.os.os} {system.os.osVersion}</dd></div>
              <div><dt>Kernel</dt><dd>{system.os.kernelVersion}</dd></div>
              <div><dt>Architecture</dt><dd>{info.resources.cpus} vCPU · {info.resources.memory}</dd></div>
            </dl>
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

        </div>

        <div className="sbx-footnote">
          <Server size={14} />
          <span>Only essential runtime health and resource totals are collected. Process details are not exposed.</span>
        </div>
      </div>
    </div>
  );
}
