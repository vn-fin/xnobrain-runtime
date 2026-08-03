import { mkdir, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

const output = resolve(process.argv[2] ?? 'generated/brain-ui-build.env')
const value = (name, fallback = '') => process.env[name]?.trim() || fallback
const httpsOrigin = (name) => {
  const origin = value(name)
  if (!/^https:\/\/[^\s/]+(?::[0-9]+)?$/.test(origin)) {
    throw new Error(`${name} must be an HTTPS origin without a path or trailing slash`)
  }
  return origin
}

const firebaseApiKey = value('XNOBRAIN_FIREBASE_API_KEY')
if (!/^AIza[0-9A-Za-z_-]{35}$/.test(firebaseApiKey)) {
  throw new Error('XNOBRAIN_FIREBASE_API_KEY must be the complete 39-character Firebase Web API key beginning with AIza')
}
const edition = value('XNOBRAIN_WEB_EDITION', 'enterprise')
if (!['pro', 'cloud', 'enterprise'].includes(edition)) {
  throw new Error('XNOBRAIN_WEB_EDITION must be pro, cloud, or enterprise for external authentication')
}

const lines = [
  `API_BASE_URL=${httpsOrigin('XNOBRAIN_API_BASE_URL')}`,
  `AUTH_BASE_URL=${httpsOrigin('XNOBRAIN_AUTH_BASE_URL')}`,
  `API_CONTROL_BASE_URL=${httpsOrigin('XNOBRAIN_BRAIN_CONTROL_BASE_URL')}`,
  `APP_EDITION=${edition}`,
  'AUTH_MODE=required',
  'AUTH_PROVIDER=xno-firebase',
  `FIREBASE_API_KEY=${firebaseApiKey}`,
]
await mkdir(dirname(output), { recursive: true })
await writeFile(output, `${lines.join('\n')}\n`, { mode: 0o600 })
process.stdout.write(`${output}\n`)
