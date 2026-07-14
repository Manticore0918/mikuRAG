export type User = {
  id: string
  username: string
  is_administrator: boolean
  is_enabled: boolean
  created_at: string
}

export type KnowledgeBase = {
  id: string
  name: string
  description: string | null
  created_at: string
}

export type AccessGrant = {
  user_id: string
  knowledge_base_id: string
}

export type DocumentRecord = {
  id: string
  knowledge_base_id: string
  original_name: string
  media_type: string
  size_bytes: number
  page_count: number | null
  status: 'pending' | 'processing' | 'ready' | 'failed' | 'deleting'
  safe_error: string | null
  created_at: string
  updated_at: string
}

export type Conversation = {
  id: string
  knowledge_base_id: string
  knowledge_base_name: string
  title: string
  created_at: string
  updated_at: string
}

export type Citation = {
  id: string
  document_name: string
  locator: Record<string, string | number>
  excerpt: string
  retrieval_rank: number
  retrieval_score: number | null
  source_available: boolean
  source_url: string | null
}

export type ChatMessage = {
  id: string
  sequence: number
  role: 'user' | 'assistant'
  status: 'streaming' | 'complete' | 'failed'
  content: string
  created_at: string
  citations: Citation[]
}

export type ConversationDetail = Conversation & { messages: ChatMessage[] }

export type ServerEvent = {
  event: string
  data: unknown
}

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function parseError(response: Response): Promise<string> {
  try {
    const body = await response.json()
    return typeof body.detail === 'string' ? body.detail : 'Request failed'
  } catch {
    return 'Request failed'
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const isFormData = init?.body instanceof FormData
  const response = await fetch(`/api/v1${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      ...(init?.body && !isFormData ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    throw new ApiError(response.status, await parseError(response))
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

async function csrfToken(): Promise<string> {
  const result = await request<{ csrf_token: string }>('/auth/csrf')
  return result.csrf_token
}

export async function mutate<T>(path: string, method: string, body?: unknown): Promise<T> {
  const csrf = await csrfToken()
  return request<T>(path, {
    method,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers: { 'X-CSRF-Token': csrf },
  })
}

export async function mutateForm<T>(path: string, method: string, body: FormData): Promise<T> {
  const csrf = await csrfToken()
  return request<T>(path, {
    method,
    body,
    headers: { 'X-CSRF-Token': csrf },
  })
}

function parseServerEvent(block: string): ServerEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  return { event, data: JSON.parse(data.join('\n')) as unknown }
}

export async function streamMutation(
  path: string,
  body: unknown,
  onEvent: (event: ServerEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const csrf = await csrfToken()
  const response = await fetch(`/api/v1${path}`, {
    method: 'POST',
    credentials: 'include',
    signal,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
    body: JSON.stringify(body),
  })
  if (!response.ok) throw new ApiError(response.status, await parseError(response))
  if (!response.body) throw new ApiError(502, 'The response stream was unavailable')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() ?? ''
    for (const block of blocks) {
      const event = parseServerEvent(block)
      if (event) onEvent(event)
    }
    if (done) break
  }
  if (buffer.trim()) {
    const event = parseServerEvent(buffer)
    if (event) onEvent(event)
  }
}
