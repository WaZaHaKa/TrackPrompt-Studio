import { spawn, spawnSync } from 'node:child_process'
import { existsSync, rmSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const backend = path.resolve(here, '../../backend')
const ownsDataDirectory = !process.env.TRACKPROMPT_DATA_DIR
const dataDirectory = process.env.TRACKPROMPT_DATA_DIR ?? path.resolve(here, '../.e2e-data')
const venvPython = process.platform === 'win32'
  ? path.join(backend, '.venv', 'Scripts', 'python.exe')
  : path.join(backend, '.venv', 'bin', 'python')
const configuredPython = process.env.PYTHON
const candidates = configuredPython
  ? [{ command: configuredPython, prefix: [] }]
  : [
      ...(existsSync(venvPython) ? [{ command: venvPython, prefix: [] }] : []),
      ...(process.platform === 'win32'
        ? [{ command: 'python', prefix: [] }, { command: 'py', prefix: ['-3'] }]
        : [{ command: 'python', prefix: [] }, { command: 'python3', prefix: [] }]),
    ]

let child
let candidateIndex = 0

function launch() {
  const candidate = candidates[candidateIndex]
  if (!candidate) {
    process.stderr.write('No usable Python interpreter was found for the E2E backend. Set PYTHON to a Python 3.11+ executable.\n')
    process.exit(1)
  }
  child = spawn(
    candidate.command,
    [...candidate.prefix, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000'],
    {
      cwd: backend,
      env: {
        ...process.env,
        TRACKPROMPT_DATA_DIR: dataDirectory,
        MODEL_CACHE_DIR: process.env.MODEL_CACHE_DIR ?? path.join(dataDirectory, 'models'),
      },
      stdio: 'inherit',
    },
  )
  child.once('error', (error) => {
    if (error.code === 'ENOENT' && !configuredPython) {
      candidateIndex += 1
      launch()
      return
    }
    process.stderr.write(`Could not start the E2E backend: ${error.message}\n`)
    process.exit(1)
  })
  child.once('exit', (code, signal) => {
    if (signal) process.kill(process.pid, signal)
    else process.exit(code ?? 1)
  })
}

function stop(signal) {
  if (!child || child.killed) return
  if (process.platform === 'win32' && child.pid) {
    // A Windows venv launcher starts the base interpreter as a child process.
    // Terminate the complete tree so Playwright never leaves an API or analysis
    // worker behind after success, failure, or interruption.
    spawnSync('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { stdio: 'ignore' })
  } else {
    child.kill(signal)
  }
}

function cleanupOwnedData() {
  if (!ownsDataDirectory) return
  try {
    rmSync(dataDirectory, { recursive: true, force: true })
  } catch (error) {
    process.stderr.write(`Could not remove isolated E2E data: ${error.message}\n`)
  }
}

process.on('SIGTERM', () => stop('SIGTERM'))
process.on('SIGINT', () => stop('SIGINT'))
process.on('exit', () => {
  stop('SIGTERM')
  cleanupOwnedData()
})

launch()
