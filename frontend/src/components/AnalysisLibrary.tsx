import { useCallback, useEffect, useState } from 'react'
import { Archive, FileDown, RefreshCw, Search, ShieldCheck, Trash2, Wrench } from 'lucide-react'
import { deleteAnalysis, listAnalyses, reconcileAnalysis } from '../api'
import type { AnalysisCatalogueItem } from '../types'

interface AnalysisLibraryProps {
  onOpen: (analysisId: string) => Promise<void>
}

function formatDuration(value: number | null): string {
  if (value === null) return 'Duration unavailable'
  const minutes = Math.floor(value / 60)
  const seconds = Math.round(value % 60).toString().padStart(2, '0')
  return `${minutes}:${seconds}`
}

function readableError(value: unknown): string {
  return value instanceof Error ? value.message : 'The analysis library could not complete this action.'
}

export function AnalysisLibrary({ onOpen }: AnalysisLibraryProps) {
  const [items, setItems] = useState<AnalysisCatalogueItem[]>([])
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('created_desc')
  const [health, setHealth] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string>()
  const [deleteTarget, setDeleteTarget] = useState<string>()
  const [error, setError] = useState<string>()

  const refresh = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError(undefined)
    try {
      const page = await listAnalyses({ search, sort, archiveHealth: health || undefined, limit: 100 })
      setItems(page.items)
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setLoading(false)
    }
  }, [health, search, sort])

  useEffect(() => { void refresh() }, [refresh])

  const repair = async (analysisId: string): Promise<void> => {
    setBusy(`repair-${analysisId}`)
    setError(undefined)
    try {
      await reconcileAnalysis(analysisId)
      await refresh()
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setBusy(undefined)
    }
  }

  const remove = async (analysisId: string): Promise<void> => {
    setBusy(`delete-${analysisId}`)
    setError(undefined)
    try {
      await deleteAnalysis(analysisId)
      setDeleteTarget(undefined)
      await refresh()
    } catch (caught) {
      setError(readableError(caught))
    } finally {
      setBusy(undefined)
    }
  }

  return (
    <main className="analysis-library" id="main-content">
      <section className="analysis-library__hero">
        <div>
          <span className="eyebrow">Persistent · local-only · explicit delete</span>
          <h1>Analysis Library</h1>
          <p>Completed analyses, source masters, planning artifacts, and downstream dependencies remain available across restarts until you explicitly delete them.</p>
        </div>
        <button type="button" className="button button--secondary" onClick={() => { void refresh() }} disabled={loading}>
          <RefreshCw aria-hidden="true" /> Refresh
        </button>
      </section>

      <section className="analysis-library__controls" aria-label="Analysis library filters">
        <label><span>Search</span><span className="analysis-library__search"><Search aria-hidden="true" /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Name or analysis ID" /></span></label>
        <label><span>Archive health</span><select value={health} onChange={(event) => setHealth(event.target.value)}><option value="">All</option><option value="healthy">Healthy</option><option value="degraded">Needs repair</option><option value="missing">Legacy missing</option></select></label>
        <label><span>Sort</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="created_desc">Newest first</option><option value="created_asc">Oldest first</option><option value="updated_desc">Recently updated</option><option value="display_name_asc">Name A–Z</option></select></label>
      </section>

      {error ? <div className="analysis-library__error" role="alert">{error}</div> : null}
      {loading ? <div className="analysis-library__empty" role="status">Loading the persistent local catalogue…</div> : null}
      {!loading && items.length === 0 ? <div className="analysis-library__empty"><Archive aria-hidden="true" /><h2>No matching analyses</h2><p>New completed analyses will be archived here automatically.</p></div> : null}

      <section className="analysis-library__grid" aria-label="Persistent analyses">
        {items.map((item) => (
          <article className="analysis-library__card" key={item.analysisId}>
            <header>
              <div><span className="eyebrow">{new Date(item.createdAt).toLocaleString()}</span><h2>{item.displayName}</h2></div>
              <span className={`analysis-health analysis-health--${item.archiveHealth}`}>{item.archiveHealth}</span>
            </header>
            <dl>
              <div><dt>Status</dt><dd>{item.status.replaceAll('_', ' ')}</dd></div>
              <div><dt>Duration</dt><dd>{formatDuration(item.durationSeconds)}</dd></div>
              <div><dt>Source</dt><dd>{item.retainedAudioAvailable ? 'Retained' : 'Unavailable'}</dd></div>
              <div><dt>Analysis</dt><dd>{item.analysisAvailable ? 'Available' : 'Unavailable'}</dd></div>
              <div><dt>StoryPlan</dt><dd>{item.storyPlanAvailable ? 'Ready' : 'Not available'}</dd></div>
              <div><dt>ShotPlan</dt><dd>{item.shotPlanAvailable ? 'Ready' : 'Not available'}</dd></div>
              <div><dt>Video jobs</dt><dd>{item.dependentVideoJobCount}</dd></div>
              <div><dt>Retention</dt><dd><ShieldCheck aria-hidden="true" /> Explicit delete only</dd></div>
            </dl>
            {item.legacyMissing ? <p className="analysis-library__warning">This is a truthful legacy reference. Canonical analysis artifacts are unavailable, but self-contained downstream video work remains protected.</p> : null}
            <footer>
              <button type="button" className="button button--primary" disabled={!item.analysisAvailable || busy !== undefined} onClick={() => { setBusy(`open-${item.analysisId}`); void onOpen(item.analysisId).catch((caught) => setError(readableError(caught))).finally(() => setBusy(undefined)) }}>Open analysis</button>
              {item.analysisAvailable ? <a className="button button--secondary" href={`/api/analyses/${encodeURIComponent(item.analysisId)}/export.json`} download><FileDown aria-hidden="true" /> Export</a> : null}
              {item.archiveHealth === 'degraded' ? <button type="button" className="button button--secondary" disabled={busy !== undefined} onClick={() => { void repair(item.analysisId) }}><Wrench aria-hidden="true" /> Repair index</button> : null}
              {deleteTarget === item.analysisId ? <><span className="analysis-library__confirm">Delete this analysis and its unshared private archive?</span><button type="button" className="button button--danger" disabled={!item.explicitDeleteEligible || busy !== undefined} onClick={() => { void remove(item.analysisId) }}>Confirm delete</button><button type="button" className="button button--secondary" onClick={() => setDeleteTarget(undefined)}>Keep it</button></> : <button type="button" className="button button--secondary" disabled={!item.explicitDeleteEligible || busy !== undefined} title={item.explicitDeleteEligible ? undefined : 'A dependent job needs an immutable snapshot first.'} onClick={() => setDeleteTarget(item.analysisId)}><Trash2 aria-hidden="true" /> Delete…</button>}
            </footer>
          </article>
        ))}
      </section>
    </main>
  )
}
