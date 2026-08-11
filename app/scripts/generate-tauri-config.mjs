import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

const output = resolve(process.argv[2] ?? 'generated/tauri.release.conf.json')
const base = JSON.parse(await readFile(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'))
const appName = process.env.XNOBRAIN_APP_NAME?.trim() || 'XNOBrain'
const appDescription = process.env.XNOBRAIN_APP_DESCRIPTION?.trim()
  || 'Install and run the private XNOBrain Docker Web workspace.'
const windowsCertificateThumbprint = process.env.XNOBRAIN_WINDOWS_CERTIFICATE_THUMBPRINT?.replace(/\s/g, '').toUpperCase() || ''
const windowsTimestampUrl = process.env.XNOBRAIN_WINDOWS_TIMESTAMP_URL?.trim() || 'http://timestamp.digicert.com'
if (appName.length > 48 || appName.length < 1) throw new Error('XNOBRAIN_APP_NAME must contain 1–48 characters')
if (appDescription.length > 180 || appDescription.length < 1) throw new Error('XNOBRAIN_APP_DESCRIPTION must contain 1–180 characters')
if (windowsCertificateThumbprint && !/^[A-F0-9]{40}$/.test(windowsCertificateThumbprint)) {
  throw new Error('XNOBRAIN_WINDOWS_CERTIFICATE_THUMBPRINT must contain exactly 40 hexadecimal characters')
}
if (!/^https?:\/\/[^/]/.test(windowsTimestampUrl)) {
  throw new Error('XNOBRAIN_WINDOWS_TIMESTAMP_URL must be an HTTP(S) URL')
}

const generated = {
  productName: appName,
  app: {
    windows: base.app.windows.map((window) => ({ ...window, title: appName })),
  },
  bundle: {
    shortDescription: appDescription,
    longDescription: appDescription,
    ...(windowsCertificateThumbprint ? {
      windows: {
        certificateThumbprint: windowsCertificateThumbprint,
        digestAlgorithm: 'sha256',
        timestampUrl: windowsTimestampUrl,
      },
    } : {}),
  },
}
await mkdir(dirname(output), { recursive: true })
await writeFile(output, `${JSON.stringify(generated, null, 2)}\n`, { mode: 0o600 })
process.stdout.write(`${output}\n`)
