import { useEffect, useRef, useState } from 'react'
import { Box, Download, Film, TriangleAlert } from 'lucide-react'

import { exportVisualCues } from '../api'
import type { VisualCuePreferences, VisualCurveDetail } from '../types'
import { Button, InlineNotice } from './ui'

const DEFAULT_PREFERENCES: VisualCuePreferences = {
  fps: 30,
  curveDetail: 'balanced',
  includeBeats: true,
  includeOnsets: true,
  includeStemEvidence: true,
  includeCurves: true,
}

export function BlenderVisualizerPanel({ jobId }: { jobId: string }) {
  const [preferences, setPreferences] = useState(DEFAULT_PREFERENCES)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState<string>()
  const [summary, setSummary] = useState<string>()
  const objectUrl = useRef<string>()

  useEffect(() => () => {
    if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
  }, [])

  const exportCues = async (): Promise<void> => {
    setExporting(true)
    setError(undefined)
    setSummary(undefined)
    try {
      const result = await exportVisualCues(jobId, preferences)
      if (objectUrl.current) URL.revokeObjectURL(objectUrl.current)
      objectUrl.current = URL.createObjectURL(result.blob)
      const anchor = document.createElement('a')
      anchor.href = objectUrl.current
      anchor.download = result.filename
      anchor.style.display = 'none'
      document.body.append(anchor)
      anchor.click()
      anchor.remove()
      setSummary(
        `Exported ${result.cueSheet.beats.length} beats, ${result.cueSheet.onsets.length} onsets, ${result.cueSheet.sections.length} sections, ${result.cueSheet.transitions.length} transitions, and ${Object.keys(result.cueSheet.curves).length} curves.`,
      )
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The visual cue sheet could not be exported.')
    } finally {
      setExporting(false)
    }
  }

  const toggle = (key: 'includeBeats' | 'includeOnsets' | 'includeCurves') => {
    setPreferences((current) => ({ ...current, [key]: !current[key] }))
  }

  return (
    <section className="visualizer-panel" aria-labelledby="blender-visualizer-heading">
      <div className="visualizer-panel__intro">
        <span className="eyebrow"><Box aria-hidden="true" /> Procedural video export</span>
        <h2 id="blender-visualizer-heading">Blender Visualizer</h2>
        <p>Exports a compact Blender-ready cue sheet containing timing events, structure, and normalized audio-reactive curves. Blender is not launched from this button.</p>
        <span className="local-chip"><Film aria-hidden="true" /> Abstract Geometry preset</span>
      </div>
      <div className="visualizer-panel__controls">
        <label className="field-stack">
          <span>Frames per second</span>
          <select
            aria-label="Frames per second"
            value={preferences.fps}
            onChange={(event) => setPreferences((current) => ({
              ...current,
              fps: Number(event.target.value) as VisualCuePreferences['fps'],
            }))}
          >
            {[24, 25, 30, 50, 60].map((fps) => <option key={fps} value={fps}>{fps} FPS</option>)}
          </select>
        </label>
        <label className="field-stack">
          <span>Curve detail</span>
          <select
            aria-label="Curve detail"
            value={preferences.curveDetail}
            onChange={(event) => setPreferences((current) => ({
              ...current,
              curveDetail: event.target.value as VisualCurveDetail,
            }))}
          >
            <option value="compact">Compact</option>
            <option value="balanced">Balanced</option>
            <option value="detailed">Detailed</option>
          </select>
        </label>
        <div className="visualizer-panel__toggles" aria-label="Cue sheet contents">
          <label><input type="checkbox" checked={preferences.includeBeats} onChange={() => toggle('includeBeats')} /> Beats</label>
          <label><input type="checkbox" checked={preferences.includeOnsets} onChange={() => toggle('includeOnsets')} /> Onsets</label>
          <label><input type="checkbox" checked={preferences.includeCurves} onChange={() => toggle('includeCurves')} /> Continuous curves</label>
        </div>
        <Button
          icon={<Download aria-hidden="true" />}
          busy={exporting}
          onClick={() => void exportCues()}
        >
          {exporting ? 'Preparing cue sheet...' : 'Export cue sheet'}
        </Button>
        {summary ? <InlineNotice tone="success">{summary}</InlineNotice> : null}
        {error ? <InlineNotice tone="error"><TriangleAlert aria-hidden="true" /> {error}</InlineNotice> : null}
      </div>
    </section>
  )
}
