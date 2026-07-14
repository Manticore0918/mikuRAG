import { FormEvent, useState } from 'react'

import { mutate, type User } from './api'

type LoginPageProps = {
  onAuthenticated: (user: User) => void
}

export default function LoginPage({ onAuthenticated }: LoginPageProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      const user = await mutate<User>('/auth/login', 'POST', { username, password })
      onAuthenticated(user)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Unable to sign in')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="auth-layout">
      <section className="auth-story">
        <div className="brand"><span className="brand-mark">μ</span> mikuRAG</div>
        <div>
          <p className="eyebrow">Private knowledge, grounded answers</p>
          <h1>Ask what your organization knows.</h1>
          <p className="intro">
            Every answer stays inside an authorized Knowledge Base and points back to evidence.
          </p>
        </div>
        <p className="privacy-note">
          Conversations are visible to this Installation’s Administrators for support,
          security, and compliance.
        </p>
      </section>
      <section className="auth-panel" aria-labelledby="sign-in-title">
        <form className="card login-card" onSubmit={submit}>
          <p className="eyebrow">Administrator-provisioned access</p>
          <h2 id="sign-in-title">Sign in to mikuRAG</h2>
          <label>Username<input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></label>
          <label>Password<input type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} required /></label>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button className="primary" disabled={submitting}>{submitting ? 'Signing in…' : 'Sign in'}</button>
        </form>
      </section>
    </main>
  )
}

