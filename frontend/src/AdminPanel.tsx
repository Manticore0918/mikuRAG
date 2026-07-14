import { FormEvent, useEffect, useState } from 'react'

import { mutate, request, type AccessGrant, type KnowledgeBase, type User } from './api'
import DocumentPanel from './DocumentPanel'

function grantKey(knowledgeBaseId: string, userId: string) {
  return `${knowledgeBaseId}:${userId}`
}

async function fetchAdministrationData() {
  const [users, knowledgeBases] = await Promise.all([
    request<User[]>('/admin/users'),
    request<KnowledgeBase[]>('/admin/knowledge-bases'),
  ])
  const grantGroups = await Promise.all(
    knowledgeBases.map((knowledgeBase) =>
      request<AccessGrant[]>(`/admin/knowledge-bases/${knowledgeBase.id}/access`),
    ),
  )
  return { users, knowledgeBases, grantGroups }
}

export default function AdminPanel() {
  const [users, setUsers] = useState<User[]>([])
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [grants, setGrants] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [newUsername, setNewUsername] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [newUserIsAdmin, setNewUserIsAdmin] = useState(false)
  const [newKnowledgeName, setNewKnowledgeName] = useState('')
  const [newKnowledgeDescription, setNewKnowledgeDescription] = useState('')
  const [resetPasswords, setResetPasswords] = useState<Record<string, string>>({})

  async function load() {
    setError('')
    try {
      const data = await fetchAdministrationData()
      setUsers(data.users)
      setKnowledgeBases(data.knowledgeBases)
      setGrants(new Set(data.grantGroups.flat().map((grant) => grantKey(grant.knowledge_base_id, grant.user_id))))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to load administration data')
    }
  }

  useEffect(() => {
    let active = true
    void fetchAdministrationData()
      .then((data) => {
        if (!active) return
        setUsers(data.users)
        setKnowledgeBases(data.knowledgeBases)
        setGrants(new Set(data.grantGroups.flat().map((grant) => grantKey(grant.knowledge_base_id, grant.user_id))))
      })
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : 'Unable to load administration data')
      })
    return () => { active = false }
  }, [])

  function report(message: string) {
    setNotice(message)
    setError('')
  }

  async function createUser(event: FormEvent) {
    event.preventDefault()
    try {
      await mutate<User>('/admin/users', 'POST', { username: newUsername, password: newPassword, is_administrator: newUserIsAdmin })
      setNewUsername(''); setNewPassword(''); setNewUserIsAdmin(false)
      report('User created')
      await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to create User') }
  }

  async function updateUser(user: User, patch: Partial<Pick<User, 'is_enabled' | 'is_administrator'>>) {
    try {
      await mutate<User>(`/admin/users/${user.id}`, 'PATCH', patch)
      report('User updated')
      await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to update User') }
  }

  async function resetPassword(user: User) {
    const password = resetPasswords[user.id] || ''
    try {
      await mutate<void>(`/admin/users/${user.id}/password`, 'PUT', { password })
      setResetPasswords((current) => ({ ...current, [user.id]: '' }))
      report(`Password reset for ${user.username}; existing sessions were invalidated`)
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to reset password') }
  }

  async function createKnowledgeBase(event: FormEvent) {
    event.preventDefault()
    try {
      await mutate<KnowledgeBase>('/admin/knowledge-bases', 'POST', { name: newKnowledgeName, description: newKnowledgeDescription || null })
      setNewKnowledgeName(''); setNewKnowledgeDescription('')
      report('Knowledge Base created')
      await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to create Knowledge Base') }
  }

  async function updateKnowledgeBase(knowledgeBase: KnowledgeBase) {
    try {
      await mutate<KnowledgeBase>(`/admin/knowledge-bases/${knowledgeBase.id}`, 'PATCH', { name: knowledgeBase.name, description: knowledgeBase.description })
      report('Knowledge Base updated')
      await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to update Knowledge Base') }
  }

  async function deleteKnowledgeBase(knowledgeBase: KnowledgeBase) {
    if (!window.confirm(`Delete ${knowledgeBase.name}? It must contain no Documents; its access grants will also be removed.`)) return
    try {
      await mutate<void>(`/admin/knowledge-bases/${knowledgeBase.id}`, 'DELETE')
      report('Knowledge Base deleted')
      await load()
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to delete Knowledge Base') }
  }

  async function toggleGrant(knowledgeBaseId: string, userId: string, enabled: boolean) {
    const path = `/admin/knowledge-bases/${knowledgeBaseId}/access/${userId}`
    try {
      await mutate<AccessGrant | void>(path, enabled ? 'PUT' : 'DELETE')
      setGrants((current) => {
        const next = new Set(current)
        const key = grantKey(knowledgeBaseId, userId)
        if (enabled) next.add(key); else next.delete(key)
        return next
      })
      report(enabled ? 'Access granted' : 'Access revoked')
    } catch (reason) { setError(reason instanceof Error ? reason.message : 'Unable to change access') }
  }

  return (
    <main className="content admin-content">
      <div className="page-heading"><div><p className="eyebrow">Installation control</p><h1>Administration</h1><p>Manage identities, Knowledge Bases, and the boundary between them.</p></div></div>
      {error && <p className="form-error banner" role="alert">{error}</p>}
      {notice && <p className="form-notice banner" role="status">{notice}</p>}

      <section className="admin-section" aria-labelledby="users-title">
        <div className="section-title"><div><h2 id="users-title">Users</h2><p>{users.length} provisioned accounts</p></div></div>
        <form className="card create-form" onSubmit={createUser}>
          <label>Username<input value={newUsername} onChange={(event) => setNewUsername(event.target.value)} pattern="[A-Za-z0-9._-]+" minLength={3} required /></label>
          <label>Temporary password<input type="password" value={newPassword} onChange={(event) => setNewPassword(event.target.value)} minLength={12} required /></label>
          <label className="check-label"><input type="checkbox" checked={newUserIsAdmin} onChange={(event) => setNewUserIsAdmin(event.target.checked)} /> Administrator</label>
          <button className="primary">Create User</button>
        </form>
        <div className="table-wrap card"><table><thead><tr><th>User</th><th>Enabled</th><th>Administrator</th><th>Reset password</th></tr></thead><tbody>{users.map((user) => <tr key={user.id}><td><strong>{user.username}</strong><small>Created {new Date(user.created_at).toLocaleDateString()}</small></td><td><input aria-label={`Enable ${user.username}`} type="checkbox" checked={user.is_enabled} onChange={(event) => void updateUser(user, { is_enabled: event.target.checked })} /></td><td><input aria-label={`Administrator ${user.username}`} type="checkbox" checked={user.is_administrator} onChange={(event) => void updateUser(user, { is_administrator: event.target.checked })} /></td><td><div className="inline-action"><input aria-label={`New password for ${user.username}`} type="password" minLength={12} placeholder="12+ characters" value={resetPasswords[user.id] || ''} onChange={(event) => setResetPasswords((current) => ({ ...current, [user.id]: event.target.value }))} /><button disabled={(resetPasswords[user.id] || '').length < 12} onClick={() => void resetPassword(user)}>Reset</button></div></td></tr>)}</tbody></table></div>
      </section>

      <section className="admin-section" aria-labelledby="knowledge-title">
        <div className="section-title"><div><h2 id="knowledge-title">Knowledge Bases</h2><p>Create the access boundaries that will contain Documents and Conversations.</p></div></div>
        <form className="card create-form knowledge-create" onSubmit={createKnowledgeBase}>
          <label>Name<input value={newKnowledgeName} onChange={(event) => setNewKnowledgeName(event.target.value)} required /></label>
          <label>Description<input value={newKnowledgeDescription} onChange={(event) => setNewKnowledgeDescription(event.target.value)} /></label>
          <button className="primary">Create Knowledge Base</button>
        </form>
        <div className="admin-grid">
          {knowledgeBases.map((knowledgeBase, index) => <article className="card boundary-card" key={knowledgeBase.id}><div className="boundary-heading"><span className="knowledge-icon">{String(index + 1).padStart(2, '0')}</span><button className="danger-link" onClick={() => void deleteKnowledgeBase(knowledgeBase)}>Delete</button></div><label>Name<input value={knowledgeBase.name} onChange={(event) => setKnowledgeBases((current) => current.map((item) => item.id === knowledgeBase.id ? { ...item, name: event.target.value } : item))} /></label><label>Description<textarea value={knowledgeBase.description || ''} onChange={(event) => setKnowledgeBases((current) => current.map((item) => item.id === knowledgeBase.id ? { ...item, description: event.target.value } : item))} /></label><button onClick={() => void updateKnowledgeBase(knowledgeBase)}>Save details</button><div className="access-list"><h3>User access</h3>{users.filter((user) => !user.is_administrator).map((user) => <label className="check-label" key={user.id}><input type="checkbox" checked={grants.has(grantKey(knowledgeBase.id, user.id))} onChange={(event) => void toggleGrant(knowledgeBase.id, user.id, event.target.checked)} />{user.username}{!user.is_enabled && <span className="muted">disabled</span>}</label>)}{!users.some((user) => !user.is_administrator) && <p className="muted">Create a non-Administrator User to grant access.</p>}</div></article>)}
        </div>
      </section>

      <DocumentPanel
        knowledgeBases={knowledgeBases}
        onError={setError}
        onNotice={report}
      />
    </main>
  )
}
