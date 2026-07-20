import {
  ApiError,
  mutate,
  mutateBinary,
  type DocumentRecord,
  type UploadPartResult,
  type UploadSession,
} from './api'

export class UploadPausedError extends Error {
  constructor() {
    super('Upload paused')
  }
}

export async function sha256Hex(content: Blob): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await content.arrayBuffer())
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function retryable(reason: unknown): boolean {
  return !(reason instanceof ApiError) || reason.status === 408 || reason.status === 429 || reason.status >= 500
}

function wait(delayMs: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new UploadPausedError())
      return
    }
    const aborted = () => {
      window.clearTimeout(timer)
      reject(new UploadPausedError())
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', aborted)
      resolve()
    }, delayMs)
    signal.addEventListener('abort', aborted, { once: true })
  })
}

export async function transferUpload(
  knowledgeBaseId: string,
  session: UploadSession,
  file: File,
  signal: AbortSignal,
  isPaused: () => boolean,
  onConfirmed: (offset: number, expiresAt: string) => void,
): Promise<DocumentRecord> {
  let offset = session.received_bytes
  while (offset < file.size) {
    if (signal.aborted || isPaused()) throw new UploadPausedError()
    const part = file.slice(offset, Math.min(file.size, offset + session.part_size_bytes))
    const checksum = await sha256Hex(part)
    let attempt = 0
    let result: UploadPartResult | null = null
    while (result === null) {
      if (signal.aborted || isPaused()) throw new UploadPausedError()
      try {
        result = await mutateBinary<UploadPartResult>(
          `/admin/knowledge-bases/${knowledgeBaseId}/document-uploads/${session.id}/parts`,
          part,
          {
            'X-Upload-Offset': String(offset),
            'X-Upload-Length': String(part.size),
            'X-Upload-SHA256': checksum,
          },
          signal,
        )
      } catch (reason) {
        if (signal.aborted || isPaused()) throw new UploadPausedError()
        if (!retryable(reason) || attempt >= 3) throw reason
        const delay = 500 * (2 ** attempt) + Math.floor(Math.random() * 250)
        attempt += 1
        await wait(delay, signal)
      }
    }
    offset = result.next_offset
    onConfirmed(offset, result.expires_at)
  }
  if (signal.aborted || isPaused()) throw new UploadPausedError()
  return mutate<DocumentRecord>(
    `/admin/knowledge-bases/${knowledgeBaseId}/document-uploads/${session.id}/complete`,
    'POST',
  )
}
