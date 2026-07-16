import type { AnalysisGroup, FactUpdate } from '../types'
import { collectFeatureEntries } from '../types'
import { FactField } from './FactField'
import { InlineNotice } from './ui'

interface AnalysisGroupPanelProps {
  group: AnalysisGroup
  prefix: string
  title: string
  description: string
  disabledPaths: string[]
  savingPath?: string
  onUpdate: (update: FactUpdate) => Promise<void>
}

export function AnalysisGroupPanel({
  group,
  prefix,
  title,
  description,
  disabledPaths,
  savingPath,
  onUpdate,
}: AnalysisGroupPanelProps) {
  const entries = collectFeatureEntries(group, prefix)
  return (
    <section className="analysis-group" aria-labelledby={`${prefix}-heading`}>
      <div className="section-heading">
        <div><span className="eyebrow">Detected evidence</span><h2 id={`${prefix}-heading`}>{title}</h2><p>{description}</p></div>
        <span className="count-pill">{entries.length} facts</span>
      </div>
      {entries.length === 0 ? (
        <InlineNotice tone="warning">This analyzer did not return usable facts. Nothing has been invented to fill the gap.</InlineNotice>
      ) : (
        <div className="fact-grid">
          {entries.map((entry) => (
            <FactField
              key={entry.path}
              entry={entry}
              disabledForPrompt={disabledPaths.includes(entry.path)}
              saving={savingPath === entry.path}
              onSave={(path, value) => onUpdate({ path, value })}
              onAccept={(path, acceptedForPrompt) => onUpdate({ path, acceptedForPrompt })}
              onDisable={(path, disabledForPrompt) => onUpdate({ path, disabledForPrompt })}
              onRestore={(path) => onUpdate({ path, restoreDetected: true })}
            />
          ))}
        </div>
      )}
    </section>
  )
}
