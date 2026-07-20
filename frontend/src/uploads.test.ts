import { afterEach, expect, test, vi } from 'vitest'

const apiMocks = vi.hoisted(() => ({
  mutate: vi.fn(),
  mutateBinary: vi.fn(),
}))

vi.mock('./api', async (importOriginal) => ({
  ...await importOriginal<typeof import('./api')>(),
  mutate: apiMocks.mutate,
  mutateBinary: apiMocks.mutateBinary,
}))

import type { DocumentRecord, UploadSession } from './api'
import { transferUpload } from './uploads'

const session: UploadSession = {
  id: 'upload-1',
  knowledge_base_id: 'kb-1',
  initiated_by_id: 'user-1',
  initiated_by_username: 'admin',
  original_name: 'notes.txt',
  declared_sha256: '0'.repeat(64),
  total_bytes: 6,
  received_bytes: 0,
  part_size_bytes: 4,
  status: 'open',
  safe_error: null,
  resulting_document_id: null,
  expires_at: '2026-07-17T00:00:00Z',
  created_at: '2026-07-16T00:00:00Z',
  updated_at: '2026-07-16T00:00:00Z',
}

const document: DocumentRecord = {
  id: 'document-1',
  knowledge_base_id: 'kb-1',
  original_name: 'notes.txt',
  media_type: 'text/plain',
  size_bytes: 6,
  page_count: null,
  status: 'pending',
  safe_error: null,
  created_at: '2026-07-16T00:00:00Z',
  updated_at: '2026-07-16T00:00:00Z',
}

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  apiMocks.mutate.mockReset()
  apiMocks.mutateBinary.mockReset()
})

test('sends sequential parts and reports only confirmed offsets', async () => {
  apiMocks.mutateBinary
    .mockResolvedValueOnce({ next_offset: 4, expires_at: 'later-1' })
    .mockResolvedValueOnce({ next_offset: 6, expires_at: 'later-2' })
  apiMocks.mutate.mockResolvedValue(document)
  const confirmed: number[] = []

  const result = await transferUpload(
    'kb-1',
    session,
    new File([new Uint8Array([1, 2, 3, 4, 5, 6])], 'notes.txt'),
    new AbortController().signal,
    () => false,
    (offset) => confirmed.push(offset),
  )

  expect(result).toBe(document)
  expect(confirmed).toEqual([4, 6])
  expect(apiMocks.mutateBinary).toHaveBeenNthCalledWith(
    1,
    expect.any(String),
    expect.any(Blob),
    expect.objectContaining({ 'X-Upload-Offset': '0', 'X-Upload-Length': '4' }),
    expect.any(AbortSignal),
  )
  expect(apiMocks.mutateBinary).toHaveBeenNthCalledWith(
    2,
    expect.any(String),
    expect.any(Blob),
    expect.objectContaining({ 'X-Upload-Offset': '4', 'X-Upload-Length': '2' }),
    expect.any(AbortSignal),
  )
})

test('retries a transient part failure before advancing progress', async () => {
  vi.spyOn(Math, 'random').mockReturnValue(0)
  apiMocks.mutateBinary
    .mockRejectedValueOnce(new Error('network unavailable'))
    .mockResolvedValueOnce({ next_offset: 4, expires_at: 'later-1' })
    .mockResolvedValueOnce({ next_offset: 6, expires_at: 'later-2' })
  apiMocks.mutate.mockResolvedValue(document)
  const confirmed: number[] = []

  const pending = transferUpload(
    'kb-1',
    session,
    new File([new Uint8Array([1, 2, 3, 4, 5, 6])], 'notes.txt'),
    new AbortController().signal,
    () => false,
    (offset) => confirmed.push(offset),
  )
  await expect(pending).resolves.toBe(document)

  expect(apiMocks.mutateBinary).toHaveBeenCalledTimes(3)
  expect(confirmed).toEqual([4, 6])
})
