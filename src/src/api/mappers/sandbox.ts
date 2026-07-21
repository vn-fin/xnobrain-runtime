import type { SandboxDetailDTO } from '../contracts/sandboxes';
import type { SandboxData } from '../../types';

export function mapSandboxData(detail: SandboxDetailDTO): SandboxData {
  const info = detail.info ?? {};
  const metrics = detail.metrics ?? {};
  const system = detail.system ?? {};
  const health = detail.health ?? {};
  return {
    info: {
      vmId: info.vmId ?? info.id ?? '',
      status: info.status ?? 'unknown',
      type: info.type ?? '',
      image: info.image ?? '',
      ipv4: info.ipv4 ?? '',
      ipv6: info.ipv6 ?? '',
      createdAt: info.createdAt ?? info.created_at ?? '',
      gateway: { healthy: info.gateway?.healthy ?? false, port: info.gateway?.port ?? 0 },
      resources: {
        cpus: String(info.resources?.cpus ?? ''),
        memory: info.resources?.memory ?? '',
        rootSize: info.resources?.rootSize ?? info.resources?.root_size ?? '',
      },
    },
    metrics: {
      cpuPercent: metrics.cpuPercent ?? metrics.cpu_percent ?? 0,
      vcpuTimeNs: metrics.vcpuTimeNs ?? metrics.vcpu_time_ns ?? 0,
      uptimeSeconds: metrics.uptimeSeconds ?? metrics.uptime_seconds ?? 0,
      memoryBytes: metrics.memoryBytes ?? metrics.memory_bytes ?? 0,
      memoryLimitBytes: metrics.memoryLimitBytes ?? metrics.memory_limit_bytes ?? 0,
      memoryAvailableBytes: metrics.memoryAvailableBytes ?? metrics.memory_available_bytes ?? 0,
      diskUsageBytes: metrics.diskUsageBytes ?? metrics.disk_usage_bytes ?? 0,
      diskTotalBytes: metrics.diskTotalBytes ?? metrics.disk_total_bytes ?? 0,
      diskReadBytes: metrics.diskReadBytes ?? metrics.disk_read_bytes ?? 0,
      diskWriteBytes: metrics.diskWriteBytes ?? metrics.disk_write_bytes ?? 0,
      netRxBytes: metrics.netRxBytes ?? metrics.net_rx_bytes ?? 0,
      netTxBytes: metrics.netTxBytes ?? metrics.net_tx_bytes ?? 0,
    },
    system: {
      cpuPercent: system.cpuPercent ?? system.cpu_percent ?? 0,
      os: {
        hostname: system.os?.hostname ?? '',
        os: system.os?.os ?? '',
        osVersion: system.os?.osVersion ?? '',
        kernelVersion: system.os?.kernelVersion ?? '',
        fqdn: system.os?.fqdn ?? '',
      },
    },
    health: {
      healthy: health.healthy ?? false,
      statusCode: health.statusCode ?? health.status_code ?? 0,
      endpoint: health.endpoint ?? '',
    },
    updatedAt: detail.updatedAt ?? detail.updated_at ?? '',
  };
}
