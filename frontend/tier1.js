/**
 * Tier-1 local engine: frame differencing + crude motion boxes.
 * No YOLO — cheap Canvas pixel math at ~12 FPS analysis rate.
 */

const ANALYSIS_MAX_W = 160;
const STABLE_MS = 1300;
const MOTION_THRESHOLD = 28; // mean abs grayscale delta (was 18 — too twitchy)
const PIXEL_HOT = 40;
const COOLDOWN_MS = 10000; // wait after an inspect before auto-firing again
const MIN_BLOB_PIXELS = 80; // ignore tiny noise blobs at analysis resolution
const MIN_BOX_FRAC = 0.04; // ignore boxes smaller than ~4% of frame edge

export class Tier1Engine {
  /**
   * @param {object} opts
   * @param {HTMLVideoElement} opts.video
   * @param {HTMLCanvasElement} opts.overlay
   * @param {(info: object) => void} opts.onTick
   * @param {() => void} opts.onStable
   */
  constructor({ video, overlay, onTick, onStable }) {
    this.video = video;
    this.overlay = overlay;
    this.onTick = onTick;
    this.onStable = onStable;

    this._work = document.createElement("canvas");
    this._prev = null;
    this._stableSince = null;
    this._lastInspectAt = 0;
    this._raf = 0;
    this._lastSample = 0;
    this._running = false;
    this._autoInspect = false; // off unless the UI checkbox is enabled
    this._inspecting = false;
    this._resultBox = null;
    this._lastMotionBoxes = [];
    this._emaMean = 0;
  }

  setAutoInspect(on) {
    this._autoInspect = on;
  }

  setInspecting(on) {
    this._inspecting = on;
  }

  get inspecting() {
    return this._inspecting;
  }

  captureWidth(maxWidth = 960) {
    if (!this.video.videoWidth) return maxWidth;
    return Math.round(Math.min(this.video.videoWidth, maxWidth));
  }

  /** @param {{x:number,y:number,w:number,h:number}|null} box @param {boolean} ok */
  setResultBox(box, ok) {
    this._resultBox = box ? { ...box, ok } : null;
    this._drawOverlay(this._lastMotionBoxes);
  }

  clearResultBox() {
    this._resultBox = null;
    this._drawOverlay(this._lastMotionBoxes);
  }

  markInspected() {
    this._lastInspectAt = performance.now();
    this._stableSince = null;
  }

  start() {
    if (this._running) return;
    this._running = true;
    this._loop();
  }

  stop() {
    this._running = false;
    if (this._raf) cancelAnimationFrame(this._raf);
  }

  _loop = () => {
    if (!this._running) return;
    this._raf = requestAnimationFrame(this._loop);
    const now = performance.now();
    // Analyze ~12 FPS; do NOT redraw/clear between samples (that caused flashing).
    if (now - this._lastSample < 80) return;
    this._lastSample = now;
    this._sample(now);
  };

  _sample(now) {
    const video = this.video;
    if (!video.videoWidth) return;

    const scale = Math.min(1, ANALYSIS_MAX_W / video.videoWidth);
    const w = Math.max(32, Math.floor(video.videoWidth * scale));
    const h = Math.max(24, Math.floor(video.videoHeight * scale));
    this._work.width = w;
    this._work.height = h;
    const ctx = this._work.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(video, 0, 0, w, h);
    const frame = ctx.getImageData(0, 0, w, h);
    const gray = new Uint8ClampedArray(w * h);
    for (let i = 0, p = 0; i < frame.data.length; i += 4, p++) {
      gray[p] =
        (frame.data[i] * 0.299 +
          frame.data[i + 1] * 0.587 +
          frame.data[i + 2] * 0.114) |
        0;
    }

    let boxes = [];
    let moving = false;
    if (this._prev && this._prev.length === gray.length) {
      let sum = 0;
      const hot = new Uint8Array(w * h);
      for (let i = 0; i < gray.length; i++) {
        const d = Math.abs(gray[i] - this._prev[i]);
        sum += d;
        if (d >= PIXEL_HOT) hot[i] = 1;
      }
      const mean = sum / gray.length;
      this._emaMean = this._emaMean * 0.7 + mean * 0.3;
      moving = this._emaMean >= MOTION_THRESHOLD;
      if (moving) {
        boxes = this._boxesFromHot(
          hot,
          w,
          h,
          video.videoWidth / w,
          video.videoHeight / h
        );
      }

      if (moving) {
        this._stableSince = null;
      } else if (this._stableSince == null) {
        this._stableSince = now;
      }
    }

    this._prev = gray;
    this._lastMotionBoxes = boxes;

    const stableMs = this._stableSince ? now - this._stableSince : 0;
    const sinceInspect = now - this._lastInspectAt;
    const cooldownRemainingMs = Math.max(0, COOLDOWN_MS - sinceInspect);
    const cooled = cooldownRemainingMs <= 0;
    const readyToInspect =
      this._autoInspect &&
      !this._inspecting &&
      cooled &&
      stableMs >= STABLE_MS;

    this.onTick?.({
      stableMs,
      moving,
      boxes,
      cooldownRemainingMs,
      inspecting: this._inspecting,
      readyToInspect,
      targetStableMs: STABLE_MS,
    });
    this._drawOverlay(boxes);

    if (readyToInspect) {
      this._stableSince = null;
      this.onStable?.();
    }
  }

  _boxesFromHot(hot, w, h, sx, sy) {
    const visited = new Uint8Array(w * h);
    const out = [];
    const dirs = [1, -1, w, -w];
    const minEdge = Math.min(w * sx, h * sy) * MIN_BOX_FRAC;

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const start = y * w + x;
        if (!hot[start] || visited[start]) continue;
        let minX = x,
          maxX = x,
          minY = y,
          maxY = y,
          count = 0;
        const stack = [start];
        visited[start] = 1;
        while (stack.length) {
          const i = stack.pop();
          count++;
          const cx = i % w;
          const cy = (i / w) | 0;
          minX = Math.min(minX, cx);
          maxX = Math.max(maxX, cx);
          minY = Math.min(minY, cy);
          maxY = Math.max(maxY, cy);
          for (const d of dirs) {
            const n = i + d;
            if (n < 0 || n >= hot.length) continue;
            if ((d === 1 || d === -1) && ((n / w) | 0) !== cy) continue;
            if (!hot[n] || visited[n]) continue;
            visited[n] = 1;
            stack.push(n);
          }
        }
        if (count < MIN_BLOB_PIXELS) continue;
        const bw = (maxX - minX + 1) * sx;
        const bh = (maxY - minY + 1) * sy;
        if (bw < minEdge || bh < minEdge) continue;
        out.push({ x: minX * sx, y: minY * sy, w: bw, h: bh });
        if (out.length >= 4) return out;
      }
    }
    return out;
  }

  _drawOverlay(motionBoxes) {
    const canvas = this.overlay;
    const video = this.video;
    if (!video.videoWidth) return;

    if (canvas.width !== video.videoWidth || canvas.height !== video.videoHeight) {
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
    }
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.lineWidth = Math.max(2, canvas.width / 400);
    ctx.strokeStyle = "rgba(61, 156, 253, 0.85)";
    ctx.fillStyle = "rgba(61, 156, 253, 0.10)";
    for (const b of motionBoxes) {
      ctx.fillRect(b.x, b.y, b.w, b.h);
      ctx.strokeRect(b.x, b.y, b.w, b.h);
    }

    if (this._resultBox) {
      const b = this._resultBox;
      ctx.strokeStyle = b.ok ? "rgba(61, 207, 142, 0.95)" : "rgba(255, 92, 92, 0.95)";
      ctx.fillStyle = b.ok ? "rgba(61, 207, 142, 0.15)" : "rgba(255, 92, 92, 0.15)";
      ctx.lineWidth = Math.max(3, canvas.width / 280);
      ctx.fillRect(b.x, b.y, b.w, b.h);
      ctx.strokeRect(b.x, b.y, b.w, b.h);
      ctx.font = `600 ${Math.max(14, canvas.width / 40)}px Outfit, sans-serif`;
      ctx.fillStyle = b.ok ? "#3dcf8e" : "#ff5c5c";
      ctx.fillText(b.ok ? "ALIGNED" : "CHECK THIS", b.x + 6, Math.max(18, b.y - 8));
    }
  }

  captureJpegBase64(maxWidth = 960, quality = 0.72) {
    const video = this.video;
    if (!video.videoWidth) throw new Error("Camera not ready");
    const scale = Math.min(1, maxWidth / video.videoWidth);
    const w = Math.round(video.videoWidth * scale);
    const h = Math.round(video.videoHeight * scale);
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    c.getContext("2d").drawImage(video, 0, 0, w, h);
    const dataUrl = c.toDataURL("image/jpeg", quality);
    return dataUrl.split(",", 2)[1];
  }
}
