export type Platform = 'windows' | 'macos' | 'linux'

export type CheckStatus = 'pass' | 'warning' | 'fail'

export interface PreflightCheck {
  id: string
  label: string
  detail: string
  status: CheckStatus
  blocking: boolean
}

export interface SystemInspection {
  platform: Platform
  platformLabel: string
  architecture: string
  dockerInstalled: boolean
  dockerRunning: boolean
  composeAvailable: boolean
  dockerVersion?: string
  composeVersion?: string
  defaultPort: number
  installed: boolean
  healthy: boolean
  selectedPort?: number
  webUrl?: string
  checks: PreflightCheck[]
}

export interface PortInspection {
  port: number
  available: boolean
  message: string
}

export interface InstallRequest {
  port: number
}

export type InstallPhase =
  | 'validating'
  | 'preparing'
  | 'pulling'
  | 'creating'
  | 'starting'
  | 'health_check'
  | 'ready'

export interface InstallProgress {
  phase: InstallPhase
  percent: number
  title: string
  detail: string
}

export interface InstallResult {
  port: number
  webUrl: string
  composePath: string
}

export type RuntimeState = 'running' | 'stopped' | 'degraded' | 'not_installed'
export type ServiceState = 'running' | 'stopped' | 'unhealthy' | 'missing'
export type RuntimeAction = 'start' | 'stop' | 'restart'
export type LogService = 'all' | 'traefik' | 'frontend' | 'runtime'

export interface ServiceStatus {
  id: LogService
  name: string
  state: ServiceState
  detail: string
}

export interface RuntimeOverview {
  state: RuntimeState
  webUrl: string
  port: number
  dockerVersion?: string
  composeVersion?: string
  services: ServiceStatus[]
}

export interface RuntimeLogs {
  service: LogService
  lines: string[]
  truncated: boolean
}

export interface InstallerError {
  code: string
  message: string
  detail?: string
  retryable: boolean
}

export interface InstallerBridge {
  inspectSystem(): Promise<SystemInspection>
  inspectPort(port: number): Promise<PortInspection>
  installWeb(
    request: InstallRequest,
    onProgress: (progress: InstallProgress) => void,
  ): Promise<InstallResult>
  openWeb(): Promise<void>
  openDockerHelp(): Promise<void>
  inspectRuntime(): Promise<RuntimeOverview>
  controlRuntime(action: RuntimeAction): Promise<RuntimeOverview>
  readLogs(service: LogService): Promise<RuntimeLogs>
  toggleFullscreen(): Promise<boolean>
  resetInstallation(): Promise<void>
}
