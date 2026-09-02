import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

const here = path.dirname(fileURLToPath(import.meta.url))
const fixturePath = process.env.E2E_AUDIO_FIXTURE
  ? path.resolve(process.env.E2E_AUDIO_FIXTURE)
  : path.resolve(here, '../../test-fixtures/120bpm_click.wav')

test('analyzes, corrects a section, composes a prompt, copies, and deletes', async ({ page, context }) => {
  test.setTimeout(180_000)
  expect(existsSync(fixturePath), `Synthetic E2E fixture missing at ${fixturePath}. Run tools/generate_test_audio.py first.`).toBe(true)

  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/?workspace=analysis')
  await expect(page.getByRole('heading', { name: 'Choose your track' })).toBeVisible()

  await page.getByLabel('Audio file').setInputFiles(fixturePath)
  await page.getByRole('checkbox', { name: /I have permission/i }).check()
  await page.getByRole('button', { name: 'Analyze track' }).click()

  const completed = page.getByText('Analysis complete')
  const failed = page.getByRole('heading', { name: 'Analysis needs attention' })
  await expect(completed.or(failed)).toBeVisible({ timeout: 150_000 })
  await expect(completed).toBeVisible()
  await expect(page.getByText('Tempo', { exact: true }).locator('..')).toContainText('BPM')
  await expect(page.getByRole('tab', { name: 'Timeline' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Blender Visualizer' })).toBeVisible()
  const presetSelect = page.getByLabel('Visualizer preset')
  await expect(presetSelect).toHaveValue('abstract-geometry')
  await presetSelect.selectOption('space-journey')
  await expect(page.getByRole('group', { name: 'Camera' })).toBeVisible()
  await expect(page.getByLabel('Camera distance')).toHaveValue('18')
  const configDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Download visualizer config' }).click()
  const downloadedConfig = await configDownload
  expect(downloadedConfig.suggestedFilename()).toBe('visualizer-config.resolved.json')
  const configPath = await downloadedConfig.path()
  expect(configPath).not.toBeNull()
  if (configPath === null) {
    throw new Error('Playwright did not retain the downloaded visualizer configuration.')
  }
  const configPayload = JSON.parse(await readFile(configPath, 'utf8')) as Record<string, unknown>
  expect(configPayload.schemaVersion).toBe('1.0.0')
  expect(configPayload.preset).toBe('space-journey')
  expect(configPayload.seed).toBe(84291)
  expect(configPayload.parameters).toMatchObject({
    cameraDistance: 18,
    palette: 'andromeda',
    ringOcclusion: 0.2,
  })
  expect(JSON.stringify(configPayload)).not.toContain('120bpm_click.wav')
  await expect(page.getByText('Space Journey configuration validated and downloaded.')).toBeVisible()
  const cueDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Export cue sheet' }).click()
  const downloadedCue = await cueDownload
  expect(downloadedCue.suggestedFilename()).toMatch(/^trackprompt-[0-9a-f-]+-visual-cues\.json$/)
  const cuePath = await downloadedCue.path()
  expect(cuePath).not.toBeNull()
  if (cuePath === null) {
    throw new Error('Playwright did not retain the downloaded cue sheet.')
  }
  const cuePayload = JSON.parse(await readFile(cuePath, 'utf8')) as Record<string, unknown>
  expect(cuePayload.schemaVersion).toBe('1.1.0')
  expect(JSON.stringify(cuePayload)).not.toContain('120bpm_click.wav')
  await expect(page.getByText(/Exported \d+ beats, \d+ onsets/)).toBeVisible()

  await page.getByRole('tab', { name: 'Timeline' }).click()
  await page.getByRole('button', { name: /Edit section/ }).first().click()
  await page.getByLabel('Section label').fill('verse')
  await page.getByRole('button', { name: 'Save section' }).click()
  await expect(page.getByRole('button', { name: 'Edit section verse' })).toBeVisible()

  await page.getByRole('tab', { name: 'Prompt' }).click()
  const editor = page.getByLabel('Editable primary prompt')
  if ((await editor.inputValue()).length === 0) {
    await page.getByRole('button', { name: 'Generate candidates', exact: true }).click()
  }
  await expect(editor).not.toHaveValue('')
  await page.getByRole('button', { name: 'Copy prompt' }).click()
  await expect(page.getByText('Prompt copied')).toBeVisible()

  await page.getByRole('button', { name: 'Delete analysis' }).click()
  await page.getByRole('button', { name: 'Delete everything' }).click()
  await expect(page.getByRole('heading', { name: 'Choose your track' })).toBeVisible()
  await expect(page.getByRole('status')).toContainText('temporary data deleted')
})

test('creates an archived catalogue, resumes through virtual segmentation, analyzes, and reports', async ({ page }) => {
  test.setTimeout(240_000)
  expect(existsSync(fixturePath), `Synthetic E2E fixture missing at ${fixturePath}.`).toBe(true)

  await page.goto('/?workspace=analysis')
  await page.getByRole('button', { name: 'Client catalogue' }).click()
  await expect(page.getByRole('heading', { name: /Client projects, long sets/ })).toBeVisible()

  const clientName = page.getByLabel('New client name')
  await clientName.fill('Synthetic E2E client')
  await clientName.locator('..').getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('listbox', { name: 'Clients' })).toContainText('Synthetic E2E client')

  await page.getByLabel('New project name').fill('Archived mastering project')
  await page.getByRole('button', { name: 'Create project' }).click()
  await expect(page.getByRole('listbox', { name: 'Projects' })).toContainText('Archived mastering project')

  const batchName = page.getByLabel('New batch name')
  await batchName.fill('Set A')
  await batchName.locator('..').getByRole('button', { name: 'Create' }).click()
  await expect(page.getByRole('listbox', { name: 'Batches' })).toContainText('Set A')

  await page.getByLabel('Bulk audio files').setInputFiles(fixturePath)
  await expect(page.getByText('1 items')).toBeVisible()
  await page.getByRole('button', { name: 'Upload queue' }).click()
  await expect(page.getByText('120bpm_click.wav', { exact: true }).last()).toBeVisible({ timeout: 60_000 })

  await page.getByRole('button', { name: 'Detect tracks' }).click()
  await expect(page.getByLabel('Label for segment 1')).toBeVisible({ timeout: 60_000 })
  const acceptedTrack = page.getByLabel('Accept Track 1')
  await acceptedTrack.click()
  await expect(acceptedTrack).toBeChecked()
  await page.getByRole('button', { name: 'Analyze accepted tracks' }).click()

  const batchMonitor = page.getByRole('heading', { name: 'Batch progress' }).locator('../..')
  await expect(batchMonitor).toContainText('1 complete', { timeout: 150_000 })
  await page.getByRole('button', { name: 'Generate archived report' }).click()
  await expect(page.getByText('JSON, Markdown, and CSV report revisions were archived.')).toBeVisible()
  await expect(page.getByRole('link', { name: 'JSON' })).toHaveAttribute('href', /\/api\/batches\/.+\/report\.json/)
  await expect(page.getByText('child_analysis.completed')).toBeVisible()
})
