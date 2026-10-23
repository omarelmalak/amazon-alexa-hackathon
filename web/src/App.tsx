import { useCallback, useEffect, useRef, useState } from 'react'
import {
  type AgentEvent,
  type ChatTurn,
  fetchGreeting,
  streamAgent,
} from './api/agent'
import type { SessionState } from './api/sessions'
import { AskBar } from './components/AskBar'
import { DebugPanel, type AssemblyContext } from './components/DebugPanel'
import {
  IconBag,
  IconBook,
  IconCheck,
  IconCompass,
  IconSparkle,
  IconUser,
} from './components/icons'
import { SideDock } from './components/SideDock'
import { useVoice } from './hooks/useVoice'
import './App.css'

const USER_NAME = 'Omar'

const QUICK_ACTIONS = [
  {
    id: 'plan',
    label: 'Plan',
    Icon: IconCheck,
    prompt: 'Help me plan assembling my IKEA furniture step by step.',
  },
  {
    id: 'learn',
    label: 'Learn',
    Icon: IconBook,
    prompt: 'Teach me how Flatpack Co-Pilot works with Alexa+.',
  },
  {
    id: 'create',
    label: 'Create',
    Icon: IconSparkle,
    prompt: 'Start a new assembly session for my furniture.',
  },
  {
    id: 'shop',
    label: 'Shop',
    Icon: IconBag,
    prompt: 'I need replacement parts for my build.',
  },
  {
    id: 'find',
    label: 'Find',
    Icon: IconCompass,
    prompt: 'Find my product in the catalog and start helping me assemble it.',
  },
] as const

type OrderReceiptData = {
  orderId?: string
  bookingId?: string
  status?: string
  provider?: string
  totalCents?: number
  currency?: string
  checkoutUrl?: string
  estimatedDelivery?: string
  arrivalWindow?: string
  task?: string
  taskerName?: string
  taskerRating?: number
  message?: string
  items?: Array<{
    partId?: string
    name?: string
    qty?: number
    unitPriceCents?: number
    stockStatus?: string
  }>
}

type UiMessage =
  | { id: string; kind: 'user' | 'alexa' | 'system'; text: string }
  | {
      id: string
      kind: 'tool'
      name: string
      args?: unknown
      status: 'running' | 'ok' | 'error'
      result?: unknown
      frameDataUrl?: string
      frameSource?: string
    }
  | {
      id: string
      kind: 'order'
      order: OrderReceiptData
    }
  | {
      id: string
      kind: 'debug'
      tool?: string
      stepKind?: string
      title: string
      text?: string
      imageDataUrl?: string
      refImageDataUrl?: string
      similarity?: number
      partId?: string
    }

let msgSeq = 0
function nextId() {
  msgSeq += 1
  return `m${msgSeq}`
}

function readDebugFlag() {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).get('debug') === '1'
}

function writeDebugFlag(on: boolean) {
  const url = new URL(window.location.href)
  if (on) url.searchParams.set('debug', '1')
  else url.searchParams.delete('debug')
  window.history.replaceState({}, '', url)
}

function truncateToolJson(value: unknown): unknown {
  if (value == null) return value
  if (typeof value === 'string') {
    if (value.startsWith('data:image') || value.length > 240) {
      return `[${value.slice(0, 24)}… ${value.length} chars]`
    }
    return value
  }
  if (Array.isArray(value)) return value.map(truncateToolJson)
  if (typeof value === 'object') {
    const out: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (
        k === 'annotatedFrameBase64' ||
        k === 'imageBase64' ||
        k === 'frameBase64' ||
        k === 'frame_base64'
      ) {
        out[k] = typeof v === 'string' ? `[image ${v.length} chars]` : v
      } else {
        out[k] = truncateToolJson(v)
      }
    }
    return out
  }
  return value
}

function unwrapToolResult(result: unknown): Record<string, unknown> | null {
  if (!result || typeof result !== 'object') return null
  const obj = result as Record<string, unknown>
  // Prefer identity / inspect payloads as-is (don't dig into nested .result).
  if (
    'partId' in obj ||
    'debugPipeline' in obj ||
    'comparisons' in obj ||
    'detections' in obj
  ) {
    return obj
  }
  if (obj.structuredContent && typeof obj.structuredContent === 'object') {
    return obj.structuredContent as Record<string, unknown>
  }
  if (obj.structured_content && typeof obj.structured_content === 'object') {
    return obj.structured_content as Record<string, unknown>
  }
  if (obj.result && typeof obj.result === 'object' && !Array.isArray(obj.result)) {
    return obj.result as Record<string, unknown>
  }
  return obj
}

function annotatedFrameFromResult(result: unknown): string | undefined {
  const data = unwrapToolResult(result)
  if (!data) return undefined
  const ann = data.annotatedFrameBase64
  if (typeof ann === 'string' && ann.startsWith('data:')) return ann
  const img = data.imageBase64
  if (typeof img === 'string' && img.startsWith('data:')) return img
  return undefined
}

function formatMoney(cents: number | undefined, currency = 'usd'): string {
  const n = typeof cents === 'number' ? cents : 0
  try {
    return new Intl.NumberFormat('en-US', {
      style: 'currency',
      currency: currency.toUpperCase(),
    }).format(n / 100)
  } catch {
    return `$${(n / 100).toFixed(2)}`
  }
}

function OrderReceiptCard({
  order,
  compact = false,
}: {
  order: OrderReceiptData
  compact?: boolean
}) {
  const items = Array.isArray(order.items) ? order.items : []
  const isTaskrabbit =
    (order.provider || '').includes('taskrabbit') || Boolean(order.bookingId)
  const id = order.bookingId || order.orderId
  const title = isTaskrabbit ? 'Taskrabbit' : 'IKEA spare parts'
  const linkLabel = isTaskrabbit ? 'Open Taskrabbit' : 'Open IKEA spare parts'
  const when =
    order.arrivalWindow ||
    (order.estimatedDelivery ? `arrives ~${order.estimatedDelivery}` : '')
  return (
    <div className={`bubble order-card${compact ? ' compact' : ''}`}>
      <div className="order-card-h">
        <span>{title}</span>
        <span className="order-status">{order.status || 'confirmed'}</span>
      </div>
      {id && (
        <p className="order-id">
          {isTaskrabbit ? 'Booking' : 'Order'} <code>{id}</code>
        </p>
      )}
      {isTaskrabbit && (order.taskerName || order.task) && (
        <ul className="order-lines">
          {order.taskerName && (
            <li>
              <span>
                {order.taskerName}
                {order.taskerRating != null
                  ? ` · ${order.taskerRating}★`
                  : ''}
              </span>
              <span>Tasker</span>
            </li>
          )}
          {order.task && (
            <li>
              <span>{order.task}</span>
              <span>Task</span>
            </li>
          )}
        </ul>
      )}
      {!isTaskrabbit && items.length > 0 && (
        <ul className="order-lines">
          {items.map((it, i) => (
            <li key={`${it.partId || 'p'}-${i}`}>
              <span>
                {it.qty ?? 1}× {it.name || 'part'}
                {it.partId ? (
                  <span className="order-pid"> #{it.partId}</span>
                ) : null}
              </span>
              <span>{formatMoney(it.unitPriceCents)}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="order-foot">
        <span>
          Total {formatMoney(order.totalCents, order.currency)}
          {when ? ` · ${when}` : ''}
        </span>
        {order.checkoutUrl && (
          <a
            className="order-link"
            href={order.checkoutUrl}
            target="_blank"
            rel="noreferrer"
          >
            {linkLabel}
          </a>
        )}
      </div>
      {!compact && order.message && (
        <p className="order-note">{order.message}</p>
      )}
      <p className="order-sim">Simulated checkout — no real charge</p>
    </div>
  )
}

function InspectPartDebug({
  result,
  frameDataUrl,
  frameSource,
  args,
}: {
  result?: unknown
  frameDataUrl?: string
  frameSource?: string
  args?: unknown
}) {
  const data = unwrapToolResult(result) || {}
  const annotated = annotatedFrameFromResult(result)
  const src = annotated || frameDataUrl
  const pipeline = Array.isArray(data.debugPipeline)
    ? (data.debugPipeline as string[])
    : []
  const comparisons = Array.isArray(data.comparisons)
    ? (data.comparisons as Array<Record<string, unknown>>)
    : []
  const detections = Array.isArray(data.detections)
    ? (data.detections as Array<Record<string, unknown>>)
    : []
  const winnerIdx =
    typeof data.winnerDetectionIndex === 'number'
      ? data.winnerDetectionIndex
      : null
  const winnerId = typeof data.partId === 'string' ? data.partId : null

  return (
    <div className="inspect-debug">
      {src && (
        <div className="tool-frame">
          <img
            src={src}
            alt={annotated ? 'Isolated fasteners with boxes' : 'Webcam frame'}
          />
          <span className="tool-frame-cap">
            {annotated
              ? `isolated ${detections.length || '?'} fastener(s) — winner highlighted`
              : frameSource === 'webcam'
                ? 'webcam frame (awaiting annotate)'
                : 'captured frame'}
          </span>
        </div>
      )}

      {pipeline.length > 0 && (
        <div className="inspect-pipeline">
          <div className="inspect-pipeline-h">Match pipeline</div>
          <ol>
            {pipeline.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ol>
        </div>
      )}

      {comparisons.length > 0 && (
        <div className="inspect-scores">
          <div className="inspect-pipeline-h">
            Crop → parts DB scores (highest wins)
          </div>
          <table>
            <thead>
              <tr>
                <th>Det</th>
                <th>Part</th>
                <th>Score</th>
              </tr>
            </thead>
            <tbody>
              {comparisons.map((c, i) => {
                const det = Number(c.detectionIndex ?? 0)
                const pid = String(c.partId ?? '')
                const sim = Number(c.similarity ?? 0)
                const win = winnerIdx === det && winnerId === pid
                return (
                  <tr key={i} className={win ? 'win' : undefined}>
                    <td>#{det + 1}</td>
                    <td>
                      {pid}
                      {c.partName ? ` · ${String(c.partName)}` : ''}
                    </td>
                    <td>{(sim * 100).toFixed(0)}%</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {!pipeline.length && !comparisons.length && (
        <p className="inspect-warn">
          No isolation/compare payload — restart the API so YOLO + parts DB
          path is loaded, and ensure data/&lt;product&gt;/parts/ exists.
        </p>
      )}

      <pre>{JSON.stringify(truncateToolJson(args ?? {}), null, 2)}</pre>
      {result !== undefined && (
        <pre>
          →{' '}
          {JSON.stringify(truncateToolJson(result), null, 2).slice(0, 2800)}
        </pre>
      )}
    </div>
  )
}

function applyToolContext(
  name: string,
  result: unknown,
  prev: AssemblyContext,
): AssemblyContext {
  const data = unwrapToolResult(result)
  if (!data) return prev
  let next = { ...prev }

  if (name === 'ingest_product' && typeof data.productId === 'string') {
    next.productId = data.productId
  }
  if (name === 'create_session') {
    if (typeof data.sessionId === 'string') next.sessionId = data.sessionId
    if (data.state && typeof data.state === 'object') {
      next.sessionState = data.state as SessionState
      if (next.sessionState.productId) next.productId = next.sessionState.productId
    }
  }
  if (name === 'get_session' || name === 'advance_session') {
    if (typeof data.sessionId === 'string') next.sessionId = data.sessionId
    if (data.state && typeof data.state === 'object') {
      next.sessionState = data.state as SessionState
      if (next.sessionState.productId) next.productId = next.sessionState.productId
    }
  }
  if (name === 'get_assembly_step' && typeof data.productId === 'string') {
    next.productId = data.productId
  }
  return next
}

export default function App() {
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [started, setStarted] = useState(false)
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [debug, setDebug] = useState(readDebugFlag)
  const [handsFree, setHandsFree] = useState(true)
  const [context, setContext] = useState<AssemblyContext>({
    productId: null,
    sessionId: null,
    sessionState: null,
  })
  const [toolCount, setToolCount] = useState(0)
  const [agentReady, setAgentReady] = useState(false)

  const historyRef = useRef<ChatTurn[]>([])
  const chatEndRef = useRef<HTMLDivElement>(null)
  const busyRef = useRef(false)
  const submitRef = useRef<(text: string) => Promise<void>>(async () => undefined)
  const handsFreeRef = useRef(true)
  const startListeningRef = useRef<() => boolean>(() => false)
  const contextRef = useRef(context)
  const captureFrameRef = useRef<(() => string | null) | null>(null)
  const turnFrameRef = useRef<string | null>(null)
  const listenAfterTurnRef = useRef(false)

  handsFreeRef.current = handsFree
  contextRef.current = context

  const onCaptureReady = useCallback((capture: (() => string | null) | null) => {
    captureFrameRef.current = capture
  }, [])

  const pushMessage = useCallback((msg: UiMessage) => {
    setMessages((prev) => [...prev, msg])
  }, [])

  const { listening, speaking, speak, startListening, stopListening, speechSupported } =
    useVoice((text) => {
      void submitRef.current(text)
    })

  startListeningRef.current = startListening

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function toggleDebug() {
    setDebug((prev) => {
      const next = !prev
      writeDebugFlag(next)
      return next
    })
  }

  const handleAgentEvent = useCallback(
    async (evt: AgentEvent) => {
      if (evt.event === 'status') {
        if (debug) {
          pushMessage({ id: nextId(), kind: 'system', text: evt.message || 'working…' })
        }
      } else if (evt.event === 'plan') {
        if (debug) {
          pushMessage({ id: nextId(), kind: 'system', text: `plan: ${evt.text || ''}` })
        }
      } else if (evt.event === 'tools_ready') {
        setAgentReady(true)
        setToolCount((evt.tools || []).length)
        if (debug) {
          pushMessage({
            id: nextId(),
            kind: 'system',
            text: `tools: ${(evt.tools || []).join(', ')}`,
          })
        }
      } else if (evt.event === 'tool_call') {
        if (debug) {
          pushMessage({
            id: evt.id || nextId(),
            kind: 'tool',
            name: evt.name,
            args: evt.arguments,
            status: 'running',
          })
        }
      } else if (evt.event === 'debug_step') {
        if (debug && evt.step) {
          const s = evt.step
          pushMessage({
            id: evt.id || nextId(),
            kind: 'debug',
            tool: evt.tool,
            stepKind: s.kind,
            title: s.title || s.kind || 'debug',
            text: s.text,
            imageDataUrl: s.imageBase64,
            refImageDataUrl: s.refImageBase64,
            similarity: s.similarity,
            partId: s.partId,
          })
        }
      } else if (evt.event === 'need_frame') {
        if (debug) {
          pushMessage({
            id: nextId(),
            kind: 'system',
            text: `Capturing webcam for ${evt.tool || 'vision'}…`,
          })
        }
      } else if (evt.event === 'frame_used') {
        if (debug) {
          const frameB64 = turnFrameRef.current
          setMessages((prev) =>
            prev.map((m) =>
              m.kind === 'tool' && m.id === evt.id
                ? {
                    ...m,
                    frameSource: evt.source,
                    frameDataUrl:
                      frameB64 && evt.source === 'webcam'
                        ? `data:image/jpeg;base64,${frameB64}`
                        : m.frameDataUrl,
                  }
                : m,
            ),
          )
          if (evt.source && evt.source !== 'webcam') {
            pushMessage({
              id: nextId(),
              kind: 'system',
              text: `frame source: ${evt.source}${evt.hasFrame ? '' : ' (no image)'}`,
            })
          }
        }
      } else if (evt.event === 'tool_result') {
        setContext((prev) => applyToolContext(evt.name || '', evt.result, prev))
        if (debug) {
          setMessages((prev) => {
            let matched = false
            return prev.map((m) => {
              if (matched || m.kind !== 'tool') return m
              if (evt.id && m.id === evt.id) {
                matched = true
                const src =
                  evt.result &&
                  typeof evt.result === 'object' &&
                  'frameSource' in evt.result
                    ? String((evt.result as { frameSource?: string }).frameSource || '')
                    : m.frameSource
                return {
                  ...m,
                  status: evt.ok === false ? 'error' : 'ok',
                  result: evt.result,
                  frameSource: src || m.frameSource,
                }
              }
              if (!evt.id && m.status === 'running') {
                matched = true
                return {
                  ...m,
                  status: evt.ok === false ? 'error' : 'ok',
                  result: evt.result,
                }
              }
              return m
            })
          })
        }
        // Outside debug, show spare-parts / Taskrabbit receipts.
        if (
          !debug &&
          evt.ok !== false &&
          (evt.name === 'order_replacement_parts' ||
            evt.name === 'book_taskrabbit_help') &&
          evt.result &&
          typeof evt.result === 'object'
        ) {
          pushMessage({
            id: nextId(),
            kind: 'order',
            order: evt.result as OrderReceiptData,
          })
        }
      } else if (evt.event === 'speak') {
        const text = evt.text
        pushMessage({ id: nextId(), kind: 'alexa', text })
        historyRef.current = [
          ...historyRef.current,
          { role: 'assistant' as const, content: text },
        ].slice(-24)
        await speak(text)
        // Don't start the mic here — busyRef is still true until the turn ends.
        if (handsFreeRef.current) listenAfterTurnRef.current = true
      } else if (evt.event === 'error') {
        pushMessage({
          id: nextId(),
          kind: 'system',
          text: `error: ${evt.message || 'unknown'}`,
        })
      }
    },
    [debug, pushMessage, speak],
  )

  submitRef.current = async (utterance: string) => {
    const text = utterance.trim()
    if (!text || busyRef.current) return
    busyRef.current = true
    setBusy(true)
    setStarted(true)
    setDraft('')
    listenAfterTurnRef.current = false
    stopListening()

    pushMessage({ id: nextId(), kind: 'user', text })
    historyRef.current = [
      ...historyRef.current,
      { role: 'user' as const, content: text },
    ].slice(-24)

    turnFrameRef.current = null

    try {
      const prior = historyRef.current.slice(0, -1)
      const spoke = await streamAgent(text, prior, handleAgentEvent, {
        sessionId: contextRef.current.sessionId,
        productId: contextRef.current.productId,
        onNeedFrame: () => {
          // Only runs when the agent chose a vision tool (show-and-tell intent).
          const frame = captureFrameRef.current?.() ?? null
          turnFrameRef.current = frame
          return frame
        },
      })
      if (!spoke) {
        const fallback = 'Okay.'
        pushMessage({ id: nextId(), kind: 'alexa', text: fallback })
        historyRef.current = [
          ...historyRef.current,
          { role: 'assistant' as const, content: fallback },
        ]
        await speak(fallback)
        if (handsFreeRef.current) listenAfterTurnRef.current = true
      }
    } catch (err) {
      console.error(err)
      const msg = "Sorry — I hit a snag. Give me a second and try again."
      pushMessage({ id: nextId(), kind: 'alexa', text: msg })
      await speak(msg)
      if (handsFreeRef.current) listenAfterTurnRef.current = true
    } finally {
      busyRef.current = false
      setBusy(false)
      if (handsFreeRef.current && listenAfterTurnRef.current) {
        listenAfterTurnRef.current = false
        // Brief pause so TTS releases the audio path before the mic opens.
        window.setTimeout(() => {
          if (handsFreeRef.current && !busyRef.current) {
            startListeningRef.current()
          }
        }, 450)
      }
    }
  }

  useEffect(() => {
    void fetchGreeting().catch(() => undefined)
  }, [])

  const voiceLabel = listening ? 'listening' : speaking ? 'speaking…' : 'voice idle'
  const visibleMessages = debug
    ? messages
    : messages.filter(
        (m) => m.kind === 'user' || m.kind === 'alexa' || m.kind === 'order',
      )

  return (
    <div className={`app-shell${debug ? ' debug-on' : ''}`}>
      <header className="top-bar">
        <div className="brand" aria-label="alexa+">
          <span className="brand-word">alexa+</span>
          <span className="brand-smile" />
        </div>
        <div className="top-actions">
          <button
            type="button"
            className={`bts-link${debug ? ' active' : ''}`}
            onClick={toggleDebug}
            aria-pressed={debug}
          >
            {debug ? 'Debug on' : 'Debug mode'}
          </button>
          <span className="early-access">Early Access</span>
          <button type="button" className="avatar-btn" aria-label="Profile">
            <IconUser />
          </button>
        </div>
      </header>

      <SideDock
        active="home"
        onSelect={(id) => {
          if (id === 'home') {
            setStarted(false)
            setMessages([])
            historyRef.current = []
          }
        }}
      />

      <div className={`app-body${debug ? ' with-debug' : ''}`}>
        {!started ? (
          <main className="main-stage">
            <h1 className="greeting">Hello {USER_NAME}, how can I help?</h1>
            <div className="ask-wrap">
              <AskBar
                value={draft}
                onChange={setDraft}
                onSubmit={(t) => void submitRef.current(t)}
                onMic={() => {
                  if (listening) stopListening()
                  else startListening()
                }}
                listening={listening}
                busy={busy || speaking}
                autoFocus
              />
              <div className="chips" role="group" aria-label="Quick actions">
                {QUICK_ACTIONS.map(({ id, label, Icon, prompt }) => (
                  <button
                    key={id}
                    type="button"
                    className="chip"
                    disabled={busy}
                    onClick={() => void submitRef.current(prompt)}
                  >
                    <Icon />
                    {label}
                  </button>
                ))}
              </div>
              {!speechSupported && (
                <p className="hint">Voice needs Chrome or Edge — typing works everywhere.</p>
              )}
            </div>
          </main>
        ) : (
          <main className="main-stage chat">
            <div className="chat-panel">
              <div className="chat-scroll" aria-live="polite">
                {visibleMessages.map((m) => {
                  if (m.kind === 'order') {
                    return <OrderReceiptCard key={m.id} order={m.order} />
                  }
                  if (m.kind === 'tool') {
                    return (
                      <div key={m.id} className="bubble tool">
                        <div className="tool-head">
                          <span>MCP · {m.name}</span>
                          <span
                            className={
                              m.status === 'ok'
                                ? 'status-ok'
                                : m.status === 'error'
                                  ? 'status-err'
                                  : undefined
                            }
                          >
                            {m.status === 'running' ? 'running…' : m.status}
                          </span>
                        </div>
                        {m.name === 'inspect_part' ? (
                          <InspectPartDebug
                            result={m.result}
                            frameDataUrl={m.frameDataUrl}
                            frameSource={m.frameSource}
                            args={m.args}
                          />
                        ) : (m.name === 'order_replacement_parts' ||
                            m.name === 'book_taskrabbit_help') &&
                          m.result &&
                          typeof m.result === 'object' ? (
                          <OrderReceiptCard
                            order={m.result as OrderReceiptData}
                            compact
                          />
                        ) : (
                          <>
                            {(() => {
                              const annotated = annotatedFrameFromResult(m.result)
                              const src = annotated || m.frameDataUrl
                              if (!src) return null
                              return (
                                <div className="tool-frame">
                                  <img
                                    src={src}
                                    alt={`Frame for ${m.name}`}
                                  />
                                  <span className="tool-frame-cap">
                                    {annotated
                                      ? 'annotated frame'
                                      : m.frameSource === 'webcam'
                                        ? 'webcam frame'
                                        : m.frameSource
                                          ? `frame: ${m.frameSource}`
                                          : 'captured frame'}
                                  </span>
                                </div>
                              )
                            })()}
                            <pre>
                              {JSON.stringify(
                                truncateToolJson(m.args ?? {}),
                                null,
                                2,
                              )}
                            </pre>
                            {m.result !== undefined && (
                              <pre>
                                →{' '}
                                {JSON.stringify(
                                  truncateToolJson(m.result),
                                  null,
                                  2,
                                ).slice(0, 2200)}
                              </pre>
                            )}
                          </>
                        )}
                      </div>
                    )
                  }
                  if (m.kind === 'debug') {
                    return (
                      <div
                        key={m.id}
                        className={`bubble debug-card kind-${m.stepKind || 'status'}`}
                      >
                        <div className="debug-card-h">
                          <span>{m.title}</span>
                          {typeof m.similarity === 'number' && (
                            <span className="debug-score">
                              {(m.similarity * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                        {m.text && <p className="debug-card-text">{m.text}</p>}
                        <div className="debug-card-imgs">
                          {m.imageDataUrl && (
                            <figure>
                              <img src={m.imageDataUrl} alt={m.title} />
                              <figcaption>crop / frame</figcaption>
                            </figure>
                          )}
                          {m.refImageDataUrl && (
                            <figure>
                              <img
                                src={m.refImageDataUrl}
                                alt={m.partId || 'reference'}
                              />
                              <figcaption>
                                DB {m.partId || 'reference'}
                              </figcaption>
                            </figure>
                          )}
                        </div>
                      </div>
                    )
                  }
                  return (
                    <div key={m.id} className={`bubble ${m.kind}`}>
                      {m.kind === 'alexa' && <div className="who">Alexa+</div>}
                      {m.text}
                    </div>
                  )
                })}
                <div ref={chatEndRef} />
              </div>
              <div className="composer-dock">
                <AskBar
                  value={draft}
                  onChange={setDraft}
                  onSubmit={(t) => void submitRef.current(t)}
                  onMic={() => {
                    if (listening) stopListening()
                    else startListening()
                  }}
                  listening={listening}
                  busy={busy || speaking}
                />
              </div>
            </div>
          </main>
        )}

        {debug && (
          <DebugPanel
            context={context}
            onContextChange={setContext}
            handsFree={handsFree}
            onHandsFreeChange={setHandsFree}
            onCaptureReady={onCaptureReady}
            status={{
              mcp: agentReady ? 'agent ready' : 'agent idle',
              tools: `${toolCount} tools`,
              voice: voiceLabel,
            }}
          />
        )}
      </div>
    </div>
  )
}
