import type { InstallProgress, InstallResult, SystemInspection } from '../bridge/types'

export type Edition = 'web' | 'managed'
export type DockerPath = 'existing' | 'install'
export type WizardStep =
  | 'edition'
  | 'docker'
  | 'preflight'
  | 'port'
  | 'review'
  | 'installing'
  | 'ready'
  | 'dashboard'

export interface InstallerState {
  step: WizardStep
  edition?: Edition
  dockerPath?: DockerPath
  inspection?: SystemInspection
  loadingInspection: boolean
  port: number
  portMode: 'default' | 'custom'
  portAvailable?: boolean
  portMessage?: string
  checkingPort: boolean
  progress?: InstallProgress
  result?: InstallResult
  error?: string
}

export const initialInstallerState: InstallerState = {
  step: 'edition',
  loadingInspection: false,
  port: 5152,
  portMode: 'default',
  checkingPort: false,
}

export type InstallerAction =
  | { type: 'select_edition'; edition: Edition }
  | { type: 'select_docker_path'; path: DockerPath }
  | { type: 'go'; step: WizardStep }
  | { type: 'inspection_started' }
  | { type: 'inspection_finished'; inspection: SystemInspection }
  | { type: 'inspection_failed'; message: string }
  | { type: 'port_mode'; mode: 'default' | 'custom'; defaultPort: number }
  | { type: 'port_changed'; port: number }
  | { type: 'port_check_started' }
  | { type: 'port_check_finished'; available: boolean; message: string }
  | { type: 'progress'; progress: InstallProgress }
  | { type: 'installed'; result: InstallResult }
  | { type: 'restored'; inspection: SystemInspection }
  | { type: 'install_failed'; message: string }
  | { type: 'restart' }

export function installerReducer(
  state: InstallerState,
  action: InstallerAction,
): InstallerState {
  switch (action.type) {
    case 'select_edition':
      return { ...state, edition: action.edition, error: undefined }
    case 'select_docker_path':
      return { ...state, dockerPath: action.path, error: undefined }
    case 'go':
      return { ...state, step: action.step, error: undefined }
    case 'inspection_started':
      return { ...state, loadingInspection: true, error: undefined }
    case 'inspection_finished':
      return {
        ...state,
        loadingInspection: false,
        inspection: action.inspection,
        port: state.portMode === 'default' ? action.inspection.defaultPort : state.port,
      }
    case 'inspection_failed':
      return { ...state, loadingInspection: false, error: action.message }
    case 'port_mode':
      return {
        ...state,
        portMode: action.mode,
        port: action.mode === 'default' ? action.defaultPort : state.port,
        portAvailable: undefined,
        portMessage: undefined,
      }
    case 'port_changed':
      return {
        ...state,
        port: action.port,
        portAvailable: undefined,
        portMessage: undefined,
      }
    case 'port_check_started':
      return { ...state, checkingPort: true, portAvailable: undefined, error: undefined }
    case 'port_check_finished':
      return {
        ...state,
        checkingPort: false,
        portAvailable: action.available,
        portMessage: action.message,
      }
    case 'progress':
      return { ...state, progress: action.progress, error: undefined }
    case 'installed':
      return { ...state, result: action.result, step: 'ready', error: undefined }
    case 'restored':
      return {
        ...state,
        inspection: action.inspection,
        port: action.inspection.selectedPort ?? action.inspection.defaultPort,
        result: action.inspection.selectedPort && action.inspection.webUrl ? {
          port: action.inspection.selectedPort,
          webUrl: action.inspection.webUrl,
          composePath: '',
        } : undefined,
        step: 'dashboard',
        error: undefined,
      }
    case 'install_failed':
      return { ...state, step: 'review', error: action.message }
    case 'restart':
      return initialInstallerState
  }
}
