import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  mutate,
  request,
  type DocumentRecord,
  type KnowledgeBase,
  type UploadSession,
} from './api'
import { sha256Hex, transferUpload, UploadPausedError } from './uploads'

type Props = {
  knowledgeBases: KnowledgeBase[]
  onError: (message: string) => void
  onNotice: (message: string) => void
}

type LocalTransfer = {
  file: File
  sessionId: string | null
  phase: 'hashing' | 'uploading' | 'paused'
}

const activeStatuses = new Set<DocumentRecord['status']>(['pending', 'processing', 'deleting'])
const acceptedTypes = '.pdf,.docx,.txt,.md,.markdown,.htm,.html,.py,.js,.jsx,.mjs,.cjs,.ts,.tsx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown,text/html,text/x-python,text/javascript,text/typescript'

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function progress(upload: UploadSession) {
  return Math.min(100, Math.floor((upload.received_bytes / upload.total_bytes) * 100))
}

export default function DocumentPanel({ knowledgeBases, onError, onNotice }: Props) {
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('')
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [uploadSessions, setUploadSessions] = useState<UploadSession[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [sourceUri, setSourceUri] = useState('')
  const [sourcePath, setSourcePath] = useState('')
  const [language, setLanguage] = useState('')
  const [tags, setTags] = useState('')
  const [localTransfer, setLocalTransfer] = useState<LocalTransfer | null>(null)
  const formRef = useRef<HTMLFormElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const pausedRef = useRef(false)
  const cancelledRef = useRef(new Set<string>())

  const knowledgeBaseId = knowledgeBases.some(
    (knowledgeBase) => knowledgeBase.id === selectedKnowledgeBaseId,
  ) ? selectedKnowledgeBaseId : (knowledgeBases[0]?.id || '')

  const load = useCallback(async (activeKnowledgeBaseId: string) => {
    if (!activeKnowledgeBaseId) {
      setDocuments([])
      setUploadSessions([])
      return
    }
    try {
      const [nextDocuments, nextUploads] = await Promise.all([
        request<DocumentRecord[]>(`/admin/knowledge-bases/${activeKnowledgeBaseId}/documents`),
        request<UploadSession[]>(`/admin/knowledge-bases/${activeKnowledgeBaseId}/document-uploads`),
      ])
      setDocuments(nextDocuments)
      setUploadSessions(nextUploads)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'Unable to load Documents')
    }
  }, [onError])

  useEffect(() => {
    let active = true
    if (!knowledgeBaseId) {
      queueMicrotask(() => {
        if (active) {
          setDocuments([])
          setUploadSessions([])
        }
      })
      return () => { active = false }
    }
    void Promise.all([
      request<DocumentRecord[]>(`/admin/knowledge-bases/${knowledgeBaseId}/documents`),
      request<UploadSession[]>(`/admin/knowledge-bases/${knowledgeBaseId}/document-uploads`),
    ]).then(([nextDocuments, nextUploads]) => {
      if (active) {
        setDocuments(nextDocuments)
        setUploadSessions(nextUploads)
      }
    }).catch((reason) => {
      if (active) onError(reason instanceof Error ? reason.message : 'Unable to load Documents')
    })
    return () => { active = false }
  }, [knowledgeBaseId, onError])

  const hasActiveJobs = useMemo(
    () => documents.some((document) => activeStatuses.has(document.status))
      || uploadSessions.some((upload) => upload.status === 'open'),
    [documents, uploadSessions],
  )

  useEffect(() => {
    if (!hasActiveJobs || !knowledgeBaseId) return
    const timer = window.setInterval(() => void load(knowledgeBaseId), 3000)
    return () => window.clearInterval(timer)
  }, [hasActiveJobs, knowledgeBaseId, load])

  const updateConfirmed = useCallback((sessionId: string, offset: number, expiresAt: string) => {
    setUploadSessions((current) => current.map((upload) => upload.id === sessionId
      ? { ...upload, received_bytes: offset, expires_at: expiresAt }
      : upload))
  }, [])

  async function runTransfer(session: UploadSession, selectedFile: File) {
    pausedRef.current = false
    const controller = new AbortController()
    abortRef.current = controller
    setLocalTransfer({ file: selectedFile, sessionId: session.id, phase: 'uploading' })
    try {
      await transferUpload(
        knowledgeBaseId,
        session,
        selectedFile,
        controller.signal,
        () => pausedRef.current,
        (offset, expiresAt) => updateConfirmed(session.id, offset, expiresAt),
      )
      setLocalTransfer(null)
      setFile(null)
      setSourceUri('')
      setSourcePath('')
      setLanguage('')
      setTags('')
      formRef.current?.reset()
      onNotice('Document uploaded and queued for Ingestion')
      await load(knowledgeBaseId)
    } catch (reason) {
      if (cancelledRef.current.has(session.id)) {
        setLocalTransfer(null)
        return
      }
      if (reason instanceof UploadPausedError || pausedRef.current) {
        setLocalTransfer({ file: selectedFile, sessionId: session.id, phase: 'paused' })
      } else {
        setLocalTransfer({ file: selectedFile, sessionId: session.id, phase: 'paused' })
        onError(reason instanceof Error ? reason.message : 'Unable to upload Document')
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null
    }
  }

  async function prepareFile(selectedFile: File, expected?: UploadSession) {
    if (!knowledgeBaseId) return
    setLocalTransfer({ file: selectedFile, sessionId: expected?.id || null, phase: 'hashing' })
    try {
      const digest = await sha256Hex(selectedFile)
      if (expected && (expected.total_bytes !== selectedFile.size || expected.declared_sha256 !== digest)) {
        throw new Error('The selected file does not match this Upload Session')
      }
      const session = expected
        ? await mutate<UploadSession>(
          `/admin/knowledge-bases/${knowledgeBaseId}/document-uploads/${expected.id}/resume`,
          'POST',
        )
        : await mutate<UploadSession>(
          `/admin/knowledge-bases/${knowledgeBaseId}/document-uploads`,
          'POST',
          {
            original_name: selectedFile.name,
            size_bytes: selectedFile.size,
            sha256: digest,
            ...(sourceUri.trim() ? { source_uri: sourceUri.trim() } : {}),
            ...(sourcePath.trim() ? { source_path: sourcePath.trim() } : {}),
            ...(language.trim() ? { language: language.trim() } : {}),
            tags: tags.split(',').map((tag) => tag.trim()).filter(Boolean),
          },
        )
      setUploadSessions((current) => [session, ...current.filter((item) => item.id !== session.id)])
      await runTransfer(session, selectedFile)
    } catch (reason) {
      setLocalTransfer(null)
      onError(reason instanceof Error ? reason.message : 'Unable to prepare Document upload')
    }
  }

  function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (file) void prepareFile(file)
  }

  function pauseUpload() {
    pausedRef.current = true
    abortRef.current?.abort()
    setLocalTransfer((current) => current ? { ...current, phase: 'paused' } : current)
  }

  async function resumeLocal(upload: UploadSession) {
    if (!localTransfer || localTransfer.sessionId !== upload.id) return
    try {
      const session = await mutate<UploadSession>(
        `/admin/knowledge-bases/${knowledgeBaseId}/document-uploads/${upload.id}/resume`,
        'POST',
      )
      await runTransfer(session, localTransfer.file)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'Unable to resume upload')
    }
  }

  async function cancelUpload(upload: UploadSession) {
    if (!window.confirm(`Cancel the upload of ${upload.original_name}?`)) return
    cancelledRef.current.add(upload.id)
    if (localTransfer?.sessionId === upload.id) pauseUpload()
    try {
      await mutate<void>(
        `/admin/knowledge-bases/${knowledgeBaseId}/document-uploads/${upload.id}`,
        'DELETE',
      )
      if (localTransfer?.sessionId === upload.id) setLocalTransfer(null)
      onNotice('Upload Session cancelled and temporary bytes removed')
      await load(knowledgeBaseId)
    } catch (reason) {
      cancelledRef.current.delete(upload.id)
      onError(reason instanceof Error ? reason.message : 'Unable to cancel upload')
    }
  }

  async function retryDocument(document: DocumentRecord) {
    try {
      await mutate<DocumentRecord>(`/admin/knowledge-bases/${knowledgeBaseId}/documents/${document.id}/retry`, 'POST')
      onNotice('Document queued for another Ingestion attempt')
      await load(knowledgeBaseId)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'Unable to retry Document')
    }
  }

  async function deleteDocument(document: DocumentRecord) {
    if (!window.confirm(`Delete ${document.original_name}? It will stop participating in new retrieval immediately.`)) return
    try {
      await mutate<void>(`/admin/knowledge-bases/${knowledgeBaseId}/documents/${document.id}`, 'DELETE')
      onNotice('Document excluded from retrieval and queued for deletion')
      await load(knowledgeBaseId)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'Unable to delete Document')
    }
  }

  const browserBusy = localTransfer !== null && localTransfer.phase !== 'paused'

  return (
    <section className="admin-section" aria-labelledby="documents-title">
      <div className="section-title document-section-title">
        <div>
          <h2 id="documents-title">Documents</h2>
          <p>Upload originals and inspect each Ingestion lifecycle.</p>
        </div>
        <label>Knowledge Base
          <select disabled={localTransfer !== null} value={knowledgeBaseId} onChange={(event) => setSelectedKnowledgeBaseId(event.target.value)}>
            {knowledgeBases.map((knowledgeBase) => <option key={knowledgeBase.id} value={knowledgeBase.id}>{knowledgeBase.name}</option>)}
          </select>
        </label>
      </div>

      <form ref={formRef} className="card document-upload" onSubmit={uploadDocument}>
        <div>
          <strong>Add a Document</strong>
          <p>PDF, DOCX, TXT, Markdown, HTML, Python, TypeScript, or JavaScript · 50 MB maximum · resumable upload</p>
          <p className="privacy-boundary">Extracted text leaves this Installation for embedding with Alibaba Model Studio.</p>
        </div>
        <input
          aria-label="Choose Document"
          type="file"
          accept={acceptedTypes}
          disabled={!knowledgeBaseId || browserBusy}
          onChange={(event) => setFile(event.target.files?.[0] || null)}
          required
        />
        <div className="source-metadata-grid">
          <label>Source URL <input type="url" placeholder="https://docs.example.com/guide" value={sourceUri} onChange={(event) => setSourceUri(event.target.value)} /></label>
          <label>Repository path <input placeholder="src/services/worker.py" value={sourcePath} onChange={(event) => setSourcePath(event.target.value)} /></label>
          <label>Language <input placeholder="Optional for text sources" value={language} onChange={(event) => setLanguage(event.target.value)} /></label>
          <label>Tags <input placeholder="operations, runbook" value={tags} onChange={(event) => setTags(event.target.value)} /></label>
        </div>
        <button className="primary" disabled={!file || !knowledgeBaseId || browserBusy}>
          {localTransfer?.phase === 'hashing' ? 'Verifying…' : browserBusy ? 'Uploading…' : 'Upload and ingest'}
        </button>
      </form>

      {!!uploadSessions.length && <div className="card upload-session-list" aria-labelledby="uploads-title">
        <div className="upload-session-heading">
          <div><strong id="uploads-title">Uploads in progress</strong><p>Checkpoints expire after 24 hours without activity.</p></div>
        </div>
        {uploadSessions.map((upload) => {
          const local = localTransfer?.sessionId === upload.id ? localTransfer : null
          const state = upload.status === 'failed' ? 'Failed' : local?.phase === 'uploading' ? 'Uploading' : local?.phase === 'hashing' ? 'Verifying' : local?.phase === 'paused' ? 'Paused' : 'Waiting for file'
          return <div className="upload-session" key={upload.id}>
            <div className="upload-session-copy">
              <strong>{upload.original_name}</strong>
              <small>{formatBytes(upload.received_bytes)} of {formatBytes(upload.total_bytes)} confirmed · {state}</small>
              <small>Started by {upload.initiated_by_username || 'a former Administrator'} · expires {new Date(upload.expires_at).toLocaleString()}</small>
              {upload.safe_error && <span className="document-error">{upload.safe_error}</span>}
            </div>
            <div className="upload-progress" aria-label={`${progress(upload)}% uploaded`}><span style={{ width: `${progress(upload)}%` }} /></div>
            <div className="document-actions">
              {local?.phase === 'uploading' && <button type="button" onClick={pauseUpload}>Pause</button>}
              {local?.phase === 'paused' && upload.status === 'open' && <button type="button" onClick={() => void resumeLocal(upload)}>Resume</button>}
              {!local && upload.status === 'open' && <label className="file-resume-button">Select file to resume<input type="file" accept={acceptedTypes} onChange={(event) => { const selected = event.target.files?.[0]; if (selected) void prepareFile(selected, upload) }} /></label>}
              <button type="button" className="danger-link" onClick={() => void cancelUpload(upload)}>{upload.status === 'failed' ? 'Dismiss' : 'Cancel'}</button>
            </div>
          </div>
        })}
      </div>}

      {!knowledgeBases.length && <div className="empty-state">Create a Knowledge Base before uploading Documents.</div>}
      {!!knowledgeBases.length && !documents.length && <div className="empty-state">No Documents in this Knowledge Base yet.</div>}
      {!!documents.length && <div className="table-wrap card"><table className="document-table"><thead><tr><th>Document</th><th>Ingestion</th><th>Size</th><th>Pages</th><th>Actions</th></tr></thead><tbody>
        {documents.map((document) => <tr key={document.id}>
          <td><strong>{document.original_name}</strong><small>{document.source_kind}{document.language ? ` · ${document.language}` : ''}{document.source_path ? ` · ${document.source_path}` : ''}</small>{document.tags.length > 0 && <small>Tags: {document.tags.join(', ')}</small>}{document.source_uri && <small><a href={document.source_uri} target="_blank" rel="noreferrer">Original source</a></small>}<small>Added {new Date(document.created_at).toLocaleString()}</small>{document.safe_error && <span className="document-error">{document.safe_error}</span>}</td>
          <td><span className={`status-badge status-${document.status}`}>{document.status}</span><small>{document.ingestion_stage} · {document.ingestion_progress}% · attempt {document.ingestion_attempts}</small><small>parser {document.parser_version || 'pending'} · chunker {document.chunking_version || 'pending'}</small>{document.ingestion_warnings.map((warning, index) => <span className="document-warning" key={`${warning.code}-${index}`}>{warning.message}{warning.page_number ? ` (page ${warning.page_number})` : ''}</span>)}</td>
          <td>{formatBytes(document.size_bytes)}</td>
          <td>{document.page_count ?? '—'}</td>
          <td><div className="document-actions">{document.status === 'failed' && <button onClick={() => void retryDocument(document)}>Retry</button>}<button className="danger-link" disabled={document.status === 'deleting'} onClick={() => void deleteDocument(document)}>{document.status === 'deleting' ? 'Deleting…' : 'Delete'}</button></div></td>
        </tr>)}
      </tbody></table></div>}
    </section>
  )
}
