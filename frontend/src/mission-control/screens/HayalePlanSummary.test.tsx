import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { HayalePlanSummary } from './HayalePlanSummary'
import { hayaleEdit } from './hayaleEdit'

describe('Hayale plan summary', () => {
  it('separates edited scenes, provider seconds and human approval', () => {
    render(<HayalePlanSummary jobId="opaque-job" continuity={{ hayaleEdit: {
      profile: 'motion-preview', editedSeconds: 64, generatedSeconds: 8, totalFrames: 1536,
    } }} />)
    expect(screen.getByText('Hayale · 16 scenes × 4 seconds')).toBeInTheDocument()
    expect(screen.getByText(/8 provider source seconds/)).toBeInTheDocument()
    expect(screen.getByText(/separate decision before a final batch/)).toBeInTheDocument()
    expect(screen.getByText(/Technical verification and creative approval/)).toBeInTheDocument()
  })
  it('leaves historical plans alone', () => {
    expect(hayaleEdit({})).toBeNull()
    expect(hayaleEdit({ hayaleEdit: { profile: 'final' } })).toBeNull()
  })
})
