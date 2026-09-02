/// <reference lib="dom" />

import { expect, test, type Page } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'
import {
  disconnectSyntheticRenderEvents,
  installSyntheticEventSource,
  installSyntheticMissionControlRoutes,
  makeSyntheticOutputMatrixFixture,
  pushSyntheticRenderEvent,
  syntheticEventSourceUrls,
  syntheticOutputVariantIds,
  type SyntheticOutputMatrixFixture,
  type SyntheticOutputMatrixMode,
} from './synthetic-output-matrix'

const PROFILE_HASH = 'DB27AA9DE2939ACA78819B58BB08C7DB408EED7092E83FA327363EE094779BF0'
const SCENE_HASH = '225EE7124B62434FF66D68E2477E5523C99914C76D7304366B0EBB696E0EFED5'
const AUTHORIZATION_TOKEN = 'AUTHORIZE FULL RENDER: TRIP-TO-ANDROMEDA | SPACE-JOURNEY | TRIP-TO-ANDROMEDA-720P-HYPER-OPTIMIZED | SCENE 225EE7124B62 | PROFILE DB27AA9DE293'

async function captureDocumentationScreenshot(page: Page, fileName: string): Promise<void> {
  if (process.env.MC_CAPTURE_DOCS !== '1') return
  const directory = path.resolve(process.cwd(), '..', 'docs', 'images')
  fs.mkdirSync(directory, { recursive: true })
  await page.screenshot({ path: path.join(directory, fileName), fullPage: true })
}

async function expectBasicAccessibility(page: Page): Promise<void> {
  const issues = await page.evaluate(() => {
    const visible = (element: Element): boolean => {
      const style = window.getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0
    }
    const labelText = (element: Element): string => {
      const id = element.getAttribute('id')
      const explicit = id
        ? Array.from(document.querySelectorAll('label')).find((label) => label.htmlFor === id)?.textContent
        : null
      return [
        element.getAttribute('aria-label'),
        element.getAttribute('aria-labelledby')
          ?.split(/\s+/)
          .map((labelId) => document.getElementById(labelId)?.textContent ?? '')
          .join(' '),
        explicit,
        element.closest('label')?.textContent,
        element.textContent,
        element.getAttribute('title'),
      ].filter(Boolean).join(' ').trim()
    }

    const found: string[] = []
    const ids = Array.from(document.querySelectorAll<HTMLElement>('[id]')).map((element) => element.id)
    const duplicates = ids.filter((id, index) => ids.indexOf(id) !== index)
    if (duplicates.length > 0) found.push(`duplicate ids: ${Array.from(new Set(duplicates)).join(', ')}`)
    if (document.querySelectorAll('main').length !== 1) found.push('page must contain exactly one main landmark')
    if (document.querySelectorAll('h1').length !== 1) found.push('page must contain exactly one h1')

    for (const image of Array.from(document.querySelectorAll('img'))) {
      if (!image.hasAttribute('alt')) found.push(`image missing alt: ${image.getAttribute('src') ?? 'unknown'}`)
    }
    for (const control of Array.from(document.querySelectorAll('button, a[href], input, select, textarea'))) {
      if (!visible(control) || control.getAttribute('aria-hidden') === 'true') continue
      if (!labelText(control)) found.push(`unlabelled ${control.tagName.toLowerCase()}`)
    }
    return found
  })
  expect(issues).toEqual([])
}

function metric(page: Page, label: string) {
  return page.locator('.mc-metric').filter({ has: page.getByText(label, { exact: true }) })
}

function variantCard(page: Page, label: string) {
  return page.locator('.mc-variant-status').filter({ has: page.getByText(label, { exact: true }) })
}

async function openSyntheticOutputMatrix(
  page: Page,
  mode: SyntheticOutputMatrixMode,
): Promise<SyntheticOutputMatrixFixture> {
  const fixture = makeSyntheticOutputMatrixFixture(mode)
  await installSyntheticEventSource(page)
  await installSyntheticMissionControlRoutes(page, fixture)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /rendering frame/i })).toBeVisible()
  await expect(page.locator('.mc-connection-chip').getByText('Connected', { exact: true })).toBeVisible()
  return fixture
}

test('casual user can authorize, reconnect, safely stop, resume, and complete a fake render', async ({ page, request }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/?renderer=fake')
  await expect(page.getByRole('heading', { name: 'Trip to Andromeda' })).toBeVisible()
  await expect(page.getByText(/Trip to Andromeda - 720p Hyper Optimized/)).toBeVisible()
  await expect(page.getByText('Ready to authorize', { exact: true })).toBeVisible()
  await expect(page.getByText(SCENE_HASH, { exact: false })).toHaveCount(0)
  await expect(page.getByText(PROFILE_HASH, { exact: false })).toHaveCount(0)
  await expectBasicAccessibility(page)
  await captureDocumentationScreenshot(page, 'mission-control-home.png')

  await page.getByRole('button', { name: /start a new render/i }).click()
  await expect(page.getByRole('heading', { name: 'New render' })).toBeVisible()
  await expect(page.getByText(/13,029 frames/)).toBeVisible()
  await page.getByRole('button', { name: /continue/i }).click()

  await expect(page.getByRole('heading', { name: /select a render profile/i })).toBeVisible()
  const recommendedProfile = page.getByRole('radio', { name: /720p hyper optimized/i })
  await expect(recommendedProfile).toBeChecked()
  await page.getByRole('button', { name: /continue/i }).click()

  await expect(page.getByRole('heading', { name: /choose an output folder/i })).toBeVisible()
  await page.getByRole('button', { name: /browse/i }).click()
  await expect(page.getByText('Folder is ready')).toBeVisible()
  await page.getByRole('button', { name: /continue/i }).click()

  const preflightRequestPromise = page.waitForRequest((request) => request.url().endsWith('/render/preflight'))
  await page.getByRole('button', { name: /run production preflight/i }).click()
  const preflightRequest = await preflightRequestPromise
  expect(preflightRequest.postDataJSON()).toMatchObject({ renderer: 'fake' })
  await expect(page.getByText('Ready to authorize', { exact: true })).toBeVisible()
  await expect(page.getByText('Authorization is required before start.')).toBeVisible()
  await page.getByRole('button', { name: /continue/i }).click()

  await expect(page.getByText('Authorization required', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: /authorize now/i }).click()
  const reviewDialog = page.getByRole('dialog', { name: /authorize this render configuration/i })
  await expect(reviewDialog).toBeVisible()
  await expect(reviewDialog.getByRole('button', { name: /close dialog/i })).toBeFocused()
  await page.keyboard.press('Shift+Tab')
  await expect(reviewDialog.getByRole('button', { name: /review and continue/i })).toBeFocused()
  await page.keyboard.press('Tab')
  await expect(reviewDialog.getByRole('button', { name: /close dialog/i })).toBeFocused()
  await expect(reviewDialog.getByText(/13,029/)).toBeVisible()
  await expect(reviewDialog.getByText(/1280/)).toBeVisible()
  await reviewDialog.getByRole('button', { name: /review and continue/i }).click()

  const authorizeDialog = page.getByRole('dialog', { name: /ready to authorize the exact scene and profile/i })
  await expect(authorizeDialog.getByRole('button', { name: /authorize render/i })).toBeDisabled()
  await authorizeDialog.getByRole('checkbox', { name: /full production render/i }).check()
  const authorizationResponsePromise = page.waitForResponse((response) => response.url().includes('/profiles/') && response.url().endsWith('/authorize'))
  await authorizeDialog.getByRole('button', { name: /authorize render/i }).click()
  const authorizationResponse = await authorizationResponsePromise
  expect(authorizationResponse.ok()).toBe(true)
  const authorization = await authorizationResponse.json() as { authorizationToken: string; sceneSha256: string; profileSha256: string }
  expect(authorization.authorizationToken).toBe(AUTHORIZATION_TOKEN)
  expect(authorization.sceneSha256).toBe(SCENE_HASH)
  expect(authorization.profileSha256).toBe(PROFILE_HASH)

  await expect(page.getByRole('heading', { name: /ready to start/i })).toBeVisible()
  const startButton = page.getByRole('button', { name: /^start render$/i })
  await expect(startButton).toBeEnabled()
  const beforeJobs = await request.get('/api/mission-control/jobs')
  expect(await beforeJobs.json()).toEqual([])
  await startButton.click()

  await expect(page.getByText('Rendering is still active')).toBeVisible({ timeout: 15_000 })
  await expect(page.getByText('Rendered, not yet safe', { exact: true })).toBeVisible()
  await expect(page.getByText('Safe, preserved on resume', { exact: true })).toBeVisible()
  await expect(page.locator('.mc-connection-chip').getByText('Connected', { exact: true })).toBeVisible()
  await expectBasicAccessibility(page)
  await expect(page.getByRole('img', { name: /latest completed render frame/i })).toBeVisible({ timeout: 15_000 })
  await captureDocumentationScreenshot(page, 'mission-control-render-progress.png')

  const stopButton = page.getByRole('button', { name: /stop after current chunk/i })
  await expect(stopButton).toBeVisible()
  await stopButton.click()
  await expect(page.getByRole('button', { name: /resume exact render/i })).toBeVisible({ timeout: 15_000 })

  const jobsBeforeReload = await request.get('/api/mission-control/jobs')
  const oneJob = await jobsBeforeReload.json() as Array<{ id: string; state: string; identity: { sceneSha256: string; profileSha256: string } }>
  expect(oneJob).toHaveLength(1)
  expect(oneJob[0]?.state).toBe('PAUSED_SAFELY')
  expect(oneJob[0]?.identity.sceneSha256).toBe(SCENE_HASH)
  expect(oneJob[0]?.identity.profileSha256).toBe(PROFILE_HASH)

  await page.reload()
  await expect(page.getByRole('button', { name: /resume exact render/i })).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('.mc-connection-chip').getByText('Connected', { exact: true })).toBeVisible()
  const jobsAfterReload = await request.get('/api/mission-control/jobs')
  expect(await jobsAfterReload.json()).toHaveLength(1)

  await page.getByRole('button', { name: /resume exact render/i }).click()
  await expect(page.getByRole('heading', { name: 'Render complete' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/every required frame has been validated and published safely/i)).toBeVisible()
  await page.getByRole('button', { name: /encode video/i }).click()
  await expect(page.getByRole('heading', { name: 'Encode video' })).toBeVisible()
  await expect(page.getByRole('button', { name: /encode delivery \+ master/i })).toBeEnabled()
  await expect(page.getByRole('heading', { name: 'Verified frame sequence' })).toBeVisible()
  await expect(page.getByText('120 / 120 frames')).toBeVisible()
  await expect(page.getByText('No complete frame sequence yet')).toHaveCount(0)
})

test('dual output streams preserve ordered publication, reject foreign telemetry, and resume after SSE reconnect', async ({ page }) => {
  await page.setViewportSize({ width: 1_440, height: 1_100 })
  const fixture = await openSyntheticOutputMatrix(page, 'dual')
  const horizontalCard = variantCard(page, 'Horizontal 16:9')
  const verticalCard = variantCard(page, 'Vertical 9:16')

  await expect(page.getByRole('heading', { name: 'Output variants' })).toBeVisible()
  await expect(page.locator('.mc-variant-status')).toHaveCount(2)
  await expect(horizontalCard).toContainText(/1920.*1080/)
  await expect(horizontalCard).toContainText('12 of 100 safe')
  await expect(horizontalCard).toContainText('14 rendered')
  await expect(verticalCard).toContainText(/1080.*1920/)
  await expect(verticalCard).toContainText('8 of 100 safe')
  await expect(verticalCard).toContainText('9 rendered')
  await expect(page.getByText(/20 of 200 frames safe across 2 variants/)).toBeVisible()
  await expect(page.getByRole('combobox', { name: 'Output variant' })).toHaveValue(syntheticOutputVariantIds.horizontal)
  await expect(page.getByRole('img', { name: 'Latest completed Horizontal 16:9 frame 14' })).toBeVisible()
  await expect(page.locator('.mc-preview-card__image')).toHaveCSS('aspect-ratio', '1920 / 1080')
  await expect(page.getByRole('link', { name: /open exact full-resolution frame/i }))
    .toHaveAttribute('href', new RegExp(`output_variant_id=${syntheticOutputVariantIds.horizontal}`))
  await expect(metric(page, 'Render ETA P50')).toContainText('About 1 hours')
  await expect(metric(page, 'Render ETA P90')).toContainText('About 1 hr 30 min')
  await expect(metric(page, 'Aggregate ETA P50')).toContainText('About 3 hours')
  await expect(metric(page, 'Aggregate ETA P90')).toContainText('About 4 hours')

  await pushSyntheticRenderEvent(page, fixture.event({
    sequence: 101,
    horizontal: {
      currentFrame: 16,
      latestRenderedFrame: 15,
      latestSafeFrame: 13,
      renderedFrames: 15,
      inFlightFrames: 2,
      validatedFrames: 13,
      publishedFrames: 13,
      previewFrame: 15,
    },
  }))
  await expect(horizontalCard).toContainText('13 of 100 safe')
  await expect(horizontalCard).toContainText('15 rendered')
  await expect(page.getByRole('img', { name: 'Latest completed Horizontal 16:9 frame 15' })).toBeVisible()

  await pushSyntheticRenderEvent(page, fixture.event({
    sequence: 100,
    horizontal: {
      currentFrame: 2,
      latestRenderedFrame: 1,
      latestSafeFrame: 1,
      renderedFrames: 1,
      inFlightFrames: 0,
      validatedFrames: 1,
      publishedFrames: 1,
      previewFrame: 1,
    },
  }))
  await expect.poll(async () => page.evaluate(() => {
    const raw = window.localStorage.getItem('wzhk.mission-control.last-event')
    return raw ? (JSON.parse(raw) as { sequence?: number }).sequence : null
  })).toBe(101)
  await expect(horizontalCard).toContainText('13 of 100 safe')
  await expect(page.getByRole('img', { name: 'Latest completed Horizontal 16:9 frame 15' })).toBeVisible()

  const rejection = 'Rejected cross-format telemetry for vertical-9x16-1080p: expected 1080 x 1920 but received 1920 x 1080.'
  await pushSyntheticRenderEvent(page, fixture.event({
    sequence: 102,
    warning: rejection,
    horizontal: {
      currentFrame: 16,
      latestRenderedFrame: 15,
      latestSafeFrame: 13,
      renderedFrames: 15,
      inFlightFrames: 2,
      validatedFrames: 13,
      publishedFrames: 13,
      previewFrame: 15,
    },
  }))
  await expect(page.getByText('Render warning', { exact: true })).toBeVisible()
  await expect(page.getByText(rejection, { exact: true })).toBeVisible()
  await expect(horizontalCard).toContainText('13 of 100 safe')
  await expect(verticalCard).toContainText('8 of 100 safe')

  const selector = page.getByRole('combobox', { name: 'Output variant' })
  await selector.selectOption(syntheticOutputVariantIds.vertical)
  await expect(verticalCard).toHaveAttribute('data-selected', 'true')
  await expect(page.getByRole('img', { name: 'Latest completed Vertical 9:16 frame 9' })).toBeVisible()
  await expect(page.locator('.mc-preview-card__image')).toHaveCSS('aspect-ratio', '1080 / 1920')
  await expect(page.getByRole('link', { name: /open exact full-resolution frame/i }))
    .toHaveAttribute('href', new RegExp(`output_variant_id=${syntheticOutputVariantIds.vertical}`))
  await expect(metric(page, 'Render ETA P50')).toContainText('About 2 hours')
  await expect(metric(page, 'Render ETA P90')).toContainText('About 2 hr 30 min')
  await expect(page.locator('.mc-safety-progress__item--flight')).toContainText('1 frames')
  await expect(page.locator('.mc-safety-progress__item--safe')).toContainText('8 frames')
  await expect(metric(page, 'Active workers')).toContainText('1')

  await disconnectSyntheticRenderEvents(page)
  await expect(page.locator('.mc-connection-chip').getByText('Reconnecting', { exact: true })).toBeVisible()
  await expect(page.getByText('Reconnecting to live updates', { exact: true })).toBeVisible()
  await expect(page.locator('.mc-connection-chip').getByText('Connected', { exact: true })).toBeVisible({ timeout: 3_000 })

  const connectionUrls = await syntheticEventSourceUrls(page)
  expect(connectionUrls).toHaveLength(2)
  expect(connectionUrls[1]).toContain('afterSequence=102')

  await pushSyntheticRenderEvent(page, fixture.event({
    sequence: 103,
    activeVariantId: syntheticOutputVariantIds.vertical,
    horizontal: {
      currentFrame: 16,
      latestRenderedFrame: 15,
      latestSafeFrame: 13,
      renderedFrames: 15,
      inFlightFrames: 2,
      validatedFrames: 13,
      publishedFrames: 13,
      previewFrame: 15,
    },
    vertical: {
      currentFrame: 11,
      latestRenderedFrame: 10,
      latestSafeFrame: 9,
      renderedFrames: 10,
      inFlightFrames: 1,
      validatedFrames: 9,
      publishedFrames: 9,
      previewFrame: 10,
    },
  }))
  await expect(verticalCard).toContainText('9 of 100 safe')
  await expect(verticalCard).toContainText('10 rendered')
  await expect(page.getByRole('img', { name: 'Latest completed Vertical 9:16 frame 10' })).toBeVisible()
  await expect(page.getByText(rejection, { exact: true })).toHaveCount(0)
})

test('horizontal-only output matrix does not invent a vertical card, ETA, or worker', async ({ page }) => {
  await page.setViewportSize({ width: 1_280, height: 960 })
  const fixture = await openSyntheticOutputMatrix(page, 'horizontal-only')
  const horizontalCard = variantCard(page, 'Horizontal 16:9')

  await expect(page.locator('.mc-variant-status')).toHaveCount(1)
  await expect(horizontalCard).toContainText(/1920.*1080/)
  await expect(horizontalCard).toContainText('12 of 100 safe')
  await expect(page.getByText('Vertical 9:16', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('combobox', { name: 'Output variant' })).toHaveCount(0)
  await expect(page.getByText(/frames safe across 2 variants/)).toHaveCount(0)
  await expect(page.getByRole('img', { name: 'Latest completed Horizontal 16:9 frame 14' })).toBeVisible()
  await expect(metric(page, 'Render ETA P50')).toContainText('30 minutes')
  await expect(metric(page, 'Render ETA P90')).toContainText('45 minutes')
  await expect(metric(page, 'Aggregate ETA P50')).toContainText('30 minutes')
  await expect(metric(page, 'Aggregate ETA P90')).toContainText('45 minutes')
  await expect(metric(page, 'Active workers')).toContainText('1')
  await expect(page.locator('.mc-safety-progress__item--flight')).toContainText('2 frames')
  await expect(page.locator('.mc-safety-progress__item--safe')).toContainText('12 frames')

  await pushSyntheticRenderEvent(page, fixture.event({
    sequence: 201,
    vertical: {
      currentFrame: 100,
      latestRenderedFrame: 100,
      latestSafeFrame: 100,
      renderedFrames: 100,
      inFlightFrames: 0,
      validatedFrames: 100,
      publishedFrames: 100,
      previewFrame: 100,
      etaP50Seconds: 1,
      etaP90Seconds: 1,
    },
  }))
  await expect(page.getByText('Vertical 9:16', { exact: true })).toHaveCount(0)
  await expect(horizontalCard).toContainText('12 of 100 safe')
  await expect(metric(page, 'Aggregate ETA P50')).toContainText('30 minutes')
  await expect(metric(page, 'Active workers')).toContainText('1')
})

test('narrow layout, keyboard navigation, modal focus, and reduced motion remain usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/?renderer=fake')
  await expect(page.getByRole('heading', { name: /good to see you/i })).toBeVisible()
  await expect(page.getByRole('button', { name: /open navigation/i })).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true)

  await page.keyboard.press('Tab')
  await expect(page.getByRole('link', { name: /skip to main content/i })).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#mission-control-main')).toBeFocused()
  await expectBasicAccessibility(page)

  const movingElements = await page.evaluate(() => Array.from(document.querySelectorAll('.mc-root *')).filter((element) => {
    const style = window.getComputedStyle(element)
    const animation = Number.parseFloat(style.animationDuration || '0')
    const transition = Number.parseFloat(style.transitionDuration || '0')
    return animation > 0.02 || transition > 0.02
  }).length)
  expect(movingElements).toBe(0)
})
