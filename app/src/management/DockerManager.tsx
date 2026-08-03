import { useCallback, useEffect, useRef, useState } from 'react'
import type { InstallerBridge, LogService, RuntimeAction, RuntimeLogs, RuntimeOverview } from '../bridge/types'
import { DockerIcon, ExternalIcon, GlobeIcon, PlayIcon, RefreshIcon, ShieldIcon, SlidersIcon, StopIcon, TerminalIcon } from '../components/Icons'

type ManagerPanel = 'system' | 'logs'

export function DockerManager({ bridge, initialOverview }: { bridge: InstallerBridge; initialOverview?: RuntimeOverview }) {
  const [panel, setPanel] = useState<ManagerPanel>()
  const [menuOpen, setMenuOpen] = useState(false)
  const [overview, setOverview] = useState<RuntimeOverview | undefined>(initialOverview)
  const [loading, setLoading] = useState(!initialOverview)
  const initialRequest = useRef<Promise<RuntimeOverview> | undefined>(undefined)
  const [activeAction, setActiveAction] = useState<RuntimeAction>()
  const [error, setError] = useState<string>()

  const refresh = useCallback(async () => {
    setLoading(true)
    setError(undefined)
    try {
      setOverview(await bridge.inspectRuntime())
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setLoading(false)
    }
  }, [bridge])

  useEffect(() => {
    if (initialOverview) return
    initialRequest.current ??= bridge.inspectRuntime()
    let active = true
    void initialRequest.current
      .then((result) => { if (active) setOverview(result) })
      .catch((cause) => { if (active) setError(messageOf(cause)) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [bridge, initialOverview])

  const control = async (action: RuntimeAction) => {
    if (activeAction) return
    setActiveAction(action)
    setError(undefined)
    try {
      setOverview(await bridge.controlRuntime(action))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setActiveAction(undefined)
    }
  }

  const running = overview?.state === 'running'
  const embeddedUrl = window.__TAURI_INTERNALS__ ? overview?.webUrl : '/embedded-preview.html'

  return (
    <section className="manager-shell">
      <header className="workspace-toolbar">
        <div className="workspace-menu-wrap">
          <button className="workspace-menu-button" aria-label="Docker Web settings" aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}>•••</button>
          {menuOpen && (
            <div className="workspace-menu" role="menu">
              <div className="workspace-menu-status"><StatusBadge state={overview?.state} loading={loading} /><span>{overview?.webUrl ?? 'Local Docker runtime'}</span></div>
              <button role="menuitem" onClick={() => { setPanel('system'); setMenuOpen(false) }}><SlidersIcon /><span><strong>System</strong><small>Services, health, and ingress</small></span></button>
              <button role="menuitem" onClick={() => { setPanel('logs'); setMenuOpen(false) }}><TerminalIcon /><span><strong>Logs</strong><small>Recent output for this stack</small></span></button>
              <button role="menuitem" disabled={!running} onClick={() => { void bridge.openWeb(); setMenuOpen(false) }}><ExternalIcon /><span><strong>Open in browser</strong><small>Use your default browser</small></span></button>
              <div className="workspace-menu-controls">
                {running ? <button disabled={Boolean(activeAction)} onClick={() => { setMenuOpen(false); void control('stop') }}><StopIcon /> Stop</button> : <button disabled={Boolean(activeAction)} onClick={() => { setMenuOpen(false); void control('start') }}><PlayIcon /> Start</button>}
                <button disabled={!running || Boolean(activeAction)} onClick={() => { setMenuOpen(false); void control('restart') }}><RefreshIcon /> Restart</button>
              </div>
            </div>
          )}
        </div>
        <span className="workspace-address"><ShieldIcon />{overview?.webUrl ?? 'Preparing local application…'}</span>
        <button className="toolbar-browser-button" disabled={!running} onClick={() => void bridge.openWeb()} aria-label="Open Brain4All in browser"><ExternalIcon /></button>
      </header>
      <div className="embedded-application">
        {error && <div className="embedded-error" role="alert">{error}<button onClick={() => void refresh()}>Retry</button></div>}
        {running && embeddedUrl ? (
          <iframe
            title="Brain4All Web application"
            src={embeddedUrl}
            sandbox="allow-same-origin allow-scripts allow-forms allow-downloads allow-modals allow-popups allow-popups-to-escape-sandbox"
            allow="clipboard-read; clipboard-write"
          />
        ) : (
          <div className="embedded-stopped">
            <span><GlobeIcon /></span>
            <h1>{loading ? 'Connecting to Brain4All' : 'Brain4All Web is stopped'}</h1>
            <p>{loading ? 'Checking the local Docker runtime…' : 'Start the dedicated stack to show the Web application here.'}</p>
            {!loading && <button className="button primary" disabled={Boolean(activeAction)} onClick={() => void control('start')}><PlayIcon /> Start Brain4All</button>}
          </div>
        )}
      </div>
      {panel && (
        <div className="manager-overlay" role="dialog" aria-modal="true" aria-label={panel === 'system' ? 'System' : 'Logs'}>
          <div className="manager-overlay-card">
            <header><div><span className="manager-product"><DockerIcon /></span><strong>Brain4All Docker Web</strong></div><button aria-label="Close settings" onClick={() => setPanel(undefined)}>×</button></header>
            <div className="manager-content">
              {panel === 'system' && <SystemTab overview={overview} refresh={refresh} loading={loading} />}
              {panel === 'logs' && <LogsTab bridge={bridge} />}
            </div>
          </div>
        </div>
      )}
    </section>
  )
}

function SystemTab({ overview, refresh, loading }: { overview?: RuntimeOverview; refresh: () => Promise<void>; loading: boolean }) {
  return (
    <div className="manager-panel">
      <div className="panel-heading"><div><span className="section-kicker">System</span><h1>Docker runtime</h1><p>Health and network details for the Brain4All stack only.</p></div><button className="button secondary" disabled={loading} onClick={() => void refresh()}><RefreshIcon /> Refresh</button></div>
      <div className="system-metrics">
        <Metric label="Docker Engine" value={overview?.dockerVersion ?? 'Unavailable'} />
        <Metric label="Docker Compose" value={overview?.composeVersion ?? 'Unavailable'} />
        <Metric label="Published ports" value={overview?.state === 'not_installed' ? 'None' : '1 · Traefik only'} />
        <Metric label="Data policy" value="Persistent volume" />
      </div>
      <div className="service-table" role="table" aria-label="Brain4All services">
        <div className="service-table-head" role="row"><span>Service</span><span>Access</span><span>Status</span></div>
        {overview?.services.map((service) => (
          <div className="service-row" role="row" key={service.id}>
            <span><i className={`service-dot ${service.state}`} /><strong>{service.name}</strong></span>
            <span>{service.detail}</span>
            <em className={service.state}>{service.state}</em>
          </div>
        ))}
      </div>
    </div>
  )
}

function LogsTab({ bridge }: { bridge: InstallerBridge }) {
  const [service, setService] = useState<LogService>('all')
  const [logs, setLogs] = useState<RuntimeLogs>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string>()
  const pending = useRef<{ service: LogService; request: ReturnType<InstallerBridge['readLogs']> } | undefined>(undefined)
  const load = useCallback(async (reusePending = false) => {
    setLoading(true)
    setError(undefined)
    try {
      if (!reusePending || pending.current?.service !== service) {
        pending.current = { service, request: bridge.readLogs(service) }
      }
      setLogs(await pending.current.request)
    }
    catch (cause) { setError(messageOf(cause)) }
    finally { setLoading(false) }
  }, [bridge, service])
  useEffect(() => { void load(true) }, [load])

  return (
    <div className="manager-panel logs-panel">
      <div className="panel-heading"><div><span className="section-kicker">Logs</span><h1>Stack activity</h1><p>Recent, bounded output from Brain4All services. Secrets are redacted.</p></div><button className="button secondary" disabled={loading} onClick={() => void load(false)}><RefreshIcon /> Refresh</button></div>
      <div className="log-filters" aria-label="Log service filter">
        {(['all', 'traefik', 'frontend', 'runtime'] as LogService[]).map((item) => <button key={item} className={service === item ? 'active' : ''} onClick={() => setService(item)}>{item === 'all' ? 'All services' : item}</button>)}
      </div>
      <div className="log-viewer" aria-live="polite">
        {loading && <span className="log-placeholder">Loading recent logs…</span>}
        {error && <span className="log-error">{error}</span>}
        {!loading && !error && logs?.lines.length === 0 && <span className="log-placeholder">No recent log entries for this service.</span>}
        {!loading && !error && logs?.lines.map((line, index) => <div key={`${index}-${line}`}><span>{String(index + 1).padStart(2, '0')}</span><code>{line}</code></div>)}
      </div>
      <p className="log-note"><ShieldIcon /> Output is limited to the Brain4All Compose project and the latest 250 lines.</p>
    </div>
  )
}

function StatusBadge({ state, loading }: { state?: RuntimeOverview['state']; loading: boolean }) {
  const value = loading && !state ? 'checking' : state?.replace('_', ' ') ?? 'unknown'
  return <span className={`runtime-status ${state ?? 'checking'}`}><i />{value}</span>
}
function Metric({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{value}</strong></div> }
function messageOf(cause: unknown) { return cause instanceof Error ? cause.message : typeof cause === 'string' ? cause : 'The Docker operation could not be completed.' }
