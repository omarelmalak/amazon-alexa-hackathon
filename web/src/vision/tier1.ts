/** Tier-1 local engine: frame differencing + motion boxes (~12 FPS). */

const ANALYSIS_MAX_W = 160
const STABLE_MS = 1300
const MOTION_THRESHOLD = 28
const PIXEL_HOT = 40
const COOLDOWN_MS = 10000
const MIN_BLOB_PIXELS = 80
const MIN_BOX_FRAC = 0.04

export type Box = { x: number; y: number; w: number; h: number }
export type ResultBox = Box & { ok: boolean }

export type Tier1Tick = {
  stableMs: number
  moving: boolean
  boxes: Box[]
  cooldownRemainingMs: number
  inspecting: boolean
  readyToInspect: boolean
  targetStableMs: number
}

type Opts = {
  video: HTMLVideoElement
  overlay: HTMLCanvasElement
  onTick?: (info: Tier1Tick) => void
  onStable?: () => void
}

export class Tier1Engine {
  private video: HTMLVideoElement
  private overlay: HTMLCanvasElement
  private onTick?: (info: Tier1Tick) => void
  private onStable?: () => void
  private work = document.createElement('canvas')
  private prev: Uint8ClampedArray | null = null
  private stableSince: number | null = null
  private lastInspectAt = 0
  private raf = 0
  private lastSample = 0
  private running = false
  private autoInspect = false
  private _inspecting = false
  private resultBox: ResultBox | null = null
  private lastMotionBoxes: Box[] = []
  private emaMean = 0

  constructor({ video, overlay, onTick, onStable }: Opts) {
    this.video = video
    this.overlay = overlay
    this.onTick = onTick
    this.onStable = onStable
  }

  setAutoInspect(on: boolean) {
    this.autoInspect = on
  }

  setInspecting(on: boolean) {
    this._inspecting = on
  }

  get inspecting() {
    return this._inspecting
  }

  captureWidth(maxWidth = 960) {
    if (!this.video.videoWidth) return maxWidth
    return Math.round(Math.min(this.video.videoWidth, maxWidth))
  }

  setResultBox(box: Box | null, ok: boolean) {
    this.resultBox = box ? { ...box, ok } : null
    this.drawOverlay(this.lastMotionBoxes)
  }

  clearResultBox() {
    this.resultBox = null
    this.drawOverlay(this.lastMotionBoxes)
  }

  markInspected() {
    this.lastInspectAt = performance.now()
    this.stableSince = null
  }

  start() {
    if (this.running) return
    this.running = true
    this.loop()
  }

  stop() {
    this.running = false
    if (this.raf) cancelAnimationFrame(this.raf)
  }

  private loop = () => {
    if (!this.running) return
    this.raf = requestAnimationFrame(this.loop)
    const now = performance.now()
    if (now - this.lastSample < 80) return
    this.lastSample = now
    this.sample(now)
  }

  private sample(now: number) {
    const video = this.video
    if (!video.videoWidth) return

    const scale = Math.min(1, ANALYSIS_MAX_W / video.videoWidth)
    const w = Math.max(32, Math.floor(video.videoWidth * scale))
    const h = Math.max(24, Math.floor(video.videoHeight * scale))
    this.work.width = w
    this.work.height = h
    const ctx = this.work.getContext('2d', { willReadFrequently: true })
    if (!ctx) return
    ctx.drawImage(video, 0, 0, w, h)
    const frame = ctx.getImageData(0, 0, w, h)
    const gray = new Uint8ClampedArray(w * h)
    for (let i = 0, p = 0; i < frame.data.length; i += 4, p++) {
      gray[p] =
        (frame.data[i] * 0.299 + frame.data[i + 1] * 0.587 + frame.data[i + 2] * 0.114) | 0
    }

    let boxes: Box[] = []
    let moving = false
    if (this.prev && this.prev.length === gray.length) {
      let sum = 0
      const hot = new Uint8Array(w * h)
      for (let i = 0; i < gray.length; i++) {
        const d = Math.abs(gray[i]! - this.prev[i]!)
        sum += d
        if (d >= PIXEL_HOT) hot[i] = 1
      }
      const mean = sum / gray.length
      this.emaMean = this.emaMean * 0.7 + mean * 0.3
      moving = this.emaMean >= MOTION_THRESHOLD
      if (moving) {
        boxes = this.boxesFromHot(hot, w, h, video.videoWidth / w, video.videoHeight / h)
      }
      if (moving) this.stableSince = null
      else if (this.stableSince == null) this.stableSince = now
    }

    this.prev = gray
    this.lastMotionBoxes = boxes

    const stableMs = this.stableSince ? now - this.stableSince : 0
    const sinceInspect = now - this.lastInspectAt
    const cooldownRemainingMs = Math.max(0, COOLDOWN_MS - sinceInspect)
    const cooled = cooldownRemainingMs <= 0
    const readyToInspect =
      this.autoInspect && !this._inspecting && cooled && stableMs >= STABLE_MS

    this.onTick?.({
      stableMs,
      moving,
      boxes,
      cooldownRemainingMs,
      inspecting: this._inspecting,
      readyToInspect,
      targetStableMs: STABLE_MS,
    })
    this.drawOverlay(boxes)

    if (readyToInspect) {
      this.stableSince = null
      this.onStable?.()
    }
  }

  private boxesFromHot(hot: Uint8Array, w: number, h: number, sx: number, sy: number) {
    const visited = new Uint8Array(w * h)
    const out: Box[] = []
    const dirs = [1, -1, w, -w]
    const minEdge = Math.min(w * sx, h * sy) * MIN_BOX_FRAC

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const start = y * w + x
        if (!hot[start] || visited[start]) continue
        let minX = x
        let maxX = x
        let minY = y
        let maxY = y
        let count = 0
        const stack = [start]
        visited[start] = 1
        while (stack.length) {
          const i = stack.pop()!
          count++
          const cx = i % w
          const cy = (i / w) | 0
          minX = Math.min(minX, cx)
          maxX = Math.max(maxX, cx)
          minY = Math.min(minY, cy)
          maxY = Math.max(maxY, cy)
          for (const d of dirs) {
            const n = i + d
            if (n < 0 || n >= hot.length) continue
            if ((d === 1 || d === -1) && ((n / w) | 0) !== cy) continue
            if (!hot[n] || visited[n]) continue
            visited[n] = 1
            stack.push(n)
          }
        }
        if (count < MIN_BLOB_PIXELS) continue
        const bw = (maxX - minX + 1) * sx
        const bh = (maxY - minY + 1) * sy
        if (bw < minEdge || bh < minEdge) continue
        out.push({ x: minX * sx, y: minY * sy, w: bw, h: bh })
        if (out.length >= 4) return out
      }
    }
    return out
  }

  private drawOverlay(motionBoxes: Box[]) {
    const canvas = this.overlay
    const video = this.video
    if (!video.videoWidth) return

    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
    }
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    ctx.lineWidth = Math.max(2, canvas.width / 400)
    ctx.strokeStyle = 'rgba(0, 202, 255, 0.85)'
    ctx.fillStyle = 'rgba(0, 202, 255, 0.12)'
    for (const b of motionBoxes) {
      ctx.fillRect(b.x, b.y, b.w, b.h)
      ctx.strokeRect(b.x, b.y, b.w, b.h)
    }

    if (this.resultBox) {
      const b = this.resultBox
      ctx.strokeStyle = b.ok ? 'rgba(93, 222, 160, 0.95)' : 'rgba(255, 143, 143, 0.95)'
      ctx.fillStyle = b.ok ? 'rgba(93, 222, 160, 0.15)' : 'rgba(255, 143, 143, 0.15)'
      ctx.lineWidth = Math.max(3, canvas.width / 280)
      ctx.fillRect(b.x, b.y, b.w, b.h)
      ctx.strokeRect(b.x, b.y, b.w, b.h)
      ctx.font = `600 ${Math.max(14, canvas.width / 40)}px Manrope, sans-serif`
      ctx.fillStyle = b.ok ? '#5ddea0' : '#ff8f8f'
      ctx.fillText(b.ok ? 'ALIGNED' : 'CHECK THIS', b.x + 6, Math.max(18, b.y - 8))
    }
  }

  captureJpegBase64(maxWidth = 1280, quality = 0.88) {
    const video = this.video
    if (!video.videoWidth) throw new Error('Camera not ready')
    const scale = Math.min(1, maxWidth / video.videoWidth)
    const w = Math.round(video.videoWidth * scale)
    const h = Math.round(video.videoHeight * scale)
    const c = document.createElement('canvas')
    c.width = w
    c.height = h
    c.getContext('2d')!.drawImage(video, 0, 0, w, h)
    return c.toDataURL('image/jpeg', quality).split(',', 2)[1]!
  }
}
