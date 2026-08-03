import { CheckIcon } from '../components/Icons'
import type { WizardStep } from '../state/installer'

const steps: Array<{ key: WizardStep; label: string }> = [
  { key: 'edition', label: 'Version' },
  { key: 'docker', label: 'Docker' },
  { key: 'preflight', label: 'System check' },
  { key: 'port', label: 'Web access' },
  { key: 'review', label: 'Review' },
  { key: 'installing', label: 'Install' },
  { key: 'ready', label: 'Ready' },
]

export function StepRail({ current }: { current: WizardStep }) {
  const currentIndex = steps.findIndex((step) => step.key === current)
  return (
    <aside className="step-rail" aria-label="Installation progress">
      <div className="rail-heading">Setup progress</div>
      <ol>
        {steps.map((step, index) => {
          const completed = index < currentIndex
          const active = index === currentIndex
          return (
            <li className={completed ? 'completed' : active ? 'active' : ''} key={step.key} aria-current={active ? 'step' : undefined}>
              <span className="step-marker">{completed ? <CheckIcon /> : index + 1}</span>
              <span>{step.label}</span>
            </li>
          )
        })}
      </ol>
      <div className="rail-note">
        <ShieldIconSmall />
        <p><strong>Private by default</strong><br />Only Traefik is available on your computer's loopback address.</p>
      </div>
    </aside>
  )
}

function ShieldIconSmall() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M12 3 5 6v5c0 4.8 2.8 8.2 7 10 4.2-1.8 7-5.2 7-10V6l-7-3Z" />
      <path d="m9 12 2 2 4-5" />
    </svg>
  )
}
