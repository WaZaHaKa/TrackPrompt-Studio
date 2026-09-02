import { spawn, spawnSync } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(here, '..', '..')
const repositoryRoot = path.resolve(frontendRoot, '..')
const backendRoot = path.join(repositoryRoot, 'backend')
const runtimeRoot = path.join(frontendRoot, '.e2e-data', 'mission-control')
const profileRoot = path.join(runtimeRoot, 'profiles', 'trip-to-andromeda')
const outputRoot = path.join(runtimeRoot, 'output')
const stateRoot = path.join(runtimeRoot, 'state')
const sourceProfile = path.join(
  repositoryRoot,
  'render-profiles',
  'trip-to-andromeda',
  'trip-to-andromeda-720p-hyper-optimized.json',
)
const copiedProfile = path.join(profileRoot, path.basename(sourceProfile))
const staticRoot = path.join(frontendRoot, 'dist')
const port = process.env.MC_E2E_PORT ?? '18005'
const python = process.env.E2E_PYTHON ?? path.join(backendRoot, '.venv', 'Scripts', 'python.exe')

const resolvedRuntime = path.resolve(runtimeRoot)
const allowedRuntime = path.resolve(frontendRoot, '.e2e-data') + path.sep
if (!resolvedRuntime.startsWith(allowedRuntime)) {
  throw new Error(`Refusing to prepare E2E state outside ${allowedRuntime}`)
}
fs.rmSync(resolvedRuntime, { recursive: true, force: true })
fs.mkdirSync(profileRoot, { recursive: true })
fs.mkdirSync(outputRoot, { recursive: true })
fs.mkdirSync(stateRoot, { recursive: true })

if (!fs.existsSync(sourceProfile)) {
  throw new Error(
    'The real 720p regression profile is unavailable. Run this local fixture E2E in the calibrated TrackPrompt checkout.',
  )
}
fs.copyFileSync(sourceProfile, copiedProfile)

const npmCli = process.env.npm_execpath ?? path.join(path.dirname(process.execPath), 'node_modules', 'npm', 'bin', 'npm-cli.js')
if (!fs.existsSync(npmCli)) {
  throw new Error('npm CLI was not found for the Mission Control E2E build.')
}
const build = spawnSync(process.execPath, [npmCli, 'run', 'build'], {
  cwd: frontendRoot,
  encoding: 'utf8',
  shell: false,
})
if (build.status !== 0) {
  process.stderr.write(build.stdout ?? '')
  process.stderr.write(build.stderr ?? '')
  process.exit(build.status ?? 1)
}

const descriptor = path.join(stateRoot, 'instance.json')
const child = spawn(
  python,
  [
    '-m', 'app.mission_control.server',
    '--host', '127.0.0.1',
    '--port', port,
    '--static-dir', staticRoot,
    '--instance-descriptor', descriptor,
  ],
  {
    cwd: backendRoot,
    env: {
      ...process.env,
      TRACKPROMPT_DATA_DIR: path.join(runtimeRoot, 'trackprompt-data'),
      TRACKPROMPT_MC_ALLOW_FAKE_RENDERER: 'true',
      TRACKPROMPT_MC_PROFILE_ROOT: path.join(runtimeRoot, 'profiles'),
      TRACKPROMPT_MC_OUTPUT_ROOT: outputRoot,
      TRACKPROMPT_MC_STATE_ROOT: stateRoot,
      TRACKPROMPT_MC_PICKER_RESULT: outputRoot,
    },
    stdio: 'inherit',
  },
)

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => child.kill(signal))
}
child.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal)
  else process.exit(code ?? 1)
})
