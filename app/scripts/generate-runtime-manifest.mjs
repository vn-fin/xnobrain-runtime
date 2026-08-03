import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import { dirname, resolve } from 'node:path'

const output = resolve(process.argv[2] ?? 'generated/runtime-manifest.json')
const allowExample = process.argv.includes('--allow-example')
const allowLocal = process.argv.includes('--allow-local')
const example = JSON.parse(
  await readFile(new URL('../config/runtime-manifest.example.json', import.meta.url), 'utf8'),
)

const value = (name, fallback) => {
  const configured = process.env[name]?.trim()
  return configured || fallback
}

const integer = (name, fallback, minimum, maximum) => {
  const parsed = Number(value(name, String(fallback)))
  if (!Number.isInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be an integer from ${minimum} through ${maximum}`)
  }
  return parsed
}

const digestReference = (name, fallback) => {
  const reference = value(name, fallback)
  if (!/^[-./:_a-zA-Z0-9]+@sha256:[a-f0-9]{64}$/.test(reference)) {
    if (allowExample && reference.includes('REPLACE_WITH_64_HEX_CHARACTERS')) return reference
    throw new Error(`${name} must be an immutable image reference ending in @sha256:<64 lowercase hex characters>`)
  }
  const repository = reference.split('@', 1)[0].toLowerCase()
  const firstSegment = repository.split('/', 1)[0]
  const localRegistry = firstSegment === 'localhost'
    || firstSegment.startsWith('localhost:')
    || firstSegment === '127.0.0.1'
    || firstSegment.startsWith('127.0.0.1:')
    || firstSegment === '[::1]'
    || firstSegment.startsWith('[::1]:')
  if (!allowLocal && localRegistry) {
    throw new Error(`${name} must use a registry reachable from installed machines; pass --allow-local only for isolated validation`)
  }
  if (!allowExample && repository.includes('example.invalid')) {
    throw new Error(`${name} still points to the example registry`)
  }
  return reference
}

const sha256 = (name, fallback) => {
  const digest = value(name, fallback)
  if (!/^[a-f0-9]{64}$/.test(digest)) {
    throw new Error(`${name} must contain exactly 64 lowercase hexadecimal characters`)
  }
  return digest
}

const httpsUrl = (name, fallback) => {
  const url = value(name, fallback)
  if (!url.startsWith('https://')) throw new Error(`${name} must be an HTTPS URL`)
  return url
}

const authBaseUrl = value('XNOBRAIN_AUTH_BASE_URL', example.web_build.auth_base_url)
if (!/^https:\/\/[^/].*[^/]$/.test(authBaseUrl)) {
  throw new Error('XNOBRAIN_AUTH_BASE_URL must be an HTTPS origin without a trailing slash')
}
const brainControlBaseUrl = value('XNOBRAIN_BRAIN_CONTROL_BASE_URL', example.web_build.brain_control_base_url)
if (!/^https:\/\/[^/].*[^/]$/.test(brainControlBaseUrl)) {
  throw new Error('XNOBRAIN_BRAIN_CONTROL_BASE_URL must be an HTTPS origin without a trailing slash')
}
const firebaseApiKey = value('XNOBRAIN_FIREBASE_API_KEY', '')
if (!/^AIza[0-9A-Za-z_-]{35}$/.test(firebaseApiKey)) {
  throw new Error('XNOBRAIN_FIREBASE_API_KEY must be the complete 39-character Firebase Web API key beginning with AIza')
}
const firebaseApiKeySha256 = createHash('sha256').update(firebaseApiKey).digest('hex')

const manifest = {
  ...example,
  release: value('XNOBRAIN_RELEASE', example.release),
  default_host_port: integer('XNOBRAIN_DEFAULT_PORT', example.default_host_port, 1024, 65535),
  web_build: {
    ...example.web_build,
    auth_base_url: authBaseUrl,
    brain_control_base_url: brainControlBaseUrl,
    firebase_api_key_sha256: firebaseApiKeySha256,
  },
  docker_installers: {
    windows_amd64: {
      url: httpsUrl('XNOBRAIN_DOCKER_WINDOWS_URL', example.docker_installers.windows_amd64.url),
      sha256: sha256('XNOBRAIN_DOCKER_WINDOWS_SHA256', example.docker_installers.windows_amd64.sha256),
    },
    macos_arm64: {
      url: httpsUrl('XNOBRAIN_DOCKER_MACOS_ARM64_URL', example.docker_installers.macos_arm64.url),
      sha256: sha256('XNOBRAIN_DOCKER_MACOS_ARM64_SHA256', example.docker_installers.macos_arm64.sha256),
    },
    macos_amd64: {
      url: httpsUrl('XNOBRAIN_DOCKER_MACOS_AMD64_URL', example.docker_installers.macos_amd64.url),
      sha256: sha256('XNOBRAIN_DOCKER_MACOS_AMD64_SHA256', example.docker_installers.macos_amd64.sha256),
    },
  },
  images: {
    traefik: {
      reference: digestReference('XNOBRAIN_TRAEFIK_IMAGE', example.images.traefik.reference),
    },
    brain: {
      reference: digestReference('XNOBRAIN_IMAGE', example.images.brain.reference),
    },
    runtime_api: {
      reference: digestReference('XNOBRAIN_RUNTIME_API_IMAGE', example.images.runtime_api.reference),
    },
  },
}

await mkdir(dirname(output), { recursive: true })
await writeFile(output, `${JSON.stringify(manifest, null, 2)}\n`, { mode: 0o600 })
process.stdout.write(`${output}\n`)
