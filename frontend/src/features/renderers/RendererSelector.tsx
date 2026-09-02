import { useState } from 'react'

import { BlenderVisualizerPanel } from '../../components/BlenderVisualizerPanel'
import type { Capabilities } from '../../types'
import { WzhkSpectrumPanel } from './WzhkSpectrumPanel'

type RendererId = 'blender' | 'wzhk-spectrum'

interface RendererSelectorProps {
  jobId: string
  capabilities: Capabilities
}

export function RendererSelector({ jobId, capabilities }: RendererSelectorProps) {
  const [rendererId, setRendererId] = useState<RendererId>('blender')
  return (
    <section className="renderer-selector" aria-labelledby="renderer-selector-heading">
      <div className="renderer-selector__heading">
        <span className="eyebrow">Visualizer / Renderer</span>
        <h2 id="renderer-selector-heading">Choose a renderer</h2>
        <p>Renderer dependencies are isolated. Selecting Spectrum does not change Blender or analysis availability.</p>
      </div>
      <fieldset className="renderer-selector__options">
        <legend className="sr-only">Visualizer renderer</legend>
        <label className={rendererId === 'blender' ? 'is-selected' : ''}>
          <input
            type="radio"
            name="renderer"
            value="blender"
            checked={rendererId === 'blender'}
            onChange={() => setRendererId('blender')}
          />
          <span><strong>Blender Visualizer</strong><small>Existing cue and configuration export</small></span>
        </label>
        <label className={rendererId === 'wzhk-spectrum' ? 'is-selected' : ''}>
          <input
            type="radio"
            name="renderer"
            value="wzhk-spectrum"
            checked={rendererId === 'wzhk-spectrum'}
            onChange={() => setRendererId('wzhk-spectrum')}
          />
          <span><strong>WZHK Spectrum</strong><small>Private Rainmeter workspace preparation</small></span>
        </label>
      </fieldset>
      {rendererId === 'blender'
        ? <BlenderVisualizerPanel jobId={jobId} capabilities={capabilities} />
        : <WzhkSpectrumPanel />}
    </section>
  )
}
