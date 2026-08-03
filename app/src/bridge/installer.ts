import { Channel, invoke } from '@tauri-apps/api/core'
import type {
  InstallProgress,
  InstallRequest,
  InstallResult,
  InstallerBridge,
  LogService,
  PortInspection,
  RuntimeOverview,
  RuntimeLogs,
  SystemInspection,
  DockerInstallProgress,
  DockerInstallResult,
} from './types'

declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown
  }
}

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds))

let demoFullscreen = false

const demoPlatform = (): SystemInspection['platform'] => {
  const requested = new URLSearchParams(window.location.search).get('platform')
  if (requested === 'windows' || requested === 'macos' || requested === 'linux') {
    return requested
  }
  return 'windows'
}

const platformLabel = (platform: SystemInspection['platform']) => {
  if (platform === 'windows') return 'Windows 11 Pro'
  if (platform === 'macos') return 'macOS 15'
  return 'Linux'
}

const createDemoBridge = (): InstallerBridge => ({
  async inspectSystem() {
    await wait(220)
    const params = new URLSearchParams(window.location.search)
    const missingDocker = params.get('scenario') === 'docker-missing'
    const platform = demoPlatform()
    const storedPort = Number(window.localStorage.getItem('xnobrain-demo-port')) || undefined
    return {
      platform,
      platformLabel: platformLabel(platform),
      architecture: platform === 'macos' ? 'arm64' : 'x86_64',
      dockerInstalled: !missingDocker,
      dockerRunning: !missingDocker,
      composeAvailable: !missingDocker,
      dockerVersion: missingDocker ? undefined : '29.6.2',
      composeVersion: missingDocker ? undefined : '5.3.1',
      defaultPort: 5152,
      installed: Boolean(storedPort),
      healthy: Boolean(storedPort),
      selectedPort: storedPort,
      webUrl: storedPort ? `http://127.0.0.1:${storedPort}` : undefined,
      checks: [
        {
          id: 'os',
          label: 'Supported operating system',
          detail: `${platformLabel(platform)} · ${platform === 'macos' ? 'arm64' : 'x86_64'}`,
          status: 'pass',
          blocking: false,
        },
        {
          id: 'docker',
          label: 'Docker Engine',
          detail: missingDocker ? 'Docker Desktop is not installed' : 'Docker 29.6.2 is running',
          status: missingDocker ? 'fail' : 'pass',
          blocking: missingDocker,
        },
        {
          id: 'compose',
          label: 'Docker Compose',
          detail: missingDocker ? 'Install Docker Desktop to continue' : 'Compose 5.3.1 is available',
          status: missingDocker ? 'fail' : 'pass',
          blocking: missingDocker,
        },
        {
          id: 'resources',
          label: 'System resources',
          detail: '12 GB memory · 64 GB available storage',
          status: 'pass',
          blocking: false,
        },
      ],
    }
  },
  async inspectPort(port: number): Promise<PortInspection> {
    await wait(180)
    const blocked = new URLSearchParams(window.location.search).get('scenario') === 'port-busy'
    return {
      port,
      available: !blocked && port !== 8080,
      message:
        blocked || port === 8080
          ? `Port ${port} is already used by another application.`
          : `Port ${port} is available on this computer.`,
    }
  },
  async installWeb(request, onProgress) {
    const stages: InstallProgress[] = [
      { phase: 'validating', percent: 8, title: 'Validating installation', detail: 'Checking Docker and port availability' },
      { phase: 'preparing', percent: 20, title: 'Preparing XNOBrain', detail: 'Creating secure local configuration' },
      { phase: 'pulling', percent: 48, title: 'Downloading runtime', detail: 'Pulling verified XNOBrain images' },
      { phase: 'creating', percent: 68, title: 'Creating services', detail: 'Configuring Traefik as the only local entrypoint' },
      { phase: 'starting', percent: 82, title: 'Starting XNOBrain', detail: 'Starting the Web runtime' },
      { phase: 'health_check', percent: 94, title: 'Checking health', detail: `Waiting at 127.0.0.1:${request.port}` },
      { phase: 'ready', percent: 100, title: 'XNOBrain is ready', detail: 'The Web version is healthy' },
    ]
    for (const stage of stages) {
      onProgress(stage)
      await wait(260)
    }
    window.localStorage.setItem('xnobrain-demo-port', String(request.port))
    window.localStorage.setItem('xnobrain-demo-state', 'running')
    return { port: request.port, webUrl: `http://127.0.0.1:${request.port}`, composePath: '/demo/xnobrain-web/compose.yaml' }
  },
  async openWeb() {
    return Promise.resolve()
  },
  async openDockerHelp() {
    window.open('https://docs.docker.com/desktop/', '_blank', 'noopener,noreferrer')
  },
  async installDocker(onProgress) {
    for (const update of [
      { percent: 12, title: 'Requesting permission', detail: 'Preparing the official Docker installer.' },
      { percent: 55, title: 'Downloading Docker', detail: 'Downloading the verified official installer.' },
      { percent: 100, title: 'Docker setup opened', detail: 'Complete Docker setup, then check the system again.' },
    ]) {
      onProgress(update)
      await wait(240)
    }
    return { restartRequired: false, message: 'Docker setup completed. Start Docker, then check the system again.' }
  },
  async inspectRuntime() {
    await wait(140)
    return demoRuntimeOverview()
  },
  async controlRuntime(action) {
    await wait(360)
    window.localStorage.setItem('xnobrain-demo-state', action === 'stop' ? 'stopped' : 'running')
    return demoRuntimeOverview()
  },
  async readLogs(service) {
    await wait(120)
    const allLines = [
      '2026-08-03T07:42:11Z  traefik   INFO  Configuration loaded from Docker provider',
      '2026-08-03T07:42:12Z  runtime   INFO  API server listening on private port 8642',
      '2026-08-03T07:42:12Z  frontend  INFO  Web interface ready on private port 8080',
      '2026-08-03T07:42:13Z  runtime   INFO  Health check passed',
      '2026-08-03T07:42:13Z  traefik   INFO  XNOBrain available through loopback ingress',
    ]
    return {
      service,
      lines: service === 'all' ? allLines : allLines.filter((line) => line.includes(`  ${service}`)),
      truncated: false,
    }
  },
  async toggleFullscreen() {
    demoFullscreen = !demoFullscreen
    return demoFullscreen
  },
  async resetInstallation() {
    window.localStorage.removeItem('xnobrain-demo-port')
    window.localStorage.removeItem('xnobrain-demo-state')
  },
})

function demoRuntimeOverview(): RuntimeOverview {
  const storedPort = Number(window.localStorage.getItem('xnobrain-demo-port')) || undefined
  const port = storedPort ?? 5152
  const running = window.localStorage.getItem('xnobrain-demo-state') !== 'stopped'
  return {
    state: storedPort ? (running ? 'running' : 'stopped') : 'not_installed',
    webUrl: `http://127.0.0.1:${port}`,
    port,
    dockerVersion: '29.6.2',
    composeVersion: '5.3.1',
    services: storedPort ? (['traefik', 'frontend', 'runtime'] as LogService[]).map((id) => ({
      id,
      name: id === 'traefik' ? 'Traefik ingress' : id === 'frontend' ? 'XNOBrain Web' : 'XNOBrain control',
      state: running ? 'running' : 'stopped',
      detail: id === 'traefik' ? `127.0.0.1:${port} → private :5152` : 'Docker-internal only',
    })) : [],
  }
}

const tauriBridge: InstallerBridge = {
  inspectSystem: () => invoke<SystemInspection>('inspect_system'),
  inspectPort: (port) => invoke<PortInspection>('inspect_port', { port }),
  installWeb: async (request, onProgress) => {
    const progress = new Channel<InstallProgress>()
    progress.onmessage = onProgress
    return invoke<InstallResult>('install_web', { request, progress })
  },
  openWeb: () => invoke<void>('open_web'),
  openDockerHelp: () => invoke<void>('open_docker_help'),
  installDocker: async (onProgress) => {
    const progress = new Channel<DockerInstallProgress>()
    progress.onmessage = onProgress
    return invoke<DockerInstallResult>('install_docker', { progress })
  },
  inspectRuntime: () => invoke<RuntimeOverview>('inspect_runtime'),
  controlRuntime: (action) => invoke<RuntimeOverview>('control_runtime', { action }),
  readLogs: (service) => invoke<RuntimeLogs>('read_logs', { service }),
  toggleFullscreen: () => invoke<boolean>('toggle_fullscreen'),
  resetInstallation: () => invoke<void>('reset_installation'),
}

export const installerBridge: InstallerBridge = window.__TAURI_INTERNALS__
  ? tauriBridge
  : createDemoBridge()
