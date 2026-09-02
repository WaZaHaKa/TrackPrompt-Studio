import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import * as api from '../api'
import { capabilities } from '../test/factories'
import { CatalogueWorkspace } from './CatalogueWorkspace'

vi.mock('../api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api')>()
  return {
    ...original,
    listCatalogueClients: vi.fn(),
    createCatalogueClient: vi.fn(),
    listCatalogueProjects: vi.fn(),
    listCatalogueBatches: vi.fn(),
    listProjectAudit: vi.fn(),
    listSourceAssets: vi.fn(),
    listBatchQueue: vi.fn(),
  }
})

describe('CatalogueWorkspace', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.mocked(api.listCatalogueClients).mockResolvedValue({ items: [], total: 0, offset: 0, limit: 200 })
    vi.mocked(api.listCatalogueProjects).mockResolvedValue([])
    vi.mocked(api.listCatalogueBatches).mockResolvedValue([])
    vi.mocked(api.listProjectAudit).mockResolvedValue([])
    vi.mocked(api.listSourceAssets).mockResolvedValue([])
    vi.mocked(api.listBatchQueue).mockResolvedValue([])
  })

  it('creates a private client from the catalogue workspace', async () => {
    vi.mocked(api.createCatalogueClient).mockResolvedValue({
      id: '11111111-1111-4111-8111-111111111111',
      displayName: 'Mastering client',
      privateNotes: '',
      tags: [],
      archived: false,
      projectCount: 0,
      createdAt: '2026-07-20T00:00:00Z',
      updatedAt: '2026-07-20T00:00:00Z',
    })
    render(<CatalogueWorkspace capabilities={capabilities} />)
    const clientName = screen.getByLabelText('New client name')
    fireEvent.change(clientName, { target: { value: 'Mastering client' } })
    const clientForm = clientName.closest('.catalogue-inline-form')
    expect(clientForm).not.toBeNull()
    fireEvent.click(clientForm!.querySelector('button')!)
    await waitFor(() => expect(api.createCatalogueClient).toHaveBeenCalledWith('Mastering client'))
  })

  it('accepts 1000 lightweight file records while rendering only one bounded page', async () => {
    const { container } = render(<CatalogueWorkspace capabilities={capabilities} />)
    const files = Array.from(
      { length: 1000 },
      (_value, index) => new File([new Uint8Array([index % 255])], `track-${index}.wav`, { type: 'audio/wav' }),
    )
    fireEvent.change(screen.getByLabelText('Bulk audio files'), { target: { files } })
    expect(await screen.findByText('1000 items')).toBeInTheDocument()
    expect(container.querySelectorAll('.bulk-row')).toHaveLength(50)
    expect(screen.getByText('Page 1 of 20')).toBeInTheDocument()
  })

  it('restores bounded upload progress metadata after reload', async () => {
    localStorage.setItem('trackprompt.catalogue.uploads.v1', JSON.stringify([{
      id: 'local-1', name: 'set.wav', size: 1000, lastModified: 10, order: 0,
      sessionId: '22222222-2222-4222-8222-222222222222', receivedBytes: 500, state: 'paused',
    }]))
    const { container } = render(<CatalogueWorkspace capabilities={capabilities} />)
    await waitFor(() => expect(api.listCatalogueClients).toHaveBeenCalled())
    expect(screen.getByText('set.wav')).toBeInTheDocument()
    expect(container.querySelector('progress[value="50"]')).toBeInTheDocument()
    expect(screen.getByText(/Reselect matching local files/)).toBeInTheDocument()
  })
})
