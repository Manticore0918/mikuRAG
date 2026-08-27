import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
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


test('sends normalized retrieval filters with a turn', async () => {
  const conversation = {
    id: 'conversation-3', knowledge_base_id: 'kb-1', knowledge_base_name: 'Operations',
    title: 'Filtered question', created_at: '2026-07-13T00:00:00Z', updated_at: '2026-07-13T00:00:00Z',
  }
  let turnBody: Record<string, unknown> | null = null
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.endsWith('/auth/csrf')) {
      return new Response(JSON.stringify({ csrf_token: 'csrf' }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    if (url.endsWith('/retrieval-documents')) {
      return new Response(JSON.stringify([{
        id: 'document-1', original_name: 'policy.md', source_kind: 'markdown',
        language: 'en', tags: ['policy'], ingested_at: '2026-08-01T00:00:00Z',
      }]), { status: 200, headers: { 'Content-Type': 'application/json' } })
    }
    if (url.endsWith('/turns')) {
      turnBody = JSON.parse(String(init?.body)) as Record<string, unknown>
      return new Response('event: done\ndata: {"outcome":"insufficient_evidence"}\n\n', {
        status: 200, headers: { 'Content-Type': 'text/event-stream' },
      })
    }
    if (url.endsWith('/conversation-3')) {
      return new Response(JSON.stringify({ ...conversation, messages: [] }), {
        status: 200, headers: { 'Content-Type': 'application/json' },
      })
    }
    return new Response(JSON.stringify([conversation]), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    })
  }))

  render(<ChatPanel knowledgeBases={knowledgeBases} initialConversationId="conversation-3" onError={vi.fn()} />)
  fireEvent.click(screen.getByText('Retrieval filters'))
  const documents = (await screen.findByLabelText(
    'Filter Documents',
  )) as HTMLSelectElement
  const documentOption = Array.from(documents.options).find(
    (option) => option.value === 'document-1',
  )
  expect(documentOption).toBeDefined()
  documentOption!.selected = true
  fireEvent.change(documents)
  fireEvent.change(screen.getByLabelText('Tags'), { target: { value: ' policy, security ' } })
  fireEvent.change(screen.getByLabelText('Source type'), { target: { value: 'markdown' } })
  fireEvent.change(screen.getByLabelText('Language'), { target: { value: 'EN' } })
  fireEvent.change(screen.getByLabelText('Ingested after'), { target: { value: '2026-08-01' } })
  fireEvent.change(screen.getByLabelText('Ingested before'), { target: { value: '2026-08-02' } })
  fireEvent.change(screen.getByLabelText('Ask a question'), { target: { value: 'What changed?' } })
  fireEvent.click(screen.getByRole('button', { name: 'Send' }))

  await waitFor(() => expect(turnBody).not.toBeNull())
  expect(turnBody).toEqual({
    question: 'What changed?',
    filters: {
      document_ids: ['document-1'],
      tags: ['policy', 'security'],
      source_kinds: ['markdown'],
      languages: ['en'],
      ingested_after: '2026-08-01T00:00:00.000Z',
      ingested_before: '2026-08-02T23:59:59.999Z',
    },
  })
})
