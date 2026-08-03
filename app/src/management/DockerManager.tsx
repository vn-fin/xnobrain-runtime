import { useCallback, useEffect, useState } from 'react'
import type { InstallerBridge, LogService, RuntimeAction, RuntimeLogs, RuntimeOverview } from '../bridge/types'
import { DockerIcon, ExternalIcon, GlobeIcon, PlayIcon, RefreshIcon, ShieldIcon, SlidersIcon, StopIcon, TerminalIcon } from '../components/Icons'

type ManagerTab = 'application' | 'system' | 'logs'

export function DockerManager({ bridge }: { bridge: InstallerBridge }) {
  const [tab, setTab] = useState<ManagerTab>('application')
  const [overview, setOverview] = useState<RuntimeOverview>()
  const [loading, setLoading] = useState(true)
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

  useEffect(() => { void refresh() }, [refresh])

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

  return (
    <section className="manager-shell">
      <header className="manager-header">
        <div>
          <span className="manager-product"><DockerIcon /></span>
          <div><strong>Brain4All Web</strong><span>{overview?.webUrl ?? 'Local Docker runtime'}</span></div>
        </div>
        <StatusBadge state={overview?.state} loading={loading} />
      </header>
      <nav className="manager-tabs" aria-label="Docker Web management">
        <Tab active={tab === 'application'} onClick={() => setTab('application')} icon={<GlobeIcon />}>Application</Tab>
        <Tab active={tab === 'system'} onClick={() => setTab('system')} icon={<SlidersIcon />}>System</Tab>
        <Tab active={tab === 'logs'} onClick={() => setTab('logs')} icon={<TerminalIcon />}>Logs</Tab>
      </nav>
      <div className="manager-content">
        {error && <div className="manager-error" role="alert">{error}</div>}
        {tab === 'application' && <ApplicationTab overview={overview} busy={Boolean(activeAction)} bridge={bridge} control={control} refresh={refresh} />}
        {tab === 'system' && <SystemTab overview={overview} refresh={refresh} loading={loading} />}
        {tab === 'logs' && <LogsTab bridge={bridge} />}
      </div>
    </section>
  )
}

function ApplicationTab({ overview, busy, bridge, control, refresh }: {
  overview?: RuntimeOverview
  busy: boolean
  bridge: InstallerBridge
  control: (action: RuntimeAction) => Promise<void>
  refresh: () => Promise<void>
}) {
  const running = overview?.state === 'running'
  return (
    <div className="manager-panel application-panel">
      <div className="application-hero">
        <span className={`runtime-orb ${running ? 'running' : ''}`}><GlobeIcon /></span>
        <div>
          <span className="section-kicker">Application</span>
          <h1>{running ? 'Your workspace is ready' : 'Your workspace is stopped'}</h1>
          <p>{running ? 'Brain4All is running locally and ready in your normal browser.' : 'Start the Docker Web runtime to continue working.'}</p>
          <code>{overview?.webUrl ?? 'http://localhost:5152'}</code>
        </div>
      </div>
      <div className="manager-actions">
        <button className="button primary" disabled={!running || busy} onClick={() => void bridge.openWeb()}>Open in browser <ExternalIcon /></button>
        {running ? (
          <button className="button secondary danger-subtle" disabled={busy} onClick={() => void control('stop')}><StopIcon /> Stop</button>
        ) : (
          <button className="button secondary" disabled={busy} onClick={() => void control('start')}><PlayIcon /> Start</button>
        )}
        <button className="button secondary" disabled={!running || busy} onClick={() => void control('restart')}><RefreshIcon /> Restart</button>
        <button className="icon-button" aria-label="Refresh application status" disabled={busy} onClick={() => void refresh()}><RefreshIcon /></button>
      </div>
      <div className="application-facts">
        <Fact icon={<ShieldIcon />} title="Private by default" detail={`Traefik is the only ingress at 127.0.0.1:${overview?.port ?? 5152}.`} />
        <Fact icon={<DockerIcon />} title="Dedicated stack" detail="Controls affect only Brain4All-owned containers and preserve your data." />
        <Fact icon={<GlobeIcon />} title="Browser based" detail="The Web edition always opens in your system browser." />
      </div>
    </div>
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
  const load = useCallback(async () => {
    setLoading(true)
    setError(undefined)
    try { setLogs(await bridge.readLogs(service)) }
    catch (cause) { setError(messageOf(cause)) }
    finally { setLoading(false) }
  }, [bridge, service])
  useEffect(() => { void load() }, [load])

  return (
    <div className="manager-panel logs-panel">
      <div className="panel-heading"><div><span className="section-kicker">Logs</span><h1>Stack activity</h1><p>Recent, bounded output from Brain4All services. Secrets are redacted.</p></div><button className="button secondary" disabled={loading} onClick={() => void load()}><RefreshIcon /> Refresh</button></div>
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

function Tab({ active, onClick, icon, children }: { active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }) {
  return <button className={active ? 'active' : ''} onClick={onClick} aria-current={active ? 'page' : undefined}>{icon}{children}</button>
}
function StatusBadge({ state, loading }: { state?: RuntimeOverview['state']; loading: boolean }) {
  const value = loading && !state ? 'checking' : state?.replace('_', ' ') ?? 'unknown'
  return <span className={`runtime-status ${state ?? 'checking'}`}><i />{value}</span>
}
function Fact({ icon, title, detail }: { icon: React.ReactNode; title: string; detail: string }) { return <div>{icon}<span><strong>{title}</strong><small>{detail}</small></span></div> }
function Metric({ label, value }: { label: string; value: string }) { return <div><span>{label}</span><strong>{value}</strong></div> }
function messageOf(cause: unknown) { return cause instanceof Error ? cause.message : typeof cause === 'string' ? cause : 'The Docker operation could not be completed.' }
