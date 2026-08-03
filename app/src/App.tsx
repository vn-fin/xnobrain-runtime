import { useCallback, useEffect, useReducer, useRef } from 'react'
import { installerBridge } from './bridge/installer'
import { BrainIcon } from './components/Icons'
import { StepRail } from './installer/StepRail'
import { DockerScreen, EditionScreen, InstallingScreen, PortScreen, PreflightScreen, ReadyScreen, ReviewScreen } from './installer/screens'
import { initialInstallerState, installerReducer } from './state/installer'
import { DockerManager } from './management/DockerManager'

export function App() {
  const [state, dispatch] = useReducer(installerReducer, initialInstallerState)
  const inspectionStarted = useRef(false)
  const installStarted = useRef(false)
  const bootstrapInspection = useRef<ReturnType<typeof installerBridge.inspectRuntime> | undefined>(undefined)

  useEffect(() => {
    bootstrapInspection.current ??= installerBridge.inspectRuntime()
    let active = true
    void bootstrapInspection.current.then((overview) => {
      if (active && overview.state !== 'not_installed') {
        dispatch({ type: 'restored', overview })
      }
    }).catch(() => undefined)
    return () => { active = false }
  }, [])

  const runInspection = useCallback(async () => {
    dispatch({ type: 'inspection_started' })
    try {
      const inspection = await installerBridge.inspectSystem()
      dispatch({ type: 'inspection_finished', inspection })
      if (inspection.installed && inspection.healthy && inspection.selectedPort && inspection.webUrl) {
        dispatch({
          type: 'installed',
          result: {
            port: inspection.selectedPort,
            webUrl: inspection.webUrl,
            composePath: '',
          },
        })
      }
    } catch (error) {
      dispatch({ type: 'inspection_failed', message: safeMessage(error) })
    }
  }, [])

  useEffect(() => {
    if (state.step !== 'preflight' || inspectionStarted.current) return
    inspectionStarted.current = true
    void runInspection().finally(() => {
      inspectionStarted.current = false
    })
  }, [runInspection, state.step])

  const runPortCheck = async () => {
    dispatch({ type: 'port_check_started' })
    try {
      const result = await installerBridge.inspectPort(state.port)
      dispatch({ type: 'port_check_finished', available: result.available, message: result.message })
    } catch (error) {
      dispatch({ type: 'port_check_finished', available: false, message: safeMessage(error) })
    }
  }

  const runInstall = async () => {
    if (installStarted.current) return
    installStarted.current = true
    dispatch({ type: 'go', step: 'installing' })
    try {
      const result = await installerBridge.installWeb(
        { port: state.port },
        (progress) => dispatch({ type: 'progress', progress }),
      )
      dispatch({ type: 'installed', result })
    } catch (error) {
      dispatch({ type: 'install_failed', message: safeMessage(error) })
    } finally {
      installStarted.current = false
    }
  }

  const screenProps = {
    state,
    dispatch,
    bridge: installerBridge,
    runInspection,
    runPortCheck,
    runInstall,
  }

  return (
    <div className={`app-shell ${state.step === 'dashboard' ? 'dashboard-shell' : ''}`}>
      {state.step !== 'dashboard' && <header className="app-bar">
        <div className="brand"><span><BrainIcon /></span><strong>Brain4All</strong><em>{state.step === 'dashboard' ? 'Docker Manager' : 'Installer'}</em></div>
        <span className="app-edition">{state.step === 'dashboard' ? 'Docker Web · Local' : 'Docker Web · Preview'}</span>
      </header>}
      <div className={`app-layout ${state.step === 'dashboard' ? 'management-layout' : ''}`}>
        {state.step !== 'dashboard' && <StepRail current={state.step} />}
        <main>
          {state.step === 'edition' && <EditionScreen {...screenProps} />}
          {state.step === 'docker' && <DockerScreen {...screenProps} />}
          {state.step === 'preflight' && <PreflightScreen {...screenProps} />}
          {state.step === 'port' && <PortScreen {...screenProps} />}
          {state.step === 'review' && <ReviewScreen {...screenProps} />}
          {state.step === 'installing' && <InstallingScreen {...screenProps} />}
          {state.step === 'ready' && <ReadyScreen {...screenProps} />}
          {state.step === 'dashboard' && <DockerManager bridge={installerBridge} initialOverview={state.runtimeOverview} />}
        </main>
      </div>
    </div>
  )
}

function safeMessage(error: unknown): string {
  if (typeof error === 'string') return error
  if (error && typeof error === 'object' && 'message' in error && typeof error.message === 'string') {
    return error.message
  }
  return 'An unexpected installer error occurred. Please try again.'
}
