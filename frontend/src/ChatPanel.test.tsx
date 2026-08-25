import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'

import ChatPanel from './ChatPanel'
import { formatCitationLocator } from './citationFormatting'

const knowledgeBases = [{
  id: 'kb-1',
  name: 'Operations',
  description: null,
  created_at: '2026-07-13T00:00:00Z',
}]

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

test('formats compatible page locators and hides internal hierarchy metadata', () => {
  expect(formatCitationLocator({
    page: 14,
    start_page: 14,
    end_page: 14,
    heading_path: ['Guide', 'Setup'],
    source_parent_id: 'internal',
  })).toBe('p. 14 · Guide › Setup')
  expect(formatCitationLocator({
    start_page: 14,
    end_page: 15,
  })).toBe('pp. 14-15')
  expect(formatCitationLocator({ section: 'Access' })).toBe('section: Access')
})

test('formats HTML, Markdown, and code locators with source-specific labels', () => {
  expect(formatCitationLocator({
    source_kind: 'html',
    heading_path: ['Guide', 'Recovery'],
    element: '#content > p:nth-of-type(1)',
    line_start: 18,
    line_end: 20,
  })).toBe('Guide › Recovery · lines 18-20 · #content > p:nth-of-type(1)')

  expect(formatCitationLocator({
    source_kind: 'code',
    source_path: 'src/jobs/worker.py',
    path: 'src/jobs/worker.py',
    line_start: 41,
    line_end: 44,
    symbol: 'restore',
    language: 'python',
  })).toBe('src/jobs/worker.py:41-44 · symbol restore')

  expect(formatCitationLocator({
    source_kind: 'markdown',
    heading_path: ['Setup'],
    line_start: 7,
    line_end: 7,
  })).toBe('Setup · line 7')
})

test('shows validated Citations with expandable evidence and source access', async () => {
  const conversation = {
    id: 'conversation-1',
    knowledge_base_id: 'kb-1',
    knowledge_base_name: 'Operations',
    title: 'Access policy',
    created_at: '2026-07-13T00:00:00Z',
    updated_at: '2026-07-13T00:00:00Z',
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input)
    return {
      ok: true,
      status: 200,
      json: async () => url.endsWith('/conversation-1') ? {
        ...conversation,
        messages: [{
          id: 'answer-1',
          sequence: 2,
          role: 'assistant',
          status: 'complete',
          content: 'Approval is required. [1]',
          created_at: '2026-07-13T00:00:00Z',
          citations: [{
            id: 'citation-1',
            document_name: 'policy.pdf',
            locator: { page: 2 },
            excerpt: 'All requests require approval.',
            retrieval_rank: 1,
            retrieval_score: 0.03,
            source_available: true,
            source_url: '/api/v1/source',
          }],
        }],
      } : [conversation],
    } as Response
  }))

  render(<ChatPanel knowledgeBases={knowledgeBases} initialConversationId="conversation-1" onError={vi.fn()} />)

  expect(await screen.findByText('Approval is required.')).toBeInTheDocument()
  fireEvent.click(screen.getByText('policy.pdf'))
  expect(screen.getByText('All requests require approval.')).toBeInTheDocument()
  expect(screen.getByText('p. 2')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open source' })).toHaveAttribute('href', '/api/v1/source')
})


test('renders an insufficient-evidence answer without a Citation list', async () => {
  const conversation = {
    id: 'conversation-2', knowledge_base_id: 'kb-1', knowledge_base_name: 'Operations',
    title: 'Unknown topic', created_at: '2026-07-13T00:00:00Z', updated_at: '2026-07-13T00:00:00Z',
  }
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => ({
    ok: true,
    status: 200,
    json: async () => String(input).endsWith('/conversation-2') ? {
      ...conversation,
      messages: [{
        id: 'answer-2', sequence: 2, role: 'assistant', status: 'complete',
        content: 'I cannot answer reliably from the available Documents.',
        created_at: '2026-07-13T00:00:00Z', citations: [],
      }],
    } : [conversation],
  }) as Response))

  const view = render(<ChatPanel knowledgeBases={knowledgeBases} initialConversationId="conversation-2" onError={vi.fn()} />)

  expect(await screen.findByText(/cannot answer reliably/)).toBeInTheDocument()
  expect(view.container.querySelector('[aria-label="Citations"]')).toBeNull()
})
