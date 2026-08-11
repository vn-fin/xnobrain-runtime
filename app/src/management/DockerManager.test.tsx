import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { InstallerBridge, RuntimeOverview } from '../bridge/types'
import { DockerManager } from './DockerManager'

const overview: RuntimeOverview = {
  state: 'running',
  webUrl: 'http://127.0.0.1:5152',
  port: 5152,
  services: [],
}

describe('embedded Web application boundary', () => {
  it('enables browser-compatible popup, download, clipboard, and fullscreen behavior', () => {
    render(<DockerManager bridge={{} as InstallerBridge} initialOverview={overview} />)

    const frame = screen.getByTitle('XNOBrain Web application')
    expect(frame).toHaveAttribute('sandbox', expect.stringContaining('allow-popups'))
    expect(frame).toHaveAttribute('sandbox', expect.stringContaining('allow-popups-to-escape-sandbox'))
    expect(frame).toHaveAttribute('sandbox', expect.stringContaining('allow-downloads'))
    expect(frame).toHaveAttribute('allow', expect.stringContaining('clipboard-read'))
    expect(frame).toHaveAttribute('allow', expect.stringContaining('fullscreen'))
  })
})
