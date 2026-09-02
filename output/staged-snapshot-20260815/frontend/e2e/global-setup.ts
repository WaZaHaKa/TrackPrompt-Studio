import { existsSync } from 'node:fs'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export default function globalSetup(): void {
  if (process.env.E2E_AUDIO_FIXTURE) return

  const here = path.dirname(fileURLToPath(import.meta.url))
  const root = path.resolve(here, '../..')
  const fixture = path.join(root, 'test-fixtures', '120bpm_click.wav')
  if (existsSync(fixture)) return

  const script = path.join(root, 'tools', 'generate_test_audio.py')
  if (!existsSync(script)) {
    throw new Error(`Synthetic fixture generator is missing at ${script}.`)
  }

  const candidates: Array<{ command: string; prefix: string[] }> = []
  if (process.env.PYTHON) candidates.push({ command: process.env.PYTHON, prefix: [] })
  const backendVenvPython = process.platform === 'win32'
    ? path.join(root, 'backend', '.venv', 'Scripts', 'python.exe')
    : path.join(root, 'backend', '.venv', 'bin', 'python')
  if (!process.env.PYTHON && existsSync(backendVenvPython)) {
    candidates.push({ command: backendVenvPython, prefix: [] })
  }
  candidates.push(
    { command: 'python', prefix: [] },
    process.platform === 'win32' ? { command: 'py', prefix: ['-3'] } : { command: 'python3', prefix: [] },
  )
  const failures: string[] = []

  for (const candidate of candidates) {
    const result = spawnSync(
      candidate.command,
      [...candidate.prefix, script, '--output-dir', path.join(root, 'test-fixtures')],
      { cwd: root, encoding: 'utf8', timeout: 120_000 },
    )
    if (result.status === 0 && existsSync(fixture)) return
    const detail = result.error?.message ?? (result.stderr.trim() || `exit ${String(result.status)}`)
    failures.push(`${candidate.command}: ${detail}`)
  }

  throw new Error(`Could not generate the synthetic E2E fixture. ${failures.join(' | ')}`)
}
