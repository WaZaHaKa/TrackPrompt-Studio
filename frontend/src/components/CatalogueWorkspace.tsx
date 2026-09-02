import {
  Archive,
  ChevronDown,
  ChevronUp,
  CirclePause,
  CirclePlay,
  FileAudio,
  Files,
  FolderOpen,
  History,
  ListMusic,
  RefreshCw,
  RotateCcw,
  Scissors,
  Search,
  ShieldCheck,
  Trash2,
  UploadCloud,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type DragEvent } from 'react'
import {
  appendUploadChunk,
  batchReportUrl,
  cancelSegmentationJob,
  cancelUploadSession,
  completeUploadSession,
  createCatalogueBatch,
  createCatalogueClient,
  createCatalogueProject,
  createUploadSession,
  editVirtualSegments,
  enqueueSegmentAnalyses,
  generateBatchReport,
  getSegmentationJob,
  getUploadSession,
  listBatchQueue,
  listCatalogueBatches,
  listCatalogueClients,
  listCatalogueProjects,
  listProjectAudit,
  listSourceAssets,
  listVirtualSegments,
  permanentlyDeleteCatalogueProject,
  reviewSegment,
  setBatchAction,
  startSegmentationJob,
  updateCatalogueClient,
  updateCatalogueProject,
} from '../api'
import type {
  AuditEvent,
  Capabilities,
  CatalogueBatch,
  CatalogueClient,
  CatalogueProject,
  QueueItem,
  RetentionPolicy,
  SegmentationJob,
  SegmentEdit,
  SourceAsset,
  UploadSession,
  VirtualSegment,
} from '../types'
import { Button, InlineNotice } from './ui'

const PERSISTENCE_KEY = 'trackprompt.catalogue.uploads.v1'
const VISIBLE_ITEMS = 50

type LocalUploadState = 'pending' | 'uploading' | 'paused' | 'verifying' | 'completed' | 'failed'

interface LocalUploadItem {
  id: string
  name: string
  size: number
  lastModified: number
  order: number
  file?: File
  sessionId?: string
  receivedBytes: number
  state: LocalUploadState
  error?: string
}

interface CatalogueWorkspaceProps {
  capabilities: Capabilities
}

function humanBytes(value: number): string {
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GiB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${Math.round(value / 1024)} KiB`
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : 'The local catalogue operation failed.'
}

function loadPersistedItems(): LocalUploadItem[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(PERSISTENCE_KEY) ?? '[]') as unknown
    if (!Array.isArray(parsed)) return []
    return parsed.flatMap((item): LocalUploadItem[] => {
      if (typeof item !== 'object' || item === null) return []
      const value = item as Record<string, unknown>
      if (typeof value.id !== 'string' || typeof value.name !== 'string' || typeof value.size !== 'number') return []
      return [{
        id: value.id,
        name: value.name,
        size: value.size,
        lastModified: typeof value.lastModified === 'number' ? value.lastModified : 0,
        order: typeof value.order === 'number' ? value.order : 0,
        sessionId: typeof value.sessionId === 'string' ? value.sessionId : undefined,
        receivedBytes: typeof value.receivedBytes === 'number' ? value.receivedBytes : 0,
        state: value.state === 'completed' ? 'completed' : value.state === 'failed' ? 'failed' : 'paused',
        error: typeof value.error === 'string' ? value.error : undefined,
      }]
    })
  } catch {
    return []
  }
}

function persistableItem(item: LocalUploadItem): Omit<LocalUploadItem, 'file'> {
  return {
    id: item.id,
    name: item.name,
    size: item.size,
    lastModified: item.lastModified,
    order: item.order,
    sessionId: item.sessionId,
    receivedBytes: item.receivedBytes,
    state: item.state,
    error: item.error,
  }
}

export function CatalogueWorkspace({ capabilities }: CatalogueWorkspaceProps) {
  const fileInput = useRef<HTMLInputElement>(null)
  const folderInput = useRef<HTMLInputElement>(null)
  const pausedIds = useRef(new Set<string>())
  const cancelledIds = useRef(new Set<string>())
  const [clients, setClients] = useState<CatalogueClient[]>([])
  const [projects, setProjects] = useState<CatalogueProject[]>([])
  const [batches, setBatches] = useState<CatalogueBatch[]>([])
  const [assets, setAssets] = useState<SourceAsset[]>([])
  const [segments, setSegments] = useState<Record<string, VirtualSegment[]>>({})
  const [scanJobs, setScanJobs] = useState<Record<string, SegmentationJob>>({})
  const [queue, setQueue] = useState<QueueItem[]>([])
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [selectedClientId, setSelectedClientId] = useState<string>()
  const [selectedProjectId, setSelectedProjectId] = useState<string>()
  const [selectedBatchId, setSelectedBatchId] = useState<string>()
  const [newClientName, setNewClientName] = useState('')
  const [newProjectName, setNewProjectName] = useState('')
  const [newBatchName, setNewBatchName] = useState('')
  const [retention, setRetention] = useState<RetentionPolicy>('archive')
  const [search, setSearch] = useState('')
  const [items, setItems] = useState<LocalUploadItem[]>(loadPersistedItems)
  const [itemPage, setItemPage] = useState(0)
  const [busy, setBusy] = useState<string>()
  const [error, setError] = useState<string>()
  const [notice, setNotice] = useState<string>()

  useEffect(() => {
    folderInput.current?.setAttribute('webkitdirectory', '')
    folderInput.current?.setAttribute('directory', '')
  }, [])

  useEffect(() => {
    localStorage.setItem(PERSISTENCE_KEY, JSON.stringify(items.map(persistableItem)))
  }, [items])

  const refreshClients = useCallback(async (): Promise<void> => {
    const page = await listCatalogueClients(search, 0, 200)
    setClients(page.items)
  }, [search])

  useEffect(() => {
    void refreshClients().catch((reason: unknown) => setError(readableError(reason)))
  }, [refreshClients])

  const selectClient = async (clientId: string): Promise<void> => {
    setSelectedClientId(clientId)
    setSelectedProjectId(undefined)
    setSelectedBatchId(undefined)
    setProjects(await listCatalogueProjects(clientId))
    setBatches([])
    setAssets([])
  }

  const selectProject = async (projectId: string): Promise<void> => {
    setSelectedProjectId(projectId)
    setSelectedBatchId(undefined)
    setBatches(await listCatalogueBatches(projectId))
    setAudit(await listProjectAudit(projectId))
    setAssets([])
  }

  const refreshBatch = useCallback(async (batchId: string): Promise<void> => {
    const [nextAssets, nextQueue] = await Promise.all([
      listSourceAssets(batchId),
      listBatchQueue(batchId),
    ])
    setAssets(nextAssets)
    setQueue(nextQueue)
    if (selectedProjectId) {
      const [nextBatches, nextAudit] = await Promise.all([
        listCatalogueBatches(selectedProjectId),
        listProjectAudit(selectedProjectId),
      ])
      setBatches(nextBatches)
      setAudit(nextAudit)
    }
  }, [selectedProjectId])

  const selectBatch = async (batchId: string): Promise<void> => {
    setSelectedBatchId(batchId)
    await refreshBatch(batchId)
  }

  useEffect(() => {
    if (!selectedBatchId || !queue.some((item) => ['queued', 'running'].includes(item.state))) return
    const timer = window.setInterval(() => {
      void refreshBatch(selectedBatchId).catch((reason: unknown) => setError(readableError(reason)))
    }, 1_500)
    return () => window.clearInterval(timer)
  }, [queue, refreshBatch, selectedBatchId])

  const addFiles = (files: File[]): void => {
    setError(undefined)
    setItems((current) => {
      const next = [...current]
      for (const file of files) {
        if (file.size <= 0) continue
        const resumable = next.find(
          (item) => !item.file && item.name === file.name && item.size === file.size && item.lastModified === file.lastModified,
        )
        if (resumable) {
          resumable.file = file
          resumable.state = resumable.sessionId ? 'paused' : 'pending'
          continue
        }
        next.push({
          id: crypto.randomUUID(),
          name: file.name,
          size: file.size,
          lastModified: file.lastModified,
          order: next.length,
          file,
          receivedBytes: 0,
          state: 'pending',
        })
      }
      return next.map((item, order) => ({ ...item, order }))
    })
  }

  const onDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault()
    addFiles(Array.from(event.dataTransfer.files))
  }

  const updateItem = (id: string, patch: Partial<LocalUploadItem>): void => {
    setItems((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item))
  }

  const uploadOne = async (itemId: string): Promise<void> => {
    const item = items.find((candidate) => candidate.id === itemId)
    if (!item?.file || !selectedBatchId || item.state === 'completed') return
    cancelledIds.current.delete(itemId)
    pausedIds.current.delete(itemId)
    updateItem(itemId, { state: 'uploading', error: undefined })
    try {
      let session: UploadSession
      if (item.sessionId) {
        session = await getUploadSession(item.sessionId)
      } else {
        session = await createUploadSession(selectedBatchId, item.file, item.order)
        updateItem(itemId, { sessionId: session.id, receivedBytes: session.receivedBytes })
      }
      while (session.receivedBytes < item.file.size) {
        if (pausedIds.current.has(itemId) || cancelledIds.current.has(itemId)) {
          updateItem(itemId, { state: 'paused', receivedBytes: session.receivedBytes })
          return
        }
        const receivedBytes = await appendUploadChunk(session, item.file)
        session = { ...session, receivedBytes }
        updateItem(itemId, { receivedBytes })
      }
      updateItem(itemId, { state: 'verifying' })
      await completeUploadSession(session.id)
      updateItem(itemId, { state: 'completed', receivedBytes: item.file.size })
    } catch (reason) {
      updateItem(itemId, { state: 'failed', error: readableError(reason) })
    }
  }

  const uploadAll = async (): Promise<void> => {
    if (!selectedBatchId) {
      setError('Create or select a batch before uploading.')
      return
    }
    const pending = items.filter((item) => item.file && item.state !== 'completed')
    let cursor = 0
    const worker = async (): Promise<void> => {
      while (cursor < pending.length) {
        const item = pending[cursor]
        cursor += 1
        if (!item) break
        await uploadOne(item.id)
      }
    }
    setBusy('upload')
    await Promise.all(
      Array.from({ length: Math.min(capabilities.limits.maxActiveUploads, pending.length) }, worker),
    )
    setBusy(undefined)
    await refreshBatch(selectedBatchId)
  }

  const cancelItem = async (item: LocalUploadItem): Promise<void> => {
    cancelledIds.current.add(item.id)
    if (item.sessionId) await cancelUploadSession(item.sessionId)
    setItems((current) => current.filter((candidate) => candidate.id !== item.id))
  }

  const moveItem = (index: number, direction: -1 | 1): void => {
    setItems((current) => {
      const next = [...current]
      const target = index + direction
      if (target < 0 || target >= next.length) return current
      const currentItem = next[index]
      const targetItem = next[target]
      if (!currentItem || !targetItem) return current
      next[index] = targetItem
      next[target] = currentItem
      return next.map((item, order) => ({ ...item, order }))
    })
  }

  const scanAsset = async (assetId: string): Promise<void> => {
    setBusy(`segment:${assetId}`)
    try {
      let job = await startSegmentationJob(assetId)
      setScanJobs((current) => ({ ...current, [assetId]: job }))
      while (['queued', 'running'].includes(job.state)) {
        await new Promise((resolve) => window.setTimeout(resolve, 500))
        job = await getSegmentationJob(job.id)
        setScanJobs((current) => ({ ...current, [assetId]: job }))
      }
      if (job.state === 'completed') {
        const result = await listVirtualSegments(assetId)
        setSegments((current) => ({ ...current, [assetId]: result }))
      } else if (job.state === 'failed') {
        throw new Error(`Long-form scan failed (${job.errorCode ?? 'unknown error'}).`)
      }
    } catch (reason) {
      setError(readableError(reason))
    } finally {
      setBusy(undefined)
    }
  }

  const cancelScan = async (assetId: string): Promise<void> => {
    const job = scanJobs[assetId]
    if (!job) return
    const cancelled = await cancelSegmentationJob(job.id)
    setScanJobs((current) => ({ ...current, [assetId]: cancelled }))
  }

  const loadSegments = async (assetId: string): Promise<void> => {
    setSegments((current) => ({ ...current, [assetId]: [] }))
    const result = await listVirtualSegments(assetId)
    setSegments((current) => ({ ...current, [assetId]: result }))
  }

  const toggleSegment = async (assetId: string, segment: VirtualSegment): Promise<void> => {
    const result = await reviewSegment(assetId, segment.id, segment.accepted ? 'reject' : 'accept')
    setSegments((current) => ({ ...current, [assetId]: result }))
  }

  const editSegments = async (assetId: string, edit: SegmentEdit): Promise<void> => {
    try {
      const result = await editVirtualSegments(assetId, edit)
      setSegments((current) => ({ ...current, [assetId]: result }))
      setNotice('Boundary revision saved and added to the project audit history.')
    } catch (reason) {
      setError(readableError(reason))
    }
  }

  const addSegment = (assetId: string): void => {
    const start = Number(window.prompt('New segment start in seconds'))
    const end = Number(window.prompt('New segment end in seconds'))
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      setError('Enter an ordered start and end time for the new virtual segment.')
      return
    }
    void editSegments(assetId, {
      operation: 'add', startSeconds: start, endSeconds: end, reason: 'User added boundary range',
    })
  }

  const analyzeSegments = async (assetId: string): Promise<void> => {
    await enqueueSegmentAnalyses(assetId)
    if (selectedBatchId) await setBatchAction(selectedBatchId, 'start')
    if (selectedBatchId) await refreshBatch(selectedBatchId)
  }

  const selectedClient = clients.find((item) => item.id === selectedClientId)
  const selectedProject = projects.find((item) => item.id === selectedProjectId)
  const selectedBatch = batches.find((item) => item.id === selectedBatchId)
  const totalSelectedBytes = items.reduce((sum, item) => sum + item.size, 0)
  const visibleItems = items.slice(itemPage * VISIBLE_ITEMS, (itemPage + 1) * VISIBLE_ITEMS)
  const storageWarning = capabilities.catalogue && totalSelectedBytes > capabilities.catalogue.freeStorageBytes - capabilities.limits.minimumFreeDiskBytes
  const queueSummary = useMemo(() => ({
    completed: queue.filter((item) => item.state === 'completed').length,
    failed: queue.filter((item) => item.state === 'failed').length,
    active: queue.filter((item) => item.state === 'running').length,
    pending: queue.filter((item) => ['queued', 'stored', 'paused'].includes(item.state)).length,
  }), [queue])

  return (
    <main className="catalogue-layout" id="main-content">
      <section className="catalogue-hero">
        <span className="eyebrow"><Archive aria-hidden="true" /> Professional local archive</span>
        <h1>Client projects, long sets,<br /><em>one private catalogue.</em></h1>
        <p>Sources stay local and are stored once. Virtual tracks reference reviewed time ranges; child analysis remains bounded.</p>
        <div className="catalogue-capabilities">
          <span>Long-form: {(capabilities.limits.maxLongformDurationSeconds / 3600).toFixed(0)} hours</span>
          <span>Chunk: {humanBytes(capabilities.limits.uploadChunkBytes)}</span>
          <span>Uploads: {capabilities.limits.maxActiveUploads} active</span>
          <span>GPU: {capabilities.limits.maxActiveGpuTasks} heavy task</span>
        </div>
      </section>

      {error ? <InlineNotice tone="error">{error}</InlineNotice> : null}
      {notice ? <InlineNotice tone="success">{notice}</InlineNotice> : null}

      <section className="catalogue-grid" aria-label="Client catalogue">
        <article className="catalogue-card">
          <header><Search aria-hidden="true" /><div><h2>Clients</h2><p>Searchable private client list</p></div></header>
          <div className="catalogue-inline-form">
            <input aria-label="Search clients" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search clients" />
            <Button onClick={() => void refreshClients()}><RefreshCw aria-hidden="true" /> Refresh</Button>
          </div>
          <div className="catalogue-inline-form">
            <input aria-label="New client name" value={newClientName} onChange={(event) => setNewClientName(event.target.value)} placeholder="New client" />
            <Button disabled={!newClientName.trim()} onClick={() => void (async () => {
              const created = await createCatalogueClient(newClientName.trim())
              setNewClientName('')
              await refreshClients()
              await selectClient(created.id)
            })().catch((reason: unknown) => setError(readableError(reason)))}>Create</Button>
          </div>
          <div className="catalogue-list" role="listbox" aria-label="Clients">
            {clients.map((client) => (
              <button key={client.id} className={client.id === selectedClientId ? 'is-selected' : ''} onClick={() => void selectClient(client.id)}>
                <strong>{client.displayName}</strong><small>{client.projectCount} projects</small>
              </button>
            ))}
          </div>
          {selectedClient ? <div className="catalogue-record-actions">
            <Button onClick={() => void updateCatalogueClient(selectedClient.id, { archived: !selectedClient.archived }).then(refreshClients)}>{selectedClient.archived ? 'Restore client' : 'Archive client'}</Button>
            <Button onClick={() => {
              const displayName = window.prompt('Client display name', selectedClient.displayName)?.trim()
              if (displayName && displayName !== selectedClient.displayName) void updateCatalogueClient(selectedClient.id, { displayName }).then(refreshClients)
            }}>Rename</Button>
          </div> : null}
        </article>

        <article className="catalogue-card">
          <header><FolderOpen aria-hidden="true" /><div><h2>Projects</h2><p>{selectedClient?.displayName ?? 'Select a client'}</p></div></header>
          <div className="catalogue-inline-form catalogue-inline-form--stack">
            <input aria-label="New project name" value={newProjectName} onChange={(event) => setNewProjectName(event.target.value)} placeholder="New project" />
            <select aria-label="Retention policy" value={retention} onChange={(event) => setRetention(event.target.value as RetentionPolicy)}>
              <option value="archive">Archive until explicit deletion</option>
              <option value="temporary">Temporary project workflow</option>
              <option value="custom">Custom retention date via API</option>
            </select>
            <Button disabled={!selectedClientId || !newProjectName.trim() || retention === 'custom'} onClick={() => void (async () => {
              if (!selectedClientId) return
              const created = await createCatalogueProject(selectedClientId, newProjectName.trim(), retention)
              setNewProjectName('')
              setProjects(await listCatalogueProjects(selectedClientId))
              await selectProject(created.id)
            })().catch((reason: unknown) => setError(readableError(reason)))}>Create project</Button>
          </div>
          <div className="catalogue-list" role="listbox" aria-label="Projects">
            {projects.map((project) => (
              <button key={project.id} className={project.id === selectedProjectId ? 'is-selected' : ''} onClick={() => void selectProject(project.id)}>
                <strong>{project.name}</strong><small>{project.retentionPolicy} · {humanBytes(project.storageBytes)}</small>
              </button>
            ))}
          </div>
          {selectedProject ? <div className="catalogue-record-actions">
            <Button onClick={() => void updateCatalogueProject(selectedProject.id, { archived: !selectedProject.archivedAt }).then(async () => {
              if (selectedClientId) setProjects(await listCatalogueProjects(selectedClientId))
            })}>{selectedProject.archivedAt ? 'Restore project' : 'Archive project'}</Button>
            <Button onClick={() => {
              const name = window.prompt('Project name', selectedProject.name)?.trim()
              if (name && name !== selectedProject.name) void updateCatalogueProject(selectedProject.id, { name }).then(async () => {
                if (selectedClientId) setProjects(await listCatalogueProjects(selectedClientId))
              })
            }}>Rename</Button>
            <Button onClick={() => {
              if (!window.confirm(`Permanently delete ${selectedProject.name}, its audit journal, artifacts, and unshared source bytes?`)) return
              void permanentlyDeleteCatalogueProject(selectedProject.id).then(async () => {
                setSelectedProjectId(undefined)
                setSelectedBatchId(undefined)
                setBatches([])
                setAssets([])
                setAudit([])
                if (selectedClientId) setProjects(await listCatalogueProjects(selectedClientId))
                setNotice('Project permanently deleted. A content-free deletion tombstone remains in the catalogue database.')
              }).catch((reason: unknown) => setError(readableError(reason)))
            }}>Delete permanently</Button>
          </div> : null}
        </article>

        <article className="catalogue-card">
          <header><ListMusic aria-hidden="true" /><div><h2>Batches / sets</h2><p>{selectedProject?.name ?? 'Select a project'}</p></div></header>
          <div className="catalogue-inline-form">
            <input aria-label="New batch name" value={newBatchName} onChange={(event) => setNewBatchName(event.target.value)} placeholder="New set or album" />
            <Button disabled={!selectedProjectId || !newBatchName.trim()} onClick={() => void (async () => {
              if (!selectedProjectId) return
              const created = await createCatalogueBatch(selectedProjectId, newBatchName.trim())
              setNewBatchName('')
              setBatches(await listCatalogueBatches(selectedProjectId))
              await selectBatch(created.id)
            })().catch((reason: unknown) => setError(readableError(reason)))}>Create</Button>
          </div>
          <div className="catalogue-list" role="listbox" aria-label="Batches">
            {batches.map((batch) => (
              <button key={batch.id} className={batch.id === selectedBatchId ? 'is-selected' : ''} onClick={() => void selectBatch(batch.id)}>
                <strong>{batch.name}</strong><small>{batch.state} · {batch.progress}% · {batch.itemTotal} sources</small>
              </button>
            ))}
          </div>
        </article>
      </section>

      <section className="workspace-card bulk-workspace" aria-labelledby="bulk-heading">
        <header className="workspace-heading"><UploadCloud aria-hidden="true" /><div><h2 id="bulk-heading">Bulk ingest</h2><p>{selectedBatch?.name ?? 'Select a batch'} · no arbitrary file-count cap</p></div></header>
        <div className="bulk-dropzone" onDragOver={(event) => event.preventDefault()} onDrop={onDrop}>
          <Files aria-hidden="true" /><strong>Drop files or folders here</strong>
          <span>Items register incrementally; at most {capabilities.limits.maxActiveUploads} upload at once.</span>
          <div><Button onClick={() => fileInput.current?.click()}>Choose files</Button><Button onClick={() => folderInput.current?.click()}>Choose folder</Button></div>
          <input ref={fileInput} aria-label="Bulk audio files" className="visually-hidden" type="file" multiple accept="audio/*,.wav,.flac,.mp3,.m4a,.aac,.ogg" onChange={(event) => addFiles(Array.from(event.target.files ?? []))} />
          <input ref={folderInput} aria-label="Bulk audio folder" className="visually-hidden" type="file" multiple onChange={(event) => addFiles(Array.from(event.target.files ?? []))} />
        </div>
        <div className="bulk-summary">
          <span>{items.length} items</span><span>{humanBytes(totalSelectedBytes)}</span>
          <span>{humanBytes(capabilities.catalogue?.freeStorageBytes ?? 0)} free</span>
          <Button variant="primary" busy={busy === 'upload'} disabled={!selectedBatchId || !items.some((item) => item.file && item.state !== 'completed') || Boolean(storageWarning)} onClick={() => void uploadAll()}>Upload queue</Button>
        </div>
        {storageWarning ? <InlineNotice tone="error">This selection would cross the configured free-disk reserve. Remove items or free local storage.</InlineNotice> : null}
        {items.some((item) => !item.file && item.state !== 'completed') ? <InlineNotice tone="warning">Reselect matching local files to resume sessions restored after reload.</InlineNotice> : null}
        <div className="bulk-list" aria-label="Bulk upload queue">
          {visibleItems.map((item, visibleIndex) => {
            const index = itemPage * VISIBLE_ITEMS + visibleIndex
            const percent = item.size ? Math.round(item.receivedBytes * 100 / item.size) : 0
            return (
              <article key={item.id} className="bulk-row">
                <FileAudio aria-hidden="true" />
                <div><strong>{item.name}</strong><small>{humanBytes(item.size)} · {item.state}{item.error ? ` · ${item.error}` : ''}</small><progress max={100} value={percent}>{percent}%</progress></div>
                <span>{percent}%</span>
                <div className="bulk-row__actions">
                  <button aria-label={`Move ${item.name} up`} disabled={index === 0} onClick={() => moveItem(index, -1)}><ChevronUp /></button>
                  <button aria-label={`Move ${item.name} down`} disabled={index === items.length - 1} onClick={() => moveItem(index, 1)}><ChevronDown /></button>
                  {item.state === 'uploading' ? <button aria-label={`Pause ${item.name}`} onClick={() => { pausedIds.current.add(item.id); updateItem(item.id, { state: 'paused' }) }}><CirclePause /></button> : null}
                  {item.file && ['paused', 'failed'].includes(item.state) ? <button aria-label={`Resume ${item.name}`} onClick={() => void uploadOne(item.id)}><CirclePlay /></button> : null}
                  <button aria-label={`Remove ${item.name}`} onClick={() => void cancelItem(item)}><Trash2 /></button>
                </div>
              </article>
            )
          })}
        </div>
        {items.length > VISIBLE_ITEMS ? <nav className="pagination" aria-label="Upload pages"><Button disabled={itemPage === 0} onClick={() => setItemPage((page) => page - 1)}>Previous</Button><span>Page {itemPage + 1} of {Math.ceil(items.length / VISIBLE_ITEMS)}</span><Button disabled={(itemPage + 1) * VISIBLE_ITEMS >= items.length} onClick={() => setItemPage((page) => page + 1)}>Next</Button></nav> : null}
      </section>

      <section className="workspace-card" aria-labelledby="segments-heading">
        <header className="workspace-heading"><Scissors aria-hidden="true" /><div><h2 id="segments-heading">Long-form segmentation review</h2><p>Transition bands are mixed evidence; stable cores drive child analysis.</p></div></header>
        <div className="asset-list">
          {assets.map((asset) => {
            const scanJob = scanJobs[asset.id]
            return (
            <article key={asset.id} className="asset-card">
              <div className="asset-card__summary"><div><strong>{asset.displayName}</strong><small>{(asset.durationSeconds / 60).toFixed(1)} min · {asset.segmentationState} · stored once</small>{scanJob ? <small>{scanJob.stage} · {scanJob.progress}% · {scanJob.candidateCount} candidates</small> : null}</div><div><Button busy={busy === `segment:${asset.id}`} onClick={() => void scanAsset(asset.id)}>Detect tracks</Button>{scanJob && ['queued', 'running'].includes(scanJob.state) ? <Button onClick={() => void cancelScan(asset.id)}>Cancel scan</Button> : null}<Button onClick={() => void loadSegments(asset.id)}>Review</Button></div></div>
              {scanJob ? <progress className="scan-progress" max={100} value={scanJob.progress}>{scanJob.progress}%</progress> : null}
              {(segments[asset.id] ?? []).length > 0 ? <div className="segment-list">
                <div className="segment-toolbar">
                  <Button onClick={() => addSegment(asset.id)}>Add range</Button>
                  <Button onClick={() => void editSegments(asset.id, { operation: 'restore', reason: 'Restore detected boundaries' })}>Restore detected</Button>
                </div>
                {(segments[asset.id] ?? []).map((segment, index, assetSegments) => (
                  <article key={segment.id} className="segment-row">
                    <input aria-label={`Accept ${segment.label}`} type="checkbox" checked={segment.accepted} onChange={() => void toggleSegment(asset.id, segment)} />
                    <span className="segment-row__fields">
                      <input
                        aria-label={`Label for segment ${index + 1}`}
                        defaultValue={segment.label}
                        onBlur={(event) => event.target.value !== segment.label && void editSegments(asset.id, {
                          operation: 'rename', segmentId: segment.id, label: event.target.value, reason: 'User renamed virtual segment',
                        })}
                      />
                      <span className="segment-range-fields">
                        <label>Start <input aria-label={`Start for ${segment.label}`} type="number" min={0} step="0.1" defaultValue={segment.startSeconds} onBlur={(event) => {
                          const start = Number(event.target.value)
                          if (Number.isFinite(start) && start !== segment.startSeconds) void editSegments(asset.id, {
                            operation: 'move', segmentId: segment.id, startSeconds: start, endSeconds: segment.endSeconds, reason: 'User moved segment start',
                          })
                        }} /></label>
                        <label>End <input aria-label={`End for ${segment.label}`} type="number" min={0} step="0.1" defaultValue={segment.endSeconds} onBlur={(event) => {
                          const end = Number(event.target.value)
                          if (Number.isFinite(end) && end !== segment.endSeconds) void editSegments(asset.id, {
                            operation: 'move', segmentId: segment.id, startSeconds: segment.startSeconds, endSeconds: end, reason: 'User moved segment end',
                          })
                        }} /></label>
                      </span>
                      <small>stable {(segment.stableCoreEndSeconds - segment.stableCoreStartSeconds).toFixed(1)} s · revision {segment.revision}</small>
                    </span>
                    <span className={`confidence confidence--${segment.confidence}`}>{segment.confidence}</span>
                    <span>{segment.transitionType.replaceAll('_', ' ')}</span>
                    <span className="segment-row__actions">
                      <button aria-label={`Split ${segment.label}`} onClick={() => void editSegments(asset.id, {
                        operation: 'split', segmentId: segment.id, atSeconds: (segment.startSeconds + segment.endSeconds) / 2, reason: 'User split virtual segment',
                      })}>Split</button>
                      {index + 1 < assetSegments.length ? <button aria-label={`Merge ${segment.label} with next`} onClick={() => void editSegments(asset.id, {
                        operation: 'merge', segmentId: segment.id, adjacentSegmentId: assetSegments[index + 1]?.id, reason: 'User merged adjacent virtual segments',
                      })}>Merge next</button> : null}
                      <button aria-label={`Delete ${segment.label}`} onClick={() => void editSegments(asset.id, {
                        operation: 'delete', segmentId: segment.id, reason: 'User deleted virtual segment',
                      })}><Trash2 /></button>
                    </span>
                  </article>
                ))}
                <Button variant="primary" disabled={!(segments[asset.id] ?? []).some((segment) => segment.accepted)} onClick={() => void analyzeSegments(asset.id)}>Analyze accepted tracks</Button>
              </div> : null}
            </article>
            )
          })}
          {selectedBatchId && assets.length === 0 ? <p className="muted">Completed uploads appear here after ffprobe validation.</p> : null}
        </div>
      </section>

      <section className="catalogue-grid catalogue-grid--two" aria-label="Batch status and provenance">
        <article className="catalogue-card batch-monitor">
          <header><ListMusic aria-hidden="true" /><div><h2>Batch progress</h2><p>{queueSummary.completed} complete · {queueSummary.active} active · {queueSummary.pending} pending · {queueSummary.failed} failed</p></div></header>
          <progress max={Math.max(queue.length, 1)} value={queueSummary.completed + queueSummary.failed}>{selectedBatch?.progress ?? 0}%</progress>
          <div className="batch-actions">
            <Button disabled={!selectedBatchId} onClick={() => selectedBatchId && void setBatchAction(selectedBatchId, 'start').then(() => refreshBatch(selectedBatchId))}><CirclePlay /> Start</Button>
            <Button disabled={!selectedBatchId} onClick={() => selectedBatchId && void setBatchAction(selectedBatchId, 'pause').then(() => refreshBatch(selectedBatchId))}><CirclePause /> Pause</Button>
            <Button disabled={!selectedBatchId} onClick={() => selectedBatchId && void setBatchAction(selectedBatchId, 'resume').then(() => refreshBatch(selectedBatchId))}><RotateCcw /> Resume</Button>
            <Button disabled={!selectedBatchId} onClick={() => selectedBatchId && void setBatchAction(selectedBatchId, 'retry-failed').then(() => refreshBatch(selectedBatchId))}>Retry failed</Button>
            <Button disabled={!selectedBatchId} onClick={() => selectedBatchId && void setBatchAction(selectedBatchId, 'cancel').then(() => refreshBatch(selectedBatchId))}><Trash2 /> Cancel batch</Button>
          </div>
          <div className="queue-list">{queue.slice(0, 100).map((item) => <div key={item.id}><span>{item.state}</span><small>attempt {item.attempt}{item.failureReason ? ` · ${item.failureReason}` : ''}</small></div>)}</div>
        </article>

        <article className="catalogue-card report-card">
          <header><ShieldCheck aria-hidden="true" /><div><h2>Mastering comparison</h2><p>Measurements, set medians, deviations, and withheld counts</p></div></header>
          <p>Reports compare reviewed child tracks. They do not prescribe universal loudness, tonal-balance, or stereo targets; sample peak is not true peak.</p>
          <Button variant="primary" disabled={!selectedBatchId || queueSummary.completed === 0} onClick={() => selectedBatchId && void generateBatchReport(selectedBatchId).then(() => setNotice('JSON, Markdown, and CSV report revisions were archived.')).catch((reason: unknown) => setError(readableError(reason)))}>Generate archived report</Button>
          {selectedBatchId ? <div className="report-links"><a href={batchReportUrl(selectedBatchId, 'json')}>JSON</a><a href={batchReportUrl(selectedBatchId, 'md')}>Markdown</a><a href={batchReportUrl(selectedBatchId, 'csv')}>CSV</a></div> : null}
        </article>
      </section>

      <section className="workspace-card audit-workspace" aria-labelledby="audit-heading">
        <header className="workspace-heading"><History aria-hidden="true" /><div><h2 id="audit-heading">Audit and provenance</h2><p>Append-only hash-chained project events; tamper evidence, not operator identity proof.</p></div></header>
        <div className="audit-list">{audit.slice(-100).reverse().map((event) => <article key={event.eventId}><span>#{event.sequence}</span><div><strong>{event.eventType}</strong><small>{new Date(event.timestamp).toLocaleString()} · {event.entityType}</small></div><code>{event.eventHash.slice(0, 12)}</code></article>)}</div>
      </section>
    </main>
  )
}
