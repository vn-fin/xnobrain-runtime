import assert from 'node:assert/strict'
import { readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { spawnSync } from 'node:child_process'
import { mkdtemp } from 'node:fs/promises'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const appRoot = fileURLToPath(new URL('..', import.meta.url))
const digest = 'a'.repeat(64)

const run = (script, output, environment = {}, arguments_ = []) => spawnSync(
  process.execPath,
  [join(appRoot, 'scripts', script), output, ...arguments_],
  {
    cwd: appRoot,
    encoding: 'utf8',
    env: { ...process.env, ...environment },
  },
)

test('release manifest accepts immutable remote images', async (context) => {
  const directory = await mkdtemp(join(tmpdir(), 'xnobrain-config-'))
  context.after(() => rm(directory, { recursive: true, force: true }))
  const output = join(directory, 'runtime.json')
  const result = run('generate-runtime-manifest.mjs', output, {
    XNOBRAIN_IMAGE: `registry.xnoquant.io/xnobrain@sha256:${digest}`,
    XNOBRAIN_CONTROL_IMAGE: `registry.xnoquant.io/xnobrain-control@sha256:${digest}`,
  })
  assert.equal(result.status, 0, result.stderr)
  const manifest = JSON.parse(await readFile(output, 'utf8'))
  assert.equal(manifest.images.xnobrain.reference, `registry.xnoquant.io/xnobrain@sha256:${digest}`)
})

test('release manifest rejects a loopback registry unless explicitly isolated', async (context) => {
  const directory = await mkdtemp(join(tmpdir(), 'xnobrain-config-'))
  context.after(() => rm(directory, { recursive: true, force: true }))
  const environment = {
    XNOBRAIN_IMAGE: `127.0.0.1:5000/xnobrain@sha256:${digest}`,
    XNOBRAIN_CONTROL_IMAGE: `registry.xnoquant.io/xnobrain-control@sha256:${digest}`,
  }
  const rejected = run('generate-runtime-manifest.mjs', join(directory, 'rejected.json'), environment)
  assert.notEqual(rejected.status, 0)
  assert.match(rejected.stderr, /reachable from installed machines/)

  const accepted = run(
    'generate-runtime-manifest.mjs',
    join(directory, 'local.json'),
    environment,
    ['--allow-local'],
  )
  assert.equal(accepted.status, 0, accepted.stderr)
})

test('Tauri override validates and emits configurable signing metadata', async (context) => {
  const directory = await mkdtemp(join(tmpdir(), 'xnobrain-config-'))
  context.after(() => rm(directory, { recursive: true, force: true }))
  const output = join(directory, 'tauri.json')
  const thumbprint = 'A1'.repeat(20)
  const result = run('generate-tauri-config.mjs', output, {
    XNOBRAIN_APP_NAME: 'XNOBrain Enterprise',
    XNOBRAIN_APP_DESCRIPTION: 'Managed private workspace',
    XNOBRAIN_WINDOWS_CERTIFICATE_THUMBPRINT: thumbprint,
  })
  assert.equal(result.status, 0, result.stderr)
  const config = JSON.parse(await readFile(output, 'utf8'))
  assert.equal(config.productName, 'XNOBrain Enterprise')
  assert.equal(config.bundle.windows.certificateThumbprint, thumbprint)
  assert.equal(config.bundle.windows.digestAlgorithm, 'sha256')
})
