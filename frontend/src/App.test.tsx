import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import App from './App'

test('shows the Administrator visibility disclosure when signed out', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: false,
    status: 401,
    json: async () => ({ detail: 'Not authenticated' }),
  }))
  render(<App />)
  expect(await screen.findByRole('heading', { name: 'Sign in to mikuRAG' })).toBeInTheDocument()
  expect(screen.getByText(/Conversations are visible/)).toBeInTheDocument()
  vi.unstubAllGlobals()
})

