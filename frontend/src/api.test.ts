import { afterEach, expect, test, vi } from 'vitest'

import { streamMutation } from './api'

afterEach(() => vi.unstubAllGlobals())

test('parses named SSE events after obtaining a CSRF token', async () => {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode('event: status\ndata: {"stage":"retrieving"}\n\n'))
      controller.enqueue(encoder.encode('event: done\ndata: {"outcome":"grounded_answer"}\n\n'))
      controller.close()
    },
  })
  const fetchMock = vi.fn()
    .mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ csrf_token: 'csrf-token' }),
    })
    .mockResolvedValueOnce({ ok: true, status: 200, body: stream })
  vi.stubGlobal('fetch', fetchMock)
  const events: Array<{ event: string; data: unknown }> = []

  await streamMutation('/conversations/1/turns', { question: 'Policy?' }, (event) => {
    events.push(event)
  })

  expect(events).toEqual([
    { event: 'status', data: { stage: 'retrieving' } },
    { event: 'done', data: { outcome: 'grounded_answer' } },
  ])
  expect(fetchMock).toHaveBeenLastCalledWith(
    '/api/v1/conversations/1/turns',
    expect.objectContaining({
      method: 'POST',
      headers: expect.objectContaining({ 'X-CSRF-Token': 'csrf-token' }),
    }),
  )
})
