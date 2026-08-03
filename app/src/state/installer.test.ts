import { describe, expect, it } from 'vitest'
import { initialInstallerState, installerReducer } from './installer'

describe('installerReducer', () => {
  it('requires an explicit edition and Docker path', () => {
    const edition = installerReducer(initialInstallerState, { type: 'select_edition', edition: 'web' })
    expect(edition.edition).toBe('web')
    expect(edition.dockerPath).toBeUndefined()
    const docker = installerReducer(edition, { type: 'select_docker_path', path: 'existing' })
    expect(docker.dockerPath).toBe('existing')
  })

  it('clears a previous port result when the port changes', () => {
    const checked = installerReducer(
      { ...initialInstallerState, portAvailable: true, portMessage: 'available' },
      { type: 'port_changed', port: 6200 },
    )
    expect(checked.port).toBe(6200)
    expect(checked.portAvailable).toBeUndefined()
    expect(checked.portMessage).toBeUndefined()
  })

  it('moves to ready only with an installation result', () => {
    const result = installerReducer(
      { ...initialInstallerState, step: 'installing' },
      { type: 'installed', result: { port: 5152, webUrl: 'http://127.0.0.1:5152', composePath: '/state/compose.yaml' } },
    )
    expect(result.step).toBe('ready')
    expect(result.result?.port).toBe(5152)
  })
})
