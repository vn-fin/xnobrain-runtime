export type DefaultSandboxDTO = {
  apiToken?: string;
  createdAt?: string;
  gateway?: { healthy?: boolean; port?: number };
  image?: string;
  info?: Record<string, unknown>;
  ipv4?: string;
  ipv6?: string;
  resources?: { cpus?: string | number; memory?: string; rootSize?: string };
  status?: string;
  type?: string;
  userId?: string;
  vmId?: string;
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
  topProcesses?: Array<{
    pid?: number;
    command?: string;
    cpuPercent?: number;
    memoryPercent?: number;
    rssKiB?: number;
    state?: string;
  }>;
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
};
