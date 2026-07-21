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
