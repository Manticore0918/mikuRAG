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

test('shows durable source provenance and ingestion diagnostics', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => ({
    ok: true,
    status: 200,
    json: async () => String(input).endsWith('/document-uploads') ? [] : [{
      id: 'document-1',
      knowledge_base_id: 'kb-1',
      original_name: 'worker.py',
      media_type: 'text/x-python',
      size_bytes: 120,
      page_count: null,
      status: 'processing',
      safe_error: null,
      parser_version: 'python_ast_v1',
      chunking_version: null,
      source_kind: 'code',
      language: 'python',
      tags: ['operations'],
      source_uri: 'https://git.example.test/worker.py',
      source_path: 'src/jobs/worker.py',
      source_metadata: { title: 'Worker' },
      ingestion_stage: 'embed',
      ingestion_progress: 70,
      ingestion_attempts: 2,
      ingestion_warnings: [{
        code: 'code_symbol_index_fallback',
        message: 'Line-aware source blocks were used.',
      }],
      created_at: '2026-08-25T00:00:00Z',
      updated_at: '2026-08-25T00:01:00Z',
    }],
  }) as Response))

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

  expect(await screen.findByText('worker.py')).toBeInTheDocument()
  expect(screen.getByText(/code · python · src\/jobs\/worker.py/)).toBeInTheDocument()
  expect(screen.getByText('embed · 70% · attempt 2')).toBeInTheDocument()
  expect(screen.getByText('parser python_ast_v1 · chunker pending')).toBeInTheDocument()
  expect(screen.getByText('Line-aware source blocks were used.')).toBeInTheDocument()
  vi.unstubAllGlobals()
})
