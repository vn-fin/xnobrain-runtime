import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement>

const Icon = ({ children, ...props }: IconProps) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>
    {children}
  </svg>
)

export const BrainIcon = (props: IconProps) => <Icon {...props}><path d="M9.5 4.2A3.2 3.2 0 0 0 4.8 7a3 3 0 0 0 .4 5.8A3.6 3.6 0 0 0 9.5 18V4.2Z"/><path d="M14.5 4.2A3.2 3.2 0 0 1 19.2 7a3 3 0 0 1-.4 5.8 3.6 3.6 0 0 1-4.3 5.2V4.2Z"/><path d="M9.5 8H7.8M14.5 8h1.7M9.5 13H7.6M14.5 13h1.9"/></Icon>
export const GlobeIcon = (props: IconProps) => <Icon {...props}><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></Icon>
export const DesktopIcon = (props: IconProps) => <Icon {...props}><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/></Icon>
export const DockerIcon = (props: IconProps) => <Icon {...props}><path d="M4 11h15.5c.1 4.8-3.2 8-8.1 8-4.1 0-6.8-2-7.4-5.6H2.5c-.3 0-.5-.3-.4-.6.3-.9 1-1.5 1.9-1.8Z"/><path d="M7 8h3v3H7zM10 5h3v3h-3zM10 8h3v3h-3zM13 8h3v3h-3zM16 8h3v3h-3zM20 9c1-.7 1.5-1.5 1.6-2.5 1.2.8 1.5 2 .9 3.3-.5 1-1.5 1.5-3 1.5"/></Icon>
export const CheckIcon = (props: IconProps) => <Icon {...props}><path d="m5 12 4 4L19 6"/></Icon>
export const ArrowIcon = (props: IconProps) => <Icon {...props}><path d="M5 12h14M13 6l6 6-6 6"/></Icon>
export const ChevronLeftIcon = (props: IconProps) => <Icon {...props}><path d="m15 18-6-6 6-6"/></Icon>
export const ShieldIcon = (props: IconProps) => <Icon {...props}><path d="M12 3 5 6v5c0 4.8 2.8 8.2 7 10 4.2-1.8 7-5.2 7-10V6l-7-3Z"/><path d="m9 12 2 2 4-5"/></Icon>
export const ExternalIcon = (props: IconProps) => <Icon {...props}><path d="M14 4h6v6M20 4l-9 9"/><path d="M19 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h5"/></Icon>
export const AlertIcon = (props: IconProps) => <Icon {...props}><path d="M12 4 3 20h18L12 4Z"/><path d="M12 10v4M12 17h.01"/></Icon>
export const RefreshIcon = (props: IconProps) => <Icon {...props}><path d="M20 7v5h-5M4 17v-5h5"/><path d="M18.5 9A7 7 0 0 0 6 6.5L4 9M5.5 15A7 7 0 0 0 18 17.5l2-2.5"/></Icon>
export const PlayIcon = (props: IconProps) => <Icon {...props}><path d="m8 5 11 7-11 7V5Z"/></Icon>
export const StopIcon = (props: IconProps) => <Icon {...props}><rect x="6" y="6" width="12" height="12" rx="1"/></Icon>
export const TerminalIcon = (props: IconProps) => <Icon {...props}><rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M12 15h5"/></Icon>
export const SlidersIcon = (props: IconProps) => <Icon {...props}><path d="M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6"/></Icon>
export const FullscreenIcon = (props: IconProps) => <Icon {...props}><path d="M8 3H3v5M16 3h5v5M8 21H3v-5M16 21h5v-5"/></Icon>
export const ExitFullscreenIcon = (props: IconProps) => <Icon {...props}><path d="M3 8h5V3M21 8h-5V3M3 16h5v5M21 16h-5v5"/></Icon>
