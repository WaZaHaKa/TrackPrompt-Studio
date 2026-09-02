import { useEffect, useId, useState } from 'react'
import { Check, Eye, EyeOff, Pencil, RotateCcw, Save, X } from 'lucide-react'
import type { FeatureEntry } from '../types'
import { formatFeatureValue } from '../types'
import { Button, ConfidenceBadge } from './ui'

function parseEditedValue(text: string, original: unknown): unknown {
  const trimmed = text.trim()
  if (typeof original === 'number') {
    const value = Number(trimmed)
    return Number.isFinite(value) ? value : trimmed
  }
  if (typeof original === 'boolean') {
    if (/^(true|yes)$/i.test(trimmed)) return true
    if (/^(false|no)$/i.test(trimmed)) return false
  }
  if (Array.isArray(original)) {
    if (original.some((item) => typeof item === 'object' && item !== null)) return JSON.parse(trimmed) as unknown
    return trimmed.split(',').map((item) => item.trim()).filter(Boolean)
  }
  if (typeof original === 'object' && original !== null) return JSON.parse(trimmed) as unknown
  return trimmed
}

function formatEditableValue(value: unknown): string {
  if (
    (Array.isArray(value) && value.some((item) => typeof item === 'object' && item !== null)) ||
    (typeof value === 'object' && value !== null && !Array.isArray(value))
  ) {
    return JSON.stringify(value)
  }
  return formatFeatureValue(value)
}

interface FactFieldProps {
  entry: FeatureEntry
  disabledForPrompt: boolean
  saving?: boolean
  onSave: (path: string, value: unknown) => Promise<void>
  onAccept: (path: string, accepted: boolean) => Promise<void>
  onDisable: (path: string, disabled: boolean) => Promise<void>
  onRestore: (path: string) => Promise<void>
}

export function FactField({
  entry,
  disabledForPrompt,
  saving = false,
  onSave,
  onAccept,
  onDisable,
  onRestore,
}: FactFieldProps) {
  const editId = useId()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(formatEditableValue(entry.feature.value))
  const [error, setError] = useState<string>()

  useEffect(() => {
    if (!editing) setDraft(formatEditableValue(entry.feature.value))
  }, [editing, entry.feature.value])

  const save = async (): Promise<void> => {
    setError(undefined)
    try {
      await onSave(entry.path, parseEditedValue(draft, entry.feature.value))
      setEditing(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The change could not be saved.')
    }
  }

  const toggleDisabled = async (): Promise<void> => {
    setError(undefined)
    try {
      await onDisable(entry.path, !disabledForPrompt)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The prompt setting could not be saved.')
    }
  }

  const toggleAccepted = async (): Promise<void> => {
    setError(undefined)
    try {
      await onAccept(entry.path, !entry.feature.userAccepted)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The review decision could not be saved.')
    }
  }

  const restore = async (): Promise<void> => {
    setError(undefined)
    try {
      await onRestore(entry.path)
      setEditing(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The detected value could not be restored.')
    }
  }

  return (
    <article className={`fact-field ${disabledForPrompt ? 'fact-field--disabled' : ''}`}>
      <div className="fact-field__topline">
        <span className="fact-field__label">{entry.label}</span>
        <ConfidenceBadge confidence={entry.feature.confidence} />
      </div>
      {editing ? (
        <div className="fact-editor">
          <label htmlFor={editId}>Edit {entry.label}</label>
          <input
            id={editId}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void save()
              if (event.key === 'Escape') setEditing(false)
            }}
            autoFocus
          />
          <div className="fact-editor__actions">
            <Button icon={<Save aria-hidden="true" />} busy={saving} onClick={() => void save()}>Save</Button>
            <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={() => setEditing(false)}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="fact-field__value">
          <strong>{formatFeatureValue(entry.feature.value)}</strong>
          {entry.feature.userEdited ? <span className="edited-chip">Edited</span> : entry.feature.userAccepted ? <span className="accepted-chip"><Check aria-hidden="true" /> Accepted for prompt</span> : null}
        </div>
      )}
      {entry.feature.warning ? <p className="fact-warning">{entry.feature.warning}</p> : null}
      {error ? <p className="field-error" role="alert">{error}</p> : null}
      <div className="fact-field__actions">
        {!editing ? (
          <>
            <Button
              variant="ghost"
              icon={<Check aria-hidden="true" />}
              aria-pressed={entry.feature.userAccepted}
              busy={saving}
              onClick={() => void toggleAccepted()}
            >{entry.feature.userAccepted ? 'Unaccept' : 'Accept'}</Button>
            <Button variant="ghost" icon={<Pencil aria-hidden="true" />} onClick={() => setEditing(true)}>Edit</Button>
            <Button
              variant="ghost"
              icon={disabledForPrompt ? <Eye aria-hidden="true" /> : <EyeOff aria-hidden="true" />}
              busy={saving}
              onClick={() => void toggleDisabled()}
            >{disabledForPrompt ? 'Use in prompt' : 'Disable for prompt'}</Button>
            {entry.feature.userEdited ? (
              <Button variant="ghost" icon={<RotateCcw aria-hidden="true" />} busy={saving} onClick={() => void restore()}>Restore detected</Button>
            ) : null}
          </>
        ) : null}
      </div>
      <details className="method-details">
        <summary>Method & confidence</summary>
        <dl>
          <div><dt>Method</dt><dd>{entry.feature.method}</dd></div>
          <div><dt>Confidence</dt><dd>{entry.feature.confidence}</dd></div>
          <div><dt>Evidence type</dt><dd>{entry.feature.userEdited ? 'user edited' : entry.feature.userAccepted ? 'user accepted' : (entry.feature.evidenceKind ?? 'heuristic').replaceAll('_', ' ')}</dd></div>
          {typeof entry.feature.score === 'number' ? <div><dt>Analyzer score</dt><dd>{entry.feature.score.toFixed(3)}</dd></div> : null}
          {entry.feature.alternatives && entry.feature.alternatives.length > 0 ? <div><dt>Alternatives</dt><dd>{formatFeatureValue(entry.feature.alternatives)}</dd></div> : null}
        </dl>
      </details>
    </article>
  )
}
