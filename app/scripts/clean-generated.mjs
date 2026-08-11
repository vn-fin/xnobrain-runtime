import { rm } from 'node:fs/promises'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const generated = resolve(fileURLToPath(new URL('..', import.meta.url)), 'generated')
await rm(generated, { recursive: true, force: true })
process.stdout.write(`${generated}\n`)
