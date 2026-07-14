import { render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'

import DocumentPanel from './DocumentPanel'

test('shows the remote embedding boundary and empty ingestion state', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => [],
  }))

  render(
    <DocumentPanel
      knowledgeBases={[{
        id: 'kb-1',
        name: 'Operations',
        description: null,
        created_at: '2026-07-13T00:00:00Z',
      }]}
      onError={vi.fn()}
      onNotice={vi.fn()}
    />,
  )

  expect(await screen.findByText('No Documents in this Knowledge Base yet.')).toBeInTheDocument()
  expect(screen.getByText(/text leaves this Installation/)).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'Operations' })).toBeInTheDocument()
  vi.unstubAllGlobals()
})
