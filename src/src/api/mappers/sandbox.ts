import type { DefaultSandboxDTO, SandboxHealthDTO, SandboxMetricsDTO, SandboxSystemDTO } from '../contracts/sandboxes';
import type { SandboxData } from '../../types';

export function mapSandboxData(
  info: DefaultSandboxDTO,
  metrics: SandboxMetricsDTO,
  system: SandboxSystemDTO,
  health: SandboxHealthDTO,
): SandboxData {
  return {
    info: {
      vmId: info.vmId ?? '',
      status: info.status ?? 'unknown',
      type: info.type ?? '',
      image: info.image ?? '',
      ipv4: info.ipv4 ?? '',
      ipv6: info.ipv6 ?? '',
      createdAt: info.createdAt ?? '',
      gateway: { healthy: info.gateway?.healthy ?? false, port: info.gateway?.port ?? 0 },
      resources: {
        cpus: String(info.resources?.cpus ?? ''),
        memory: info.resources?.memory ?? '',
        rootSize: info.resources?.rootSize ?? '',
      },
    },
    metrics: {
      cpuPercent: metrics.cpuPercent ?? 0,
      vcpuTimeNs: metrics.vcpuTimeNs ?? 0,
      uptimeSeconds: metrics.uptimeSeconds ?? 0,
      memoryBytes: metrics.memoryBytes ?? 0,
      memoryLimitBytes: metrics.memoryLimitBytes ?? 0,
      memoryAvailableBytes: metrics.memoryAvailableBytes ?? 0,
      diskUsageBytes: metrics.diskUsageBytes ?? 0,
      diskTotalBytes: metrics.diskTotalBytes ?? 0,
      diskReadBytes: metrics.diskReadBytes ?? 0,
      diskWriteBytes: metrics.diskWriteBytes ?? 0,
      netRxBytes: metrics.netRxBytes ?? 0,
      netTxBytes: metrics.netTxBytes ?? 0,
    },
    system: {
      cpuPercent: system.cpuPercent ?? 0,
      processes: system.processes ?? 0,
      os: {
        hostname: system.os?.hostname ?? '',
        os: system.os?.os ?? '',
        osVersion: system.os?.osVersion ?? '',
        kernelVersion: system.os?.kernelVersion ?? '',
        fqdn: system.os?.fqdn ?? '',
      },
      storage: {
        pool: system.storage?.pool ?? '',
        poolUsedBytes: system.storage?.poolUsedBytes ?? 0,
        poolTotalBytes: system.storage?.poolTotalBytes ?? 0,
        volumeName: system.storage?.volumeName ?? '',
        volumeType: system.storage?.volumeType ?? '',
        volumeUsedBytes: system.storage?.volumeUsedBytes ?? 0,
        volumeTotalBytes: system.storage?.volumeTotalBytes ?? 0,
      },
      topProcesses: (system.topProcesses ?? []).map((process) => ({
        pid: process.pid ?? 0,
        command: process.command ?? '',
        cpuPercent: process.cpuPercent ?? 0,
        memoryPercent: process.memoryPercent ?? 0,
        rssKiB: process.rssKiB ?? 0,
        state: process.state ?? '',
      })),
    },
    health: {
      healthy: health.healthy ?? false,
      statusCode: health.statusCode ?? 0,
      endpoint: health.endpoint ?? '',
    },
  };
}
