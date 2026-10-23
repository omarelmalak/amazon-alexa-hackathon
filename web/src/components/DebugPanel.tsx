import { useCallback, useEffect, useRef, useState } from 'react'
import {
  advanceSession,
  getSession,
  inspectInventory,
  inspectOrientation,
  inspectPart,
  stepDiagramUrl,
  type SessionState,
} from '../api/sessions'
import { Tier1Engine, type Tier1Tick } from '../vision/tier1'

export type AssemblyContext = {
  productId: string | null
  sessionId: string | null
  sessionState: SessionState | null
}

type Props = {
  context: AssemblyContext
  onContextChange: (ctx: AssemblyContext) => void
  handsFree: boolean
  onHandsFreeChange: (v: boolean) => void
  status: { mcp: string; tools: string; voice: string }
  /** Register a live JPEG-base64 capture fn for the agent host. */
  onCaptureReady?: (capture: (() => string | null) | null) => void
}

export function DebugPanel({
  context,
  onContextChange,
  handsFree,
  onHandsFreeChange,
  status,
  onCaptureReady,
}: Props) {
  const videoRef = useRef<HTMLVideoElement>(null)
  const overlayRef = useRef<HTMLCanvasElement>(null)
  const engineRef = useRef<Tier1Engine | null>(null)
  const inspectBusy = useRef(false)
  const autoInspectRef = useRef(false)
  const runOrientationRef = useRef<(reason: string) => Promise<void>>(async () => undefined)
  const camGen = useRef(0)

  const [cameraOn, setCameraOn] = useState(false)
  const [autoInspect, setAutoInspect] = useState(false)
  const [stability, setStability] = useState('camera off')
  const [modeBadge, setModeBadge] = useState('IDLE')
  const [modeClass, setModeClass] = useState('')
  const [error, setError] = useState<string | null>(null)

  const stopCamera = useCallback(() => {
    camGen.current += 1
    engineRef.current?.stop()
    engineRef.current = null
    const video = videoRef.current
    const stream = video?.srcObject as MediaStream | null
    stream?.getTracks().forEach((t) => t.stop())
    if (video) {
      video.pause()
      video.srcObject = null
    }
    setCameraOn(false)
    setStability('camera off')
  }, [])

  const runOrientation = useCallback(
    async (_reason: string) => {
      const sessionId = context.sessionId
      const engine = engineRef.current
      if (!sessionId || !engine || inspectBusy.current) return
      inspectBusy.current = true
      engine.setInspecting(true)
      setModeBadge('ORIENTATION')
      setModeClass('inspecting')
      try {
        const frameBase64 = engine.captureJpegBase64()
        const result = await inspectOrientation(sessionId, frameBase64)
        const ok = Boolean(result.aligned)
        setModeBadge(ok ? 'ALIGNED' : 'MISALIGNED')
        setModeClass(ok ? 'ok' : 'bad')
        const video = videoRef.current
        if (video?.videoWidth) {
          if (result.boundingBox) {
            const capW = engine.captureWidth(960)
            const scale = video.videoWidth / capW
            const b = result.boundingBox
            engine.setResultBox(
              { x: b.x * scale, y: b.y * scale, w: b.w * scale, h: b.h * scale },
              ok,
            )
          } else {
            engine.setResultBox(
              {
                x: video.videoWidth * 0.04,
                y: video.videoHeight * 0.04,
                w: video.videoWidth * 0.92,
                h: video.videoHeight * 0.92,
              },
              ok,
            )
          }
        }
        const sess = await getSession(sessionId)
        onContextChange({
          productId: sess.state.productId,
          sessionId,
          sessionState: sess.state,
        })
        if (result.aligned && result.stepCompleted) {
          const adv = await advanceSession(sessionId)
          onContextChange({
            productId: adv.state.productId,
            sessionId,
            sessionState: adv.state,
          })
          engine.clearResultBox()
        }
      } catch (err) {
        setModeBadge('ERROR')
        setModeClass('bad')
        setError(String((err as Error).message || err))
      } finally {
        engine.markInspected()
        engine.setInspecting(false)
        inspectBusy.current = false
      }
    },
    [context.sessionId, onContextChange],
  )

  runOrientationRef.current = runOrientation
  autoInspectRef.current = autoInspect

  const startCamera = useCallback(async () => {
    const gen = ++camGen.current
    setError(null)
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: 'environment',
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      })
      if (gen !== camGen.current) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }

      const video = videoRef.current
      const overlay = overlayRef.current
      if (!video || !overlay) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }

      // Tear down any prior stream before attaching a new one
      const prev = video.srcObject as MediaStream | null
      prev?.getTracks().forEach((t) => t.stop())
      video.pause()
      video.srcObject = stream

      await new Promise<void>((resolve, reject) => {
        const onReady = () => {
          video.removeEventListener('loadedmetadata', onReady)
          resolve()
        }
        if (video.readyState >= 1) resolve()
        else {
          video.addEventListener('loadedmetadata', onReady)
          video.addEventListener(
            'error',
            () => reject(new Error('Camera failed to load')),
            { once: true },
          )
        }
      })

      if (gen !== camGen.current) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }

      try {
        await video.play()
      } catch (err) {
        // React Strict Mode / remount often aborts the first play()
        if ((err as DOMException)?.name === 'AbortError') return
        throw err
      }

      if (gen !== camGen.current) return

      engineRef.current?.stop()
      const engine = new Tier1Engine({
        video,
        overlay,
        onTick: (tick: Tier1Tick) => {
          if (!autoInspectRef.current) {
            setStability(tick.moving ? 'motion (auto off)' : 'still (auto off)')
            return
          }
          if (tick.inspecting || inspectBusy.current) {
            setStability('inspecting…')
            return
          }
          if (tick.cooldownRemainingMs > 0) {
            setStability(`cooldown ${(tick.cooldownRemainingMs / 1000).toFixed(1)}s`)
            return
          }
          if (tick.moving) {
            setStability('motion')
            return
          }
          const need = (tick.targetStableMs / 1000).toFixed(1)
          const have = Math.min(tick.stableMs, tick.targetStableMs) / 1000
          setStability(
            tick.stableMs >= tick.targetStableMs
              ? 'stable ✓ → inspect'
              : `holding ${have.toFixed(1)}/${need}s`,
          )
        },
        onStable: () => {
          if (autoInspectRef.current) void runOrientationRef.current('auto')
        },
      })
      engine.setAutoInspect(autoInspectRef.current)
      engine.start()
      engineRef.current = engine
      setCameraOn(true)
      setStability('camera ready')
    } catch (err) {
      if (gen !== camGen.current) return
      if ((err as DOMException)?.name === 'AbortError') return
      setError(String((err as Error).message || err))
      setCameraOn(false)
    }
  }, [])

  useEffect(() => {
    void startCamera()
    return () => stopCamera()
  }, [startCamera, stopCamera])

  useEffect(() => {
    engineRef.current?.setAutoInspect(autoInspect)
  }, [autoInspect])

  useEffect(() => {
    if (!onCaptureReady) return
    onCaptureReady(() => {
      try {
        return engineRef.current?.captureJpegBase64(1280, 0.88) ?? null
      } catch {
        return null
      }
    })
    return () => onCaptureReady(null)
  }, [onCaptureReady])

  async function runPart() {
    const sessionId = context.sessionId
    const engine = engineRef.current
    if (!sessionId || !engine || inspectBusy.current) return
    inspectBusy.current = true
    engine.setInspecting(true)
    setModeBadge('IDENTIFY')
    setModeClass('inspecting')
    setError(null)
    try {
      const result = await inspectPart(sessionId, engine.captureJpegBase64())
      setModeBadge(result.partId ? `PART ${result.partId}` : 'UNKNOWN')
      setModeClass(result.partId ? 'ok' : 'bad')
      const sess = await getSession(sessionId)
      onContextChange({
        productId: sess.state.productId,
        sessionId,
        sessionState: sess.state,
      })
    } catch (err) {
      setModeBadge('ERROR')
      setModeClass('bad')
      setError(String((err as Error).message || err))
    } finally {
      engine.markInspected()
      engine.setInspecting(false)
      inspectBusy.current = false
    }
  }

  async function runInventory() {
    const sessionId = context.sessionId
    const engine = engineRef.current
    if (!sessionId || !engine || inspectBusy.current) return
    inspectBusy.current = true
    engine.setInspecting(true)
    setModeBadge('INVENTORY')
    setModeClass('inspecting')
    setError(null)
    try {
      await inspectInventory(sessionId, engine.captureJpegBase64(1280, 0.88))
      setModeBadge('INVENTORY OK')
      setModeClass('ok')
      const sess = await getSession(sessionId)
      onContextChange({
        productId: sess.state.productId,
        sessionId,
        sessionState: sess.state,
      })
    } catch (err) {
      setModeBadge('ERROR')
      setModeClass('bad')
      setError(String((err as Error).message || err))
    } finally {
      engine.markInspected()
      engine.setInspecting(false)
      inspectBusy.current = false
    }
  }

  const step = context.sessionState?.currentStep
  const diagramSrc =
    context.productId && step ? stepDiagramUrl(context.productId, step) : null

  const hasSession = Boolean(context.sessionId)
  const canInspect = hasSession && cameraOn

  return (
    <aside className="debug-panel" aria-label="Debug mode">
      <div className="debug-section">
        <div className="debug-h">
          <h2>Status</h2>
          <label className="handsfree">
            <input
              type="checkbox"
              checked={handsFree}
              onChange={(e) => onHandsFreeChange(e.target.checked)}
            />
            Hands-free
          </label>
        </div>
        <div className="pill-row">
          <span className="pill">{status.mcp}</span>
          <span className="pill">{status.tools}</span>
          <span className={`pill${hasSession ? ' on' : ''}`}>
            {hasSession
              ? `session ${context.sessionId!.slice(0, 6)}…`
              : 'waiting for chat…'}
          </span>
          <span className="pill">{status.voice}</span>
        </div>
      </div>

      <div className="debug-section">
        <div className="debug-h">
          <h2>Camera · Tier-1</h2>
          <span className={`mode-badge ${modeClass}`}>{modeBadge}</span>
        </div>
        <div className="cam-stage">
          <video ref={videoRef} playsInline muted />
          <canvas ref={overlayRef} />
          <div className="cam-hud">
            <span>{stability}</span>
          </div>
        </div>
        <div className="debug-actions">
          {!cameraOn ? (
            <button type="button" className="dbg-btn accent" onClick={() => void startCamera()}>
              Start camera
            </button>
          ) : (
            <button type="button" className="dbg-btn" onClick={stopCamera}>
              Stop camera
            </button>
          )}
          <label className="handsfree">
            <input
              type="checkbox"
              checked={autoInspect}
              onChange={(e) => setAutoInspect(e.target.checked)}
              disabled={!hasSession}
            />
            Auto-inspect
          </label>
        </div>
        <div className="debug-actions">
          <button
            type="button"
            className="dbg-btn"
            disabled={!canInspect}
            onClick={() => void runOrientation('manual')}
          >
            Orientation
          </button>
          <button
            type="button"
            className="dbg-btn"
            disabled={!canInspect}
            onClick={() => void runPart()}
          >
            Identify part
          </button>
          <button
            type="button"
            className="dbg-btn"
            disabled={!canInspect}
            onClick={() => void runInventory()}
          >
            Inventory
          </button>
        </div>
        {!hasSession && (
          <p className="debug-hint">Ask Alexa to start assembling — session appears here.</p>
        )}
        {error && <p className="debug-err">{error}</p>}
      </div>

      <div className="debug-section">
        <div className="debug-h">
          <h2>Assembly</h2>
        </div>
        <div className="meta-grid">
          <div>
            <span>product</span>
            <strong>{context.productId || '—'}</strong>
          </div>
          <div>
            <span>step</span>
            <strong>{context.sessionState?.currentStep ?? '—'}</strong>
          </div>
          <div>
            <span>mode</span>
            <strong>{context.sessionState?.activeMode ?? '—'}</strong>
          </div>
          <div>
            <span>done</span>
            <strong>
              {context.sessionState
                ? context.sessionState.stepCompleted
                  ? 'yes'
                  : 'no'
                : '—'}
            </strong>
          </div>
        </div>
        {diagramSrc && (
          <img className="step-diagram" src={diagramSrc} alt="Current step diagram" />
        )}
      </div>
    </aside>
  )
}
