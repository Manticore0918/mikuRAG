import { useEffect, useState } from 'react'

import { ApiError, request, type User } from './api'
import LoginPage from './LoginPage'
import Workspace from './Workspace'

type SessionState =
  | { status: 'loading' }
  | { status: 'guest' }
  | { status: 'authenticated'; user: User }

export default function App() {
  const [session, setSession] = useState<SessionState>({ status: 'loading' })

  useEffect(() => {
    const controller = new AbortController()
    request<User>('/auth/me', { signal: controller.signal })
      .then((user) => setSession({ status: 'authenticated', user }))
      .catch((error) => {
        if (error instanceof ApiError && error.status === 401) setSession({ status: 'guest' })
        else if ((error as Error).name !== 'AbortError') setSession({ status: 'guest' })
      })
    return () => controller.abort()
  }, [])

  if (session.status === 'loading') {
    return <main className="loading-screen"><div className="brand"><span className="brand-mark">μ</span> mikuRAG</div><p>Securing your workspace…</p></main>
  }
  if (session.status === 'guest') {
    return <LoginPage onAuthenticated={(user) => setSession({ status: 'authenticated', user })} />
  }
  return <Workspace user={session.user} onSignedOut={() => setSession({ status: 'guest' })} />
}

