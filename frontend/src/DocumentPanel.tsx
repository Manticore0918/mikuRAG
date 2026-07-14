import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'

import {
  mutate,
  mutateForm,
  request,
  type DocumentRecord,
  type KnowledgeBase,
} from './api'

type Props = {
  knowledgeBases: KnowledgeBase[]
  onError: (message: string) => void
  onNotice: (message: string) => void
}

const activeStatuses = new Set<DocumentRecord['status']>(['pending', 'processing', 'deleting'])

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export default function DocumentPanel({ knowledgeBases, onError, onNotice }: Props) {
  const [selectedKnowledgeBaseId, setSelectedKnowledgeBaseId] = useState('')
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)

  const knowledgeBaseId = knowledgeBases.some(
    (knowledgeBase) => knowledgeBase.id === selectedKnowledgeBaseId,
  ) ? selectedKnowledgeBaseId : (knowledgeBases[0]?.id || '')

  const load = useCallback(async (activeKnowledgeBaseId: string) => {
    if (!activeKnowledgeBaseId) {
      setDocuments([])
      return
    }
    try {
      setDocuments(await request<DocumentRecord[]>(`/admin/knowledge-bases/${activeKnowledgeBaseId}/documents`))
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'Unable to load Documents')
    }
  }, [onError])

  useEffect(() => {
    if (!knowledgeBaseId) return
    let active = true
    void request<DocumentRecord[]>(`/admin/knowledge-bases/${knowledgeBaseId}/documents`)
      .then((result) => { if (active) setDocuments(result) })
      .catch((reason) => { if (active) onError(reason instanceof Error ? reason.message : 'Unable to load Documents') })
    return () => { active = false }
  }, [knowledgeBaseId, onError])

  const hasActiveJobs = useMemo(
    () => documents.some((document) => activeStatuses.has(document.status)),
    [documents],
  )

  useEffect(() => {
    if (!hasActiveJobs || !knowledgeBaseId) return
    const timer = window.setInterval(() => void load(knowledgeBaseId), 3000)
    return () => window.clearInterval(timer)
  }, [hasActiveJobs, knowledgeBaseId, load])

  async function uploadDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!file || !knowledgeBaseId) return
    const form = event.currentTarget
    const body = new FormData()
    body.append('file', file)
    setBusy(true)
    try {
      await mutateForm<DocumentRecord>(`/admin/knowledge-bases/${knowledgeBaseId}/documents`, 'POST', body)
      setFile(null)
      form.reset()
      onNotice('Document uploaded and queued for Ingestion')
      await load(knowledgeBaseId)
    } catch (reason) {
      onError(reason instanceof Error ? reason.message : 'Unable to upload Document')
    } finally {
      setBusy(false)
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

  return (
    <section className="admin-section" aria-labelledby="documents-title">
      <div className="section-title document-section-title">
        <div>
          <h2 id="documents-title">Documents</h2>
          <p>Upload originals and inspect each Ingestion lifecycle.</p>
        </div>
        <label>Knowledge Base
          <select value={knowledgeBaseId} onChange={(event) => setSelectedKnowledgeBaseId(event.target.value)}>
            {knowledgeBases.map((knowledgeBase) => <option key={knowledgeBase.id} value={knowledgeBase.id}>{knowledgeBase.name}</option>)}
          </select>
        </label>
      </div>

      <form className="card document-upload" onSubmit={uploadDocument}>
        <div>
          <strong>Add a Document</strong>
          <p>PDF, DOCX, TXT, or Markdown · 50 MB maximum · text extraction only</p>
          <p className="privacy-boundary">Extracted text leaves this Installation for embedding with Alibaba Model Studio.</p>
        </div>
        <input
          aria-label="Choose Document"
          type="file"
          accept=".pdf,.docx,.txt,.md,.markdown,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain,text/markdown"
          disabled={!knowledgeBaseId || busy}
          onChange={(event) => setFile(event.target.files?.[0] || null)}
          required
        />
        <button className="primary" disabled={!file || !knowledgeBaseId || busy}>{busy ? 'Uploading…' : 'Upload and ingest'}</button>
      </form>

      {!knowledgeBases.length && <div className="empty-state">Create a Knowledge Base before uploading Documents.</div>}
      {!!knowledgeBases.length && !documents.length && <div className="empty-state">No Documents in this Knowledge Base yet.</div>}
      {!!documents.length && <div className="table-wrap card"><table className="document-table"><thead><tr><th>Document</th><th>Status</th><th>Size</th><th>Pages</th><th>Actions</th></tr></thead><tbody>
        {documents.map((document) => <tr key={document.id}>
          <td><strong>{document.original_name}</strong><small>Added {new Date(document.created_at).toLocaleString()}</small>{document.safe_error && <span className="document-error">{document.safe_error}</span>}</td>
          <td><span className={`status-badge status-${document.status}`}>{document.status}</span></td>
          <td>{formatBytes(document.size_bytes)}</td>
          <td>{document.page_count ?? '—'}</td>
          <td><div className="document-actions">{document.status === 'failed' && <button onClick={() => void retryDocument(document)}>Retry</button>}<button className="danger-link" disabled={document.status === 'deleting'} onClick={() => void deleteDocument(document)}>{document.status === 'deleting' ? 'Deleting…' : 'Delete'}</button></div></td>
        </tr>)}
      </tbody></table></div>}
    </section>
  )
}
