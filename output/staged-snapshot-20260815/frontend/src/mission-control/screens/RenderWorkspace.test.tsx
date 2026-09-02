import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { makeClient, makeRenderJob, makeSnapshot, testProfile, testScene } from '../testFixtures'
import { RenderWorkspace } from './RenderWorkspace'

describe('RenderWorkspace', () => {
  it('recovers an incompatible output, advances an unauthorized preflight, enforces two confirmations, and refreshes preflight before start', async () => {
    const user = userEvent.setup()
    const preflight = vi.fn()
      .mockResolvedValueOnce({
        ready: false,
        authorizationRequired: true,
        checks: [
          { id: 'scene', label: 'Scene verified', status: 'pass', summary: 'Exact scene identity matches.', technicalDetails: null },
          { id: 'profile', label: 'Profile verified', status: 'pass', summary: 'Saved-file identity matches.', technicalDetails: null },
          { id: 'authorization', label: 'Authorization', status: 'warning', summary: 'Authorization required.', technicalDetails: null },
        ],
        sceneSha256: testScene.sha256,
        profileSha256: testProfile.savedFileSha256,
        exactOperation: 'Full production render',
        rawDetails: null,
      })
      .mockResolvedValueOnce({
        ready: true,
        authorizationRequired: false,
        checks: [{ id: 'authorization', label: 'Authorization', status: 'pass', summary: 'Exact authorization record verified.', technicalDetails: null }],
        sceneSha256: testScene.sha256,
        profileSha256: testProfile.savedFileSha256,
        exactOperation: 'Full production render',
        rawDetails: null,
      })
    const authorize = vi.fn().mockResolvedValue({
      authorized: true,
      authorizationId: 'auth-sha',
      authorizedAt: '2026-07-21T10:00:00Z',
      sceneSha256: testScene.sha256,
      profileSha256: testProfile.savedFileSha256,
      token: 'hidden-in-simple-mode',
    })
    const startRender = vi.fn().mockResolvedValue(makeRenderJob())
    const createOutputChild = vi.fn().mockResolvedValue({
      path: 'C:\\renders\\trip-to-andromeda-001',
      classification: 'empty',
      usable: true,
      resumable: false,
      entries: [],
      message: 'A unique render folder was created.',
      suggestedChildName: null,
      conflictingIdentity: null,
    })
    const client = makeClient({
      inspectOutput: vi.fn().mockResolvedValue({
        path: 'C:\\renders',
        classification: 'hidden_entries',
        usable: false,
        resumable: false,
        entries: ['desktop.ini', '.previous-render'],
        message: 'Hidden or unrelated files are present.',
        suggestedChildName: 'trip-to-andromeda-001',
        conflictingIdentity: null,
      }),
      createOutputChild,
      preflight,
      authorize,
      startRender,
    })
    const onJobStarted = vi.fn()

    render(
      <RenderWorkspace
        data={makeSnapshot()}
        client={client}
        advanced={false}
        initialProfileId={null}
        resetKey={0}
        activeJob={null}
        connection="connected"
        logs={[]}
        jobBusyAction={null}
        onJobStarted={onJobStarted}
        onRefreshJob={vi.fn()}
        onStopAfterChunk={vi.fn()}
        onCancelStop={vi.fn()}
        onResumeJob={vi.fn()}
        onRefreshData={vi.fn().mockResolvedValue(undefined)}
        onOpenOutput={vi.fn()}
        onEncode={vi.fn()}
        onDismissJobError={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /continue/i }))
    expect(screen.getByRole('heading', { name: /select a render profile/i })).toBeInTheDocument()
    expect(screen.getByText('720p Hyper Optimized')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /continue/i }))

    await user.click(screen.getByRole('button', { name: /browse/i }))
    expect(await screen.findByText('desktop.ini')).toBeInTheDocument()
    expect(screen.getByText('.previous-render')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /create a new render folder here/i }))
    expect(await screen.findByText(/folder is ready/i)).toBeInTheDocument()
    expect(createOutputChild).toHaveBeenCalledTimes(1)
    await user.click(screen.getByRole('button', { name: /continue/i }))

    await user.click(screen.getByRole('button', { name: /run production preflight/i }))
    expect(await screen.findByText('Ready to authorize')).toBeInTheDocument()
    const continueAfterPreflight = screen.getByRole('button', { name: /continue/i })
    expect(continueAfterPreflight).toBeEnabled()
    await user.click(continueAfterPreflight)

    await user.click(screen.getByRole('button', { name: /authorize now/i }))
    const firstDialog = screen.getByRole('dialog', { name: /authorize this render configuration/i })
    expect(within(firstDialog).getByText('13,029')).toBeInTheDocument()
    await user.click(within(firstDialog).getByRole('button', { name: /review and continue/i }))

    const secondDialog = screen.getByRole('dialog', { name: /ready to authorize the exact scene and profile/i })
    const finalButton = within(secondDialog).getByRole('button', { name: /authorize render/i })
    expect(finalButton).toBeDisabled()
    await user.click(within(secondDialog).getByRole('checkbox', { name: /full production render/i }))
    expect(finalButton).toBeEnabled()
    await user.click(finalButton)

    expect(await screen.findByRole('heading', { name: /ready to start/i })).toBeInTheDocument()
    expect(authorize).toHaveBeenCalledWith(expect.objectContaining({ sceneId: testScene.id, profileId: testProfile.id }), {
      configurationReviewed: true,
      fullRenderApproved: true,
    })
    expect(preflight).toHaveBeenCalledTimes(2)

    await user.click(screen.getByRole('button', { name: /^start render$/i }))
    await waitFor(() => expect(startRender).toHaveBeenCalledWith(expect.objectContaining({
      renderer: 'fake',
      authorizationId: 'auth-sha',
    })))
    expect(onJobStarted).toHaveBeenCalledTimes(1)
  })

  it('shows a dry-run result without treating it as an active render job', async () => {
    const user = userEvent.setup()
    const client = makeClient()
    const onJobStarted = vi.fn()
    render(
      <RenderWorkspace
        data={makeSnapshot({ profiles: [{ ...testProfile, authorizationStatus: 'authorized' }] })}
        client={client}
        advanced
        initialProfileId={null}
        resetKey={0}
        activeJob={null}
        connection="connected"
        logs={[]}
        jobBusyAction={null}
        onJobStarted={onJobStarted}
        onRefreshJob={vi.fn()}
        onStopAfterChunk={vi.fn()}
        onCancelStop={vi.fn()}
        onResumeJob={vi.fn()}
        onRefreshData={vi.fn().mockResolvedValue(undefined)}
        onOpenOutput={vi.fn()}
        onEncode={vi.fn()}
        onDismissJobError={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /browse/i }))
    await screen.findByText(/folder is ready/i)
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /run production preflight/i }))
    await screen.findByText('Ready to start')
    await user.click(screen.getByRole('button', { name: /continue/i }))
    await user.click(screen.getByRole('button', { name: /run dry-run/i }))

    expect(await screen.findByText('Dry-run passed')).toBeInTheDocument()
    expect(onJobStarted).not.toHaveBeenCalled()
  })
})
