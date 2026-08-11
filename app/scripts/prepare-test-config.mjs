import { copyFile, mkdir, chmod } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'

const output = resolve(process.argv[2] ?? 'generated/runtime-manifest.json')
const source = new URL('../config/runtime-manifest.test.json', import.meta.url)
await mkdir(dirname(output), { recursive: true })
await copyFile(source, output)
if (process.platform !== 'win32') await chmod(output, 0o600)
process.stdout.write(`${output}\n`)
