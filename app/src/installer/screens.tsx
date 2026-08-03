import type { CSSProperties, Dispatch, ReactNode } from 'react'
import type { InstallerBridge, PreflightCheck } from '../bridge/types'
import { AlertIcon, ArrowIcon, CheckIcon, DesktopIcon, DockerIcon, ExternalIcon, GlobeIcon, RefreshIcon, ShieldIcon } from '../components/Icons'
import type { InstallerAction, InstallerState } from '../state/installer'

interface ScreenProps {
  state: InstallerState
  dispatch: Dispatch<InstallerAction>
  bridge: InstallerBridge
  runInspection: () => Promise<void>
  runPortCheck: () => Promise<void>
  runInstall: () => Promise<void>
}

export function EditionScreen({ state, dispatch }: ScreenProps) {
  const managed = state.edition === 'managed'
  return (
    <Screen title="Choose your Brain4All version" subtitle="Start with the Web version today. The fully managed desktop experience is coming next.">
      <div className="edition-grid">
        <ChoiceCard
          selected={state.edition === 'web'}
          onClick={() => dispatch({ type: 'select_edition', edition: 'web' })}
          icon={<GlobeIcon />}
          eyebrow="Available now"
          title="Web Version"
          description="Runs locally with Docker and opens securely in your browser."
          meta="Recommended · Windows, macOS, Linux"
        />
        <ChoiceCard
          selected={managed}
          onClick={() => dispatch({ type: 'select_edition', edition: 'managed' })}
          icon={<DesktopIcon />}
          eyebrow="Coming later"
          title="Full Managed App"
          description="A native application with integrated runtime management."
          meta="Not available in this release"
          muted
        />
      </div>
      {managed && (
        <InlineNotice tone="info" title="Full Managed App is not available yet">
          Choose Web Version to install Brain4All now. No partial managed runtime will be installed.
        </InlineNotice>
      )}
      <Actions>
        <span />
        <PrimaryButton
          disabled={!state.edition || managed}
          onClick={() => dispatch({ type: 'go', step: 'docker' })}
        >
          Continue <ArrowIcon />
        </PrimaryButton>
      </Actions>
    </Screen>
  )
}

export function DockerScreen({ state, dispatch }: ScreenProps) {
  return (
    <Screen title="Connect Docker" subtitle="Brain4All Web runs in verified containers, isolated from the rest of your computer.">
      <div className="docker-banner">
        <span className="docker-mark"><DockerIcon /></span>
        <div><strong>Docker Web runtime</strong><span>The installer never builds source code on your computer.</span></div>
      </div>
      <div className="choice-list">
        <ChoiceRow
          selected={state.dockerPath === 'existing'}
          onClick={() => dispatch({ type: 'select_docker_path', path: 'existing' })}
          title="Use existing Docker"
          description="Choose this if Docker Desktop or Docker Engine is already installed."
          badge="Fastest"
        />
        <ChoiceRow
          selected={state.dockerPath === 'install'}
          onClick={() => dispatch({ type: 'select_docker_path', path: 'install' })}
          title="Install Docker"
          description="We'll check your system and open the official Docker installation flow."
        />
      </div>
      <Actions>
        <BackButton onClick={() => dispatch({ type: 'go', step: 'edition' })} />
        <PrimaryButton
          disabled={!state.dockerPath}
          onClick={() => dispatch({ type: 'go', step: 'preflight' })}
        >
          Check my system <ArrowIcon />
        </PrimaryButton>
      </Actions>
    </Screen>
  )
}

export function PreflightScreen({ state, dispatch, bridge, runInspection }: ScreenProps) {
  const failed = state.inspection?.checks.some((check) => check.blocking && check.status === 'fail')
  return (
    <Screen title="System check" subtitle="Confirming this computer is ready for the Brain4All Web runtime.">
      {state.loadingInspection && <CheckingState />}
      {!state.loadingInspection && state.inspection && (
        <>
          <div className="system-summary">
            <div><span>Computer</span><strong>{state.inspection.platformLabel}</strong></div>
            <div><span>Architecture</span><strong>{state.inspection.architecture}</strong></div>
            <div><span>Runtime</span><strong>Docker Web</strong></div>
          </div>
          <div className="check-list">
            {state.inspection.checks.map((check) => <CheckRow check={check} key={check.id} />)}
          </div>
          {failed && (
            <InlineNotice tone="warning" title="Docker needs attention">
              Install and start Docker, then run the check again. Brain4All cannot accept Docker's license for you.
              <button className="text-action" onClick={() => void bridge.openDockerHelp()}>Open official Docker setup <ExternalIcon /></button>
            </InlineNotice>
          )}
        </>
      )}
      {state.error && <InlineNotice tone="error" title="System check failed">{state.error}</InlineNotice>}
      <Actions>
        <BackButton onClick={() => dispatch({ type: 'go', step: 'docker' })} />
        <div className="action-cluster">
          {!state.loadingInspection && state.inspection && <SecondaryButton onClick={() => void runInspection()}><RefreshIcon /> Check again</SecondaryButton>}
          <PrimaryButton disabled={state.loadingInspection || !state.inspection || Boolean(failed)} onClick={() => dispatch({ type: 'go', step: 'port' })}>
            Continue <ArrowIcon />
          </PrimaryButton>
        </div>
      </Actions>
    </Screen>
  )
}

export function PortScreen({ state, dispatch, runPortCheck }: ScreenProps) {
  const defaultPort = state.inspection?.defaultPort ?? 5152
  const validRange = Number.isInteger(state.port) && state.port >= 1024 && state.port <= 65535
  return (
    <Screen title="Choose Web access" subtitle="Brain4All will be available only on this computer through Traefik.">
      <div className="port-visual">
        <div className="port-node"><GlobeIcon /><span>Your browser</span></div>
        <div className="port-line"><span>127.0.0.1</span></div>
        <div className="port-node accent"><ShieldIcon /><span>Traefik :{state.port || '—'}</span></div>
        <div className="port-line internal"><span>Private network</span></div>
        <div className="port-node"><DockerIcon /><span>Brain4All</span></div>
      </div>
      <fieldset className="port-options">
        <legend>Host port</legend>
        <label className={state.portMode === 'default' ? 'selected' : ''}>
          <input type="radio" name="port-mode" checked={state.portMode === 'default'} onChange={() => dispatch({ type: 'port_mode', mode: 'default', defaultPort })} />
          <span><strong>Default port</strong><small>Recommended for most users</small></span>
          <code>{defaultPort}</code>
        </label>
        <label className={state.portMode === 'custom' ? 'selected' : ''}>
          <input type="radio" name="port-mode" checked={state.portMode === 'custom'} onChange={() => dispatch({ type: 'port_mode', mode: 'custom', defaultPort })} />
          <span><strong>Custom port</strong><small>Use an available port from 1024–65535</small></span>
          <input
            aria-label="Custom port"
            className="port-input"
            type="number"
            min="1024"
            max="65535"
            disabled={state.portMode !== 'custom'}
            value={state.port}
            onChange={(event) => dispatch({ type: 'port_changed', port: Number(event.target.value) })}
          />
        </label>
      </fieldset>
      <div className={`port-status ${state.portAvailable === true ? 'pass' : state.portAvailable === false ? 'fail' : ''}`} aria-live="polite">
        {state.checkingPort ? <span className="mini-spinner" /> : state.portAvailable ? <CheckIcon /> : state.portAvailable === false ? <AlertIcon /> : <ShieldIcon />}
        <span>{state.checkingPort ? 'Checking this port…' : state.portMessage || 'The address is always fixed to 127.0.0.1 and is never exposed to your network.'}</span>
      </div>
      <Actions>
        <BackButton onClick={() => dispatch({ type: 'go', step: 'preflight' })} />
        <div className="action-cluster">
          <SecondaryButton disabled={!validRange || state.checkingPort} onClick={() => void runPortCheck()}>Check port</SecondaryButton>
          <PrimaryButton disabled={!validRange || state.portAvailable !== true} onClick={() => dispatch({ type: 'go', step: 'review' })}>Review <ArrowIcon /></PrimaryButton>
        </div>
      </Actions>
    </Screen>
  )
}

export function ReviewScreen({ state, dispatch, runInstall }: ScreenProps) {
  return (
    <Screen title="Ready to install" subtitle="Review what Brain4All will add to this computer.">
      <div className="review-card">
        <ReviewRow label="Version" value="Web Version (Docker)" />
        <ReviewRow label="Web address" value={`http://127.0.0.1:${state.port}`} mono />
        <ReviewRow label="Network access" value="This computer only (127.0.0.1)" />
        <ReviewRow label="Published service" value="Traefik only" />
        <ReviewRow label="User data" value="Persistent Docker volume" />
      </div>
      <InlineNotice tone="info" title="What happens next">
        The installer verifies runtime images, creates a private Docker network, starts Brain4All, and checks it through Traefik. It will not build source code or expose internal services.
      </InlineNotice>
      {state.error && <InlineNotice tone="error" title="Installation could not continue">{state.error}</InlineNotice>}
      <Actions>
        <BackButton onClick={() => dispatch({ type: 'go', step: 'port' })} />
        <PrimaryButton onClick={() => void runInstall()}>Install Brain4All <ArrowIcon /></PrimaryButton>
      </Actions>
    </Screen>
  )
}

export function InstallingScreen({ state }: ScreenProps) {
  const progress = state.progress ?? { percent: 2, title: 'Starting installation', detail: 'Preparing secure local state' }
  return (
    <Screen title="Installing Brain4All" subtitle="You can keep this window open while the Web runtime is prepared.">
      <div className="installation-progress">
        <div
          className="progress-orbit"
          style={{ '--progress': progress.percent } as CSSProperties}
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progress.percent}
        ><span>{progress.percent}%</span></div>
        <h2>{progress.title}</h2>
        <p>{progress.detail}</p>
        <div className="progress-track"><span style={{ width: `${progress.percent}%` }} /></div>
      </div>
      <div className="install-facts">
        <span><CheckIcon /> Only Traefik publishes a port</span>
        <span><ShieldIcon /> Bound to 127.0.0.1:{state.port}</span>
        <span><DockerIcon /> Persistent data is preserved</span>
      </div>
    </Screen>
  )
}

export function ReadyScreen({ state, bridge, dispatch }: ScreenProps) {
  const url = state.result?.webUrl ?? state.inspection?.webUrl ?? `http://127.0.0.1:${state.port}`
  return (
    <Screen title="Brain4All is ready" subtitle="Your private Web workspace is healthy and ready to open.">
      <div className="ready-hero">
        <span className="ready-check"><CheckIcon /></span>
        <div><span>Running securely at</span><strong>{url}</strong></div>
      </div>
      <div className="ready-grid">
        <div><ShieldIcon /><span><strong>Local only</strong>Traefik is bound to loopback</span></div>
        <div><DockerIcon /><span><strong>Runtime healthy</strong>Docker services passed checks</span></div>
        <div><GlobeIcon /><span><strong>Browser based</strong>Opens in your default browser</span></div>
      </div>
      <Actions>
        <SecondaryButton onClick={() => dispatch({ type: 'go', step: 'dashboard' })}>Manage Docker</SecondaryButton>
        <PrimaryButton onClick={() => void bridge.openWeb()}>Open in browser <ExternalIcon /></PrimaryButton>
      </Actions>
    </Screen>
  )
}

function Screen({ title, subtitle, children }: { title: string; subtitle: string; children: ReactNode }) {
  return <section className="screen"><header className="screen-header"><h1>{title}</h1><p>{subtitle}</p></header>{children}</section>
}

function ChoiceCard({ selected, onClick, icon, eyebrow, title, description, meta, muted = false }: { selected: boolean; onClick: () => void; icon: ReactNode; eyebrow: string; title: string; description: string; meta: string; muted?: boolean }) {
  return <button type="button" className={`choice-card ${selected ? 'selected' : ''} ${muted ? 'muted' : ''}`} onClick={onClick} aria-pressed={selected}><span className="choice-icon">{icon}</span><span className="eyebrow">{eyebrow}</span><strong>{title}</strong><p>{description}</p><small>{meta}</small><span className="selection-dot">{selected && <CheckIcon />}</span></button>
}

function ChoiceRow({ selected, onClick, title, description, badge }: { selected: boolean; onClick: () => void; title: string; description: string; badge?: string }) {
  return <button type="button" className={`choice-row ${selected ? 'selected' : ''}`} onClick={onClick} aria-pressed={selected}><span className="radio-dot" /><span><strong>{title}{badge && <em>{badge}</em>}</strong><small>{description}</small></span></button>
}

function CheckRow({ check }: { check: PreflightCheck }) {
  return <div className={`check-row ${check.status}`}><span className="check-state">{check.status === 'pass' ? <CheckIcon /> : <AlertIcon />}</span><span><strong>{check.label}</strong><small>{check.detail}</small></span><em>{check.status === 'pass' ? 'Ready' : check.status === 'warning' ? 'Review' : 'Required'}</em></div>
}

function CheckingState() {
  return <div className="checking-state"><span className="large-spinner" /><h2>Checking your computer</h2><p>Looking for Docker, Compose, resources, and supported platform features…</p></div>
}

function InlineNotice({ tone, title, children }: { tone: 'info' | 'warning' | 'error'; title: string; children: ReactNode }) {
  return <div className={`inline-notice ${tone}`}><span>{tone === 'warning' || tone === 'error' ? <AlertIcon /> : <ShieldIcon />}</span><div><strong>{title}</strong><p>{children}</p></div></div>
}

function ReviewRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return <div><span>{label}</span><strong className={mono ? 'mono' : ''}>{value}</strong></div>
}

function Actions({ children }: { children: ReactNode }) { return <footer className="screen-actions">{children}</footer> }
function PrimaryButton({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button type="button" className="button primary" {...props}>{children}</button> }
function SecondaryButton({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button type="button" className="button secondary" {...props}>{children}</button> }
function BackButton(props: React.ButtonHTMLAttributes<HTMLButtonElement>) { return <button type="button" className="button back" {...props}>Back</button> }
