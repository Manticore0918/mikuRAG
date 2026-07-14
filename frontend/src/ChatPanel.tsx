import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'

import {
  ApiError,
  mutate,
  request,
  streamMutation,
  type ChatMessage,
  type Citation,
  type Conversation,
  type ConversationDetail,
  type KnowledgeBase,
  type ServerEvent,
} from './api'

type ChatPanelProps = {
  knowledgeBases: KnowledgeBase[]
  initialConversationId?: string | null
  onError: (message: string) => void
}

type EventData = Record<string, unknown>

function GroundedContent({ content, citations }: { content: string; citations: Citation[] }) {
  const parts = content.split(/(\[\d+\])/g)
  return (
    <p className="message-content">
      {parts.map((part, index) => {
        const match = /^\[(\d+)\]$/.exec(part)
        if (!match) return <Fragment key={index}>{part}</Fragment>
        const citation = citations[Number(match[1]) - 1]
        return citation
          ? <a className="citation-marker" href={`#citation-${citation.id}`} key={index}>{part}</a>
          : <Fragment key={index}>{part}</Fragment>
      })}
    </p>
  )
}

function CitationCards({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null
  return (
    <div className="citation-list" aria-label="Citations">
      {citations.map((citation, index) => (
        <details className="citation-card" id={`citation-${citation.id}`} key={citation.id}>
          <summary><span>[{index + 1}]</span> {citation.document_name}</summary>
          <p>{citation.excerpt}</p>
          <div className="citation-meta">
            <span>{Object.entries(citation.locator).map(([key, value]) => `${key}: ${value}`).join(' · ') || 'Document excerpt'}</span>
            {citation.source_available && citation.source_url
              ? <a href={citation.source_url} rel="noreferrer" target="_blank">Open source</a>
              : <span>Source no longer available</span>}
          </div>
        </details>
      ))}
    </div>
  )
}

export default function ChatPanel({ knowledgeBases, initialConversationId, onError }: ChatPanelProps) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | null>(initialConversationId ?? null)
  const [active, setActive] = useState<ConversationDetail | null>(null)
  const [selectedKnowledgeBase, setSelectedKnowledgeBase] = useState(knowledgeBases[0]?.id ?? '')
  const [question, setQuestion] = useState('')
  const [stage, setStage] = useState('')
  const [sending, setSending] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  const activeSummary = useMemo(
    () => conversations.find((conversation) => conversation.id === activeId),
    [activeId, conversations],
  )

  async function refreshConversations() {
    const items = await request<Conversation[]>('/conversations')
    setConversations(items)
    return items
  }

  useEffect(() => {
    let mounted = true
    void request<Conversation[]>('/conversations')
      .then((items) => { if (mounted) setConversations(items) })
      .catch((error) => { if (mounted) onError(error instanceof Error ? error.message : 'Unable to load Conversations') })
    return () => { mounted = false }
  }, [onError])

  useEffect(() => {
    if (!activeId) return
    let mounted = true
    void request<ConversationDetail>(`/conversations/${activeId}`)
      .then((detail) => { if (mounted) setActive(detail) })
      .catch((error) => {
        if (mounted) onError(error instanceof Error ? error.message : 'Unable to load Conversation')
      })
    return () => { mounted = false }
  }, [activeId, onError])

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
  }, [active?.messages, stage])

  async function createConversation(knowledgeBaseId = selectedKnowledgeBase) {
    if (!knowledgeBaseId) throw new ApiError(422, 'Select a Knowledge Base first')
    const created = await mutate<Conversation>('/conversations', 'POST', {
      knowledge_base_id: knowledgeBaseId,
    })
    setConversations((current) => [created, ...current])
    setActiveId(created.id)
    setActive({ ...created, messages: [] })
    return created.id
  }

  function applyEvent(event: ServerEvent) {
    const data = event.data as EventData
    if (event.event === 'status') {
      setStage(typeof data.stage === 'string' ? data.stage : '')
      return
    }
    if (event.event === 'start') {
      const id = typeof data.message_id === 'string' ? data.message_id : `assistant-${Date.now()}`
      setActive((current) => current && ({
        ...current,
        messages: current.messages.map((message) => message.role === 'assistant' && message.status === 'streaming'
          ? { ...message, id }
          : message),
      }))
      return
    }
    if (event.event === 'delta') {
      const content = typeof data.content === 'string' ? data.content : ''
      setActive((current) => current && ({
        ...current,
        messages: current.messages.map((message) => message.role === 'assistant' && message.status === 'streaming'
          ? { ...message, content: message.content + content }
          : message),
      }))
      return
    }
    if (event.event === 'citations') {
      const citations = Array.isArray(data.items) ? data.items as Citation[] : []
      setActive((current) => current && ({
        ...current,
        messages: current.messages.map((message) => message.role === 'assistant' && message.status === 'streaming'
          ? { ...message, citations }
          : message),
      }))
      return
    }
    if (event.event === 'done') {
      setActive((current) => current && ({
        ...current,
        messages: current.messages.map((message) => message.role === 'assistant' && message.status === 'streaming'
          ? { ...message, status: 'complete' }
          : message),
      }))
      setStage('')
      return
    }
    if (event.event === 'error') {
      const message = typeof data.message === 'string' ? data.message : 'The answer could not be generated.'
      setActive((current) => current && ({
        ...current,
        messages: current.messages.map((item) => item.role === 'assistant' && item.status === 'streaming'
          ? { ...item, status: 'failed', content: message }
          : item),
      }))
      setStage('')
    }
  }

  async function sendQuestion(event: FormEvent) {
    event.preventDefault()
    const text = question.trim()
    if (!text || sending) return
    setSending(true)
    setQuestion('')
    try {
      const conversationId = activeId ?? await createConversation()
      const now = new Date().toISOString()
      const optimistic: ChatMessage[] = [
        { id: `user-${Date.now()}`, sequence: Date.now(), role: 'user', status: 'complete', content: text, created_at: now, citations: [] },
        { id: `assistant-${Date.now()}`, sequence: Date.now() + 1, role: 'assistant', status: 'streaming', content: '', created_at: now, citations: [] },
      ]
      setActive((current) => current && ({ ...current, messages: [...current.messages, ...optimistic] }))
      await streamMutation(`/conversations/${conversationId}/turns`, { question: text }, applyEvent)
      await refreshConversations()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to send question'
      onError(message)
      setActive((current) => current && ({
        ...current,
        messages: current.messages.map((item) => item.role === 'assistant' && item.status === 'streaming'
          ? { ...item, status: 'failed', content: message }
          : item),
      }))
    } finally {
      setSending(false)
      setStage('')
    }
  }

  async function removeConversation() {
    if (!activeId || !window.confirm('Delete this Conversation and all of its messages?')) return
    try {
      await mutate<void>(`/conversations/${activeId}`, 'DELETE')
      const remaining = conversations.filter((conversation) => conversation.id !== activeId)
      setConversations(remaining)
      setActiveId(remaining[0]?.id ?? null)
      if (!remaining.length) setActive(null)
    } catch (error) {
      onError(error instanceof Error ? error.message : 'Unable to delete Conversation')
    }
  }

  return (
    <main className="chat-shell">
      <aside className="conversation-sidebar" aria-label="Conversations">
        <div className="conversation-sidebar-heading"><div><p className="eyebrow">Grounded chat</p><h2>Conversations</h2></div></div>
        <div className="new-conversation">
          <label>Knowledge Base<select value={selectedKnowledgeBase} onChange={(event) => setSelectedKnowledgeBase(event.target.value)}>
            {knowledgeBases.map((knowledgeBase) => <option key={knowledgeBase.id} value={knowledgeBase.id}>{knowledgeBase.name}</option>)}
          </select></label>
          <button className="primary" disabled={!selectedKnowledgeBase} onClick={() => void createConversation().catch((error) => onError(error.message))}>New Conversation</button>
        </div>
        <div className="conversation-list">
          {conversations.map((conversation) => (
            <button className={conversation.id === activeId ? 'conversation-active' : ''} key={conversation.id} onClick={() => setActiveId(conversation.id)}>
              <strong>{conversation.title}</strong><span>{conversation.knowledge_base_name}</span>
            </button>
          ))}
          {!conversations.length && <p className="muted">No Conversations yet.</p>}
        </div>
      </aside>

      <section className="chat-main" aria-label="Grounded chat">
        <header className="chat-header">
          <div><h2>{active?.title ?? activeSummary?.title ?? 'Start a grounded Conversation'}</h2><p>{active?.knowledge_base_name ?? activeSummary?.knowledge_base_name ?? 'Choose one immutable Knowledge Base for this Conversation.'}</p></div>
          {activeId && <button className="danger-link" onClick={() => void removeConversation()}>Delete</button>}
        </header>
        <div className="message-feed" aria-live="polite">
          {!active?.messages.length && <div className="chat-empty"><span className="knowledge-icon">RAG</span><h2>Ask from your Documents</h2><p>Every factual answer must be supported by retrieved evidence. If the Documents do not support an answer, mikuRAG will say so.</p></div>}
          {active?.messages.map((message) => (
            <article className={`message message-${message.role}`} key={message.id}>
              <div className="message-label">{message.role === 'user' ? 'You' : 'mikuRAG'}{message.status === 'failed' && <span> · failed</span>}</div>
              <GroundedContent content={message.content} citations={message.citations} />
              <CitationCards citations={message.citations} />
            </article>
          ))}
          {stage && <p className="generation-stage">{stage === 'retrieving' ? 'Retrieving authorized evidence…' : stage === 'generating' ? 'Drafting from evidence…' : 'Validating every Citation…'}</p>}
          <div ref={endRef} />
        </div>
        <form className="chat-composer" onSubmit={(event) => void sendQuestion(event)}>
          <label htmlFor="chat-question">Ask a question</label>
          <div><textarea id="chat-question" maxLength={4000} placeholder="What do the Documents say about…" value={question} onChange={(event) => setQuestion(event.target.value)} disabled={sending || (!activeId && !selectedKnowledgeBase)} /><button className="primary" disabled={sending || !question.trim()} type="submit">{sending ? 'Working…' : 'Send'}</button></div>
          <p>Conversation history resolves references only. Fresh Document retrieval is required for every answer.</p>
        </form>
      </section>
    </main>
  )
}
