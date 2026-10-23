export type ProductSummary = {
  productId: string
  displayName: string
  stepCount: number
}

export type SessionState = {
  productId: string
  currentStep: number
  activeMode: string
  stepCompleted?: boolean
}

export type SessionCreated = {
  sessionId: string
  state: SessionState
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  const text = await res.text()
  let body: unknown = null
  try {
    body = text ? JSON.parse(text) : null
  } catch {
    body = text
  }
  if (!res.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? (body as { detail: unknown }).detail
        : text || res.statusText
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  return body as T
}

export function listProducts() {
  return api<ProductSummary[]>('/v1/products')
}

export function createSession(productId: string) {
  return api<SessionCreated>('/v1/sessions', {
    method: 'POST',
    body: JSON.stringify({ productId }),
  })
}

export function getSession(sessionId: string) {
  return api<{ sessionId: string; state: SessionState }>(`/v1/sessions/${sessionId}`)
}

export function advanceSession(sessionId: string) {
  return api<{ sessionId: string; state: SessionState }>(
    `/v1/sessions/${sessionId}/advance`,
    { method: 'POST' },
  )
}

export function inspectOrientation(sessionId: string, frameBase64: string) {
  return api<{
    aligned?: boolean
    stepCompleted?: boolean
    boundingBox?: { x: number; y: number; w: number; h: number }
    [key: string]: unknown
  }>(`/v1/sessions/${sessionId}/inspect/orientation`, {
    method: 'POST',
    body: JSON.stringify({ frameBase64 }),
  })
}

export function inspectPart(sessionId: string, frameBase64: string) {
  return api<{ partId?: string; [key: string]: unknown }>(
    `/v1/sessions/${sessionId}/inspect/part`,
    {
      method: 'POST',
      body: JSON.stringify({ frameBase64 }),
    },
  )
}

export function inspectInventory(sessionId: string, frameBase64: string) {
  return api<{
    counted?: unknown
    missing?: unknown[]
    [key: string]: unknown
  }>(`/v1/sessions/${sessionId}/inspect/inventory`, {
    method: 'POST',
    body: JSON.stringify({ frameBase64 }),
  })
}

export function stepDiagramUrl(productId: string, step: number) {
  return `/v1/products/${productId}/steps/${step}/diagram?t=${Date.now()}`
}
