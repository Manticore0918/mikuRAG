import { useEffect, useState } from 'react'

import AdminPanel from './AdminPanel'
import ChatPanel from './ChatPanel'
import { mutate, request, type KnowledgeBase, type User } from './api'

type WorkspaceProps = {
  user: User
  onSignedOut: () => void
}

export default function Workspace({ user, onSignedOut }: WorkspaceProps) {
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [view, setView] = useState<'knowledge' | 'chat' | 'admin'>('knowledge')
  const [initialConversationId, setInitialConversationId] = useState<string | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    void request<KnowledgeBase[]>('/knowledge-bases')
      .then((items) => { if (active) setKnowledgeBases(items) })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load Knowledge Bases')
      })
    return () => { active = false }
  }, [])

  async function showKnowledge() {
    setView('knowledge')
    try {
      setKnowledgeBases(await request<KnowledgeBase[]>('/knowledge-bases'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load Knowledge Bases')
    }
  }

  async function signOut() {
    await mutate<void>('/auth/logout', 'POST')
    onSignedOut()
  }

  async function startConversation(knowledgeBaseId: string) {
    try {
      const conversation = await mutate<{ id: string }>('/conversations', 'POST', { knowledge_base_id: knowledgeBaseId })
      setInitialConversationId(conversation.id)
      setView('chat')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to start Conversation')
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand"><span className="brand-mark">μ</span> mikuRAG</div>
        <nav aria-label="Primary navigation">
          <button className={view === 'knowledge' ? 'nav-active' : ''} onClick={() => void showKnowledge()}>Knowledge</button>
          <button className={view === 'chat' ? 'nav-active' : ''} onClick={() => { setInitialConversationId(null); setView('chat') }}>Conversations</button>
          {user.is_administrator && <button className={view === 'admin' ? 'nav-active' : ''} onClick={() => setView('admin')}>Administration</button>}
        </nav>
        <div className="account"><span>{user.username}</span>{user.is_administrator && <span className="badge">Administrator</span>}<button onClick={() => void signOut()}>Sign out</button></div>
      </header>

      {view === 'admin' && user.is_administrator ? (
        <AdminPanel />
      ) : view === 'chat' ? (
        <><div className="chat-error-wrap">{error && <p className="form-error" role="alert">{error}</p>}</div><ChatPanel knowledgeBases={knowledgeBases} initialConversationId={initialConversationId} onError={setError} /></>
      ) : (
        <main className="content">
          <div className="page-heading"><div><p className="eyebrow">Authorized knowledge</p><h1>Your Knowledge Bases</h1><p>Select one Knowledge Base when starting a Conversation.</p></div><span className="count">{knowledgeBases.length} available</span></div>
          {error && <p className="form-error" role="alert">{error}</p>}
          <div className="knowledge-grid">
            {knowledgeBases.map((knowledgeBase) => <article className="card knowledge-card" key={knowledgeBase.id}><span className="knowledge-icon">K</span><div><h2>{knowledgeBase.name}</h2><p>{knowledgeBase.description || 'No description provided.'}</p></div><button onClick={() => void startConversation(knowledgeBase.id)}>Start Conversation</button></article>)}
            {!knowledgeBases.length && <div className="empty-state"><h2>No Knowledge Bases assigned</h2><p>An Administrator can grant access from the Administration area.</p></div>}
          </div>
        </main>
      )}
    </div>
  )
}
