export type DefaultSandboxDTO = {
  apiToken?: string;
  createdAt?: string;
  gateway?: { healthy?: boolean; port?: number };
  image?: string;
  info?: Record<string, unknown>;
  ipv4?: string;
  ipv6?: string;
  status?: string;
  type?: string;
  userId?: string;
  vmId?: string;
  id?: string;
  created_at?: string;
  ipv4_address?: string;
  ipv6_address?: string;
  resources?: { cpus?: string | number; memory?: string; rootSize?: string; root_size?: string };
};

export type SandboxMetricsDTO = {
  cpuPercent?: number;
  diskReadBytes?: number;
  diskTotalBytes?: number;
  diskUsageBytes?: number;
  diskWriteBytes?: number;
  memoryAvailableBytes?: number;
  memoryBytes?: number;
  memoryLimitBytes?: number;
  netRxBytes?: number;
  netTxBytes?: number;
  uptimeSeconds?: number;
  vcpuTimeNs?: number;
  cpu_percent?: number;
  disk_read_bytes?: number;
  disk_total_bytes?: number;
  disk_usage_bytes?: number;
  disk_write_bytes?: number;
  memory_available_bytes?: number;
  memory_bytes?: number;
  memory_limit_bytes?: number;
  net_rx_bytes?: number;
  net_tx_bytes?: number;
  uptime_seconds?: number;
  vcpu_time_ns?: number;
};

export type SandboxProcessDTO = {
  pid?: number;
  command?: string;
  cpuPercent?: number;
  memoryPercent?: number;
  rssKiB?: number;
  cpu_percent?: number;
  memory_percent?: number;
  rss_kib?: number;
  state?: string;
};

export type SandboxSystemDTO = {
  cpuPercent?: number;
  processes?: number;
  os?: { hostname?: string; os?: string; osVersion?: string; kernelVersion?: string; fqdn?: string };
  storage?: {
    pool?: string;
    poolUsedBytes?: number;
    poolTotalBytes?: number;
    volumeName?: string;
    volumeType?: string;
    volumeUsedBytes?: number;
    volumeTotalBytes?: number;
  };
  topProcesses?: SandboxProcessDTO[];
  cpu_percent?: number;
  top_processes?: SandboxProcessDTO[];
};

export type DefaultSandboxStatsDTO = {
  metrics?: SandboxMetricsDTO;
  sandbox?: DefaultSandboxDTO;
  system?: SandboxSystemDTO;
};

export type SandboxHealthDTO = {
  endpoint?: string;
  healthy?: boolean;
  statusCode?: number;
  health?: Record<string, unknown>;
  sandbox?: DefaultSandboxDTO;
  status_code?: number;
};

export type SandboxDetailDTO = {
  info?: DefaultSandboxDTO;
  metrics?: SandboxMetricsDTO;
  system?: SandboxSystemDTO;
  health?: SandboxHealthDTO;
  updatedAt?: string;
  updated_at?: string;
};
