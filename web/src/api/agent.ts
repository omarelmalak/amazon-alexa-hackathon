export type ChatTurn = { role: 'user' | 'assistant'; content: string }

export type DebugStepPayload = {
  kind?: string
  title?: string
  text?: string
  detectionIndex?: number
  partId?: string
  similarity?: number
  imageBase64?: string
  refImageBase64?: string
}

export type AgentEvent =
  | { event: 'status'; message?: string }
  | { event: 'plan'; text?: string }
  | { event: 'tools_ready'; tools?: string[] }
  | { event: 'tool_call'; id?: string; name: string; arguments?: unknown }
  | { event: 'tool_result'; id?: string; name?: string; ok?: boolean; result?: unknown }
  | {
      event: 'debug_step'
      id?: string
      tool?: string
      parentId?: string
      step?: DebugStepPayload
    }
  | { event: 'need_frame'; tool?: string; id?: string }
  | {
      event: 'frame_used'
      tool?: string
      source?: string
      id?: string
      hasFrame?: boolean
    }
  | { event: 'speak'; text: string }
  | { event: 'error'; message?: string }
  | { event: 'done' }

export async function fetchGreeting(): Promise<string> {
  const res = await fetch('/v1/sim/greeting', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ context: {} }),
  })
  if (!res.ok) throw new Error(`greeting ${res.status}`)
  const body = (await res.json()) as { speak?: string }
  return body.speak || 'Hi. How can I help?'
}

export async function probeTts(): Promise<boolean> {
  try {
    const res = await fetch('/v1/sim/tts/status')
    const body = (await res.json()) as { elevenlabs?: boolean }
    return Boolean(body.elevenlabs)
  } catch {
    return false
  }
}

export async function synthesizeTts(text: string): Promise<Blob> {
  const res = await fetch('/v1/sim/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
  if (!res.ok) throw new Error(`TTS ${res.status}`)
  return res.blob()
}

export type AgentContext = {
  sessionId?: string | null
  productId?: string | null
  /** Correlates need_frame SSE → /v1/sim/agent/frame */
  requestId?: string | null
  /** Optional seed frame; normally omitted — captured only when agent asks. */
  frameBase64?: string | null
  /** Called when the agent decides a vision tool needs a live webcam frame. */
  onNeedFrame?: (callId: string) => string | null | Promise<string | null>
}

export async function postAgentFrame(
  requestId: string,
  callId: string,
  frameBase64: string | null,
): Promise<void> {
  await fetch('/v1/sim/agent/frame', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      requestId,
      callId,
      frameBase64: frameBase64 || null,
    }),
  })
}

export async function streamAgent(
  utterance: string,
  history: ChatTurn[],
  onEvent: (evt: AgentEvent) => void | Promise<void>,
  context?: AgentContext,
): Promise<boolean> {
  const requestId =
    context?.requestId ||
    (typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `req-${Date.now()}`)

  const res = await fetch('/v1/sim/agent', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    },
    body: JSON.stringify({
      utterance,
      history,
      sessionId: context?.sessionId || undefined,
      productId: context?.productId || undefined,
      requestId,
      frameBase64: context?.frameBase64 || undefined,
    }),
  })
  if (!res.ok || !res.body) {
    const t = await res.text()
    throw new Error(t.slice(0, 240) || `agent ${res.status}`)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let spoke = false

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const chunks = buffer.split('\n\n')
    buffer = chunks.pop() || ''
    for (const chunk of chunks) {
      const line = chunk
        .split('\n')
        .filter((l) => l.startsWith('data:'))
        .map((l) => l.slice(5).trim())
        .join('')
      if (!line) continue
      let evt: AgentEvent
      try {
        evt = JSON.parse(line) as AgentEvent
      } catch {
        continue
      }

      if (evt.event === 'need_frame') {
        const callId = evt.id || 'unknown'
        let frame: string | null = null
        try {
          frame = (await context?.onNeedFrame?.(callId)) ?? null
        } catch {
          frame = null
        }
        void postAgentFrame(requestId, callId, frame)
      }

      await onEvent(evt)
      if (evt.event === 'speak') spoke = true
    }
  }
  return spoke
}
