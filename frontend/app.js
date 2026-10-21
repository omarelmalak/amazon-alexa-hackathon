import { Tier1Engine } from "./tier1.js";

const $ = (id) => document.getElementById(id);

const ui = {
  video: $("webcam"),
  overlay: $("overlay"),
  productSelect: $("productSelect"),
  btnStartCam: $("btnStartCam"),
  btnInspectOrientation: $("btnInspectOrientation"),
  btnInspectPart: $("btnInspectPart"),
  btnInspectInventory: $("btnInspectInventory"),
  btnTestMatch: $("btnTestMatch"),
  btnTestMismatch: $("btnTestMismatch"),
  btnTestPartBom: $("btnTestPartBom"),
  btnScanBom: $("btnScanBom"),
  btnDemoMissing: $("btnDemoMissing"),
  btnOrderMissing: $("btnOrderMissing"),
  btnStartSession: $("btnStartSession"),
  btnAdvance: $("btnAdvance"),
  autoInspect: $("autoInspect"),
  modeBadge: $("modeBadge"),
  stabilityMeter: $("stabilityMeter"),
  connectionStatus: $("connectionStatus"),
  metaSession: $("metaSession"),
  metaStep: $("metaStep"),
  metaMode: $("metaMode"),
  metaDone: $("metaDone"),
  stepDiagram: $("stepDiagram"),
  stepTitle: $("stepTitle"),
  hintsList: $("hintsList"),
  inspectOut: $("inspectOut"),
  inventoryOut: $("inventoryOut"),
  orderOut: $("orderOut"),
};

let sessionId = null;
let sessionState = null;
let tier1 = null;
/** Last inventory inspect payload (counted/missing only — ordering is a separate call). */
let lastInventory = null;
let inspectBusy = false;

async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const detail = body?.detail || text || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function setConn(msg, ok = true) {
  ui.connectionStatus.textContent = msg;
  ui.connectionStatus.style.borderColor = ok ? "" : "var(--bad)";
  ui.connectionStatus.style.color = ok ? "" : "var(--bad)";
}

function syncAutoInspect() {
  const on = Boolean(ui.autoInspect.checked);
  tier1?.setAutoInspect(on);
  if (!on) {
    ui.stabilityMeter.textContent = "auto-inspect off";
    ui.stabilityMeter.classList.remove("stable");
  }
}

function renderSession() {
  const hasSession = Boolean(sessionState);
  const hasCam = Boolean(ui.video.srcObject);

  if (!hasSession) {
    ui.metaSession.textContent = "—";
    ui.metaStep.textContent = "—";
    ui.metaMode.textContent = "—";
    ui.metaDone.textContent = "—";
  } else {
    ui.metaSession.textContent = sessionId?.slice(0, 10) + "…";
    ui.metaStep.textContent = String(sessionState.currentStep);
    ui.metaMode.textContent = sessionState.activeMode;
    ui.metaDone.textContent = sessionState.stepCompleted ? "yes" : "no";
  }

  ui.btnAdvance.disabled = !hasSession;
  ui.btnInspectOrientation.disabled = !(hasSession && hasCam);
  ui.btnInspectPart.disabled = !(hasSession && hasCam);
  ui.btnInspectInventory.disabled = !(hasSession && hasCam);
  ui.btnTestMatch.disabled = !hasSession;
  ui.btnTestMismatch.disabled = !hasSession;
  ui.btnTestPartBom.disabled = !hasSession;
  ui.btnScanBom.disabled = !hasSession;
  ui.btnDemoMissing.disabled = !hasSession;
  const hasMissing =
    Array.isArray(lastInventory?.missing) && lastInventory.missing.length > 0;
  ui.btnOrderMissing.disabled = !(hasSession && hasMissing);
}

async function loadProducts() {
  const products = await api("/v1/products");
  ui.productSelect.innerHTML = "";
  for (const p of products) {
    const opt = document.createElement("option");
    opt.value = p.productId;
    opt.textContent = `${p.displayName} (${p.stepCount} steps)`;
    ui.productSelect.appendChild(opt);
  }
  if (!products.length) {
    setConn("No products in data/", false);
  }
}

async function refreshStepPanel() {
  if (!sessionState) return;
  const productId = sessionState.productId;
  const step = sessionState.currentStep;
  const meta = await api(`/v1/products/${productId}/steps/${step}`);
  ui.stepTitle.textContent = `Step ${meta.number}: ${meta.title || ""}`;
  ui.stepDiagram.src = `/v1/products/${productId}/steps/${step}/diagram?t=${Date.now()}`;
  ui.hintsList.innerHTML = "";
  for (const h of meta.completionHints || []) {
    const li = document.createElement("li");
    li.textContent = h;
    ui.hintsList.appendChild(li);
  }
}

async function startSession() {
  const productId = ui.productSelect.value;
  if (!productId) return;
  const created = await api("/v1/sessions", {
    method: "POST",
    body: JSON.stringify({ productId }),
  });
  sessionId = created.sessionId;
  sessionState = created.state;
  lastInventory = null;
  ui.orderOut.textContent = "";
  // Never turn auto-inspect on just because a session started.
  ui.autoInspect.checked = false;
  syncAutoInspect();
  tier1?.clearResultBox();
  renderSession();
  await refreshStepPanel();
  setConn(`Session ${sessionId.slice(0, 8)}…`);
}

async function advanceSession() {
  if (!sessionId) return;
  const res = await api(`/v1/sessions/${sessionId}/advance`, { method: "POST" });
  sessionState = res.state;
  tier1?.clearResultBox();
  renderSession();
  await refreshStepPanel();
}

async function startCamera() {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "environment", width: { ideal: 1280 }, height: { ideal: 720 } },
    audio: false,
  });
  ui.video.srcObject = stream;
  await ui.video.play();

  tier1 = new Tier1Engine({
    video: ui.video,
    overlay: ui.overlay,
    onTick: ({
      stableMs,
      moving,
      cooldownRemainingMs,
      inspecting,
      targetStableMs,
    }) => {
      // Stability meter is informational only unless auto-inspect is enabled.
      if (!ui.autoInspect.checked) {
        ui.stabilityMeter.textContent = moving ? "motion (auto off)" : "still (auto off)";
        ui.stabilityMeter.classList.remove("stable");
        return;
      }
      ui.stabilityMeter.classList.remove("stable");
      if (inspecting || inspectBusy) {
        ui.stabilityMeter.textContent = "inspecting…";
        return;
      }
      if (cooldownRemainingMs > 0) {
        ui.stabilityMeter.textContent = `cooldown ${(cooldownRemainingMs / 1000).toFixed(1)}s`;
        return;
      }
      if (moving) {
        ui.stabilityMeter.textContent = "motion";
        return;
      }
      const need = (targetStableMs / 1000).toFixed(1);
      const have = Math.min(stableMs, targetStableMs) / 1000;
      if (stableMs >= targetStableMs) {
        ui.stabilityMeter.textContent = `stable ✓ → inspect`;
        ui.stabilityMeter.classList.add("stable");
      } else {
        ui.stabilityMeter.textContent = `holding ${have.toFixed(1)}/${need}s`;
      }
    },
    onStable: () => {
      if (!ui.autoInspect.checked) return;
      void runOrientation("auto");
    },
  });
  syncAutoInspect();
  tier1.start();

  ui.btnStartCam.textContent = "Camera on";
  ui.btnStartCam.disabled = true;
  renderSession();
  setConn("Camera ready");
}

async function blobToBase64(blob) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  return String(dataUrl).split(",", 2)[1];
}

async function fetchStepDiagramBase64(productId, stepNumber) {
  const res = await fetch(
    `/v1/products/${productId}/steps/${stepNumber}/diagram?t=${Date.now()}`
  );
  if (!res.ok) throw new Error(`Failed to load diagram for step ${stepNumber}`);
  return blobToBase64(await res.blob());
}

async function fetchBomDiagramBase64(productId) {
  const res = await fetch(`/v1/products/${productId}/bom/diagram?t=${Date.now()}`);
  if (!res.ok) throw new Error("BOM diagram missing");
  return blobToBase64(await res.blob());
}

async function scribbleFrameBase64(frameBase64) {
  const img = await new Promise((resolve, reject) => {
    const el = new Image();
    el.onload = () => resolve(el);
    el.onerror = reject;
    el.src = `data:image/png;base64,${frameBase64}`;
  });
  const c = document.createElement("canvas");
  c.width = img.naturalWidth;
  c.height = img.naturalHeight;
  const ctx = c.getContext("2d");
  ctx.drawImage(img, 0, 0);
  ctx.strokeStyle = "#ff0000";
  ctx.lineWidth = Math.max(24, c.width / 30);
  ctx.beginPath();
  ctx.moveTo(0, 0);
  ctx.lineTo(c.width, c.height);
  ctx.moveTo(0, c.height);
  ctx.lineTo(c.width, 0);
  ctx.stroke();
  ctx.strokeRect(16, 16, c.width - 32, c.height - 32);
  return c.toDataURL("image/jpeg", 0.85).split(",", 2)[1];
}

function captureWebcamBase64() {
  if (!tier1) throw new Error("Start camera first");
  return tier1.captureJpegBase64(960, 0.72);
}

function paintOrientationOverlay(result) {
  if (!tier1 || !ui.video.videoWidth) return;
  const ok = Boolean(result.aligned);
  if (result.boundingBox) {
    const capW = tier1.captureWidth(960);
    const scale = ui.video.videoWidth / capW;
    const b = result.boundingBox;
    tier1.setResultBox(
      {
        x: b.x * scale,
        y: b.y * scale,
        w: b.w * scale,
        h: b.h * scale,
      },
      ok
    );
  } else {
    tier1.setResultBox(
      {
        x: ui.video.videoWidth * 0.04,
        y: ui.video.videoHeight * 0.04,
        w: ui.video.videoWidth * 0.92,
        h: ui.video.videoHeight * 0.92,
      },
      ok
    );
  }
}

async function runOrientation(reason, frameBase64 = null) {
  if (!sessionId || inspectBusy) return;
  if (frameBase64 == null && !tier1) {
    setConn("Start camera first, or use a Test button", false);
    return;
  }

  inspectBusy = true;
  tier1?.setInspecting(true);
  ui.modeBadge.textContent = "ORIENTATION";
  ui.modeBadge.className = "badge inspecting";
  ui.inspectOut.textContent = `Orientation (${reason})…`;

  try {
    const payload = frameBase64 ?? captureWebcamBase64();
    const result = await api(`/v1/sessions/${sessionId}/inspect/orientation`, {
      method: "POST",
      body: JSON.stringify({ frameBase64: payload }),
    });
    ui.inspectOut.textContent = JSON.stringify(result, null, 2);
    const ok = Boolean(result.aligned);
    ui.modeBadge.textContent = ok ? "ALIGNED" : "MISALIGNED";
    ui.modeBadge.className = `badge ${ok ? "ok" : "bad"}`;
    paintOrientationOverlay(result);

    const sess = await api(`/v1/sessions/${sessionId}`);
    sessionState = sess.state;
    renderSession();

    if (result.aligned && result.stepCompleted) {
      ui.inspectOut.textContent +=
        "\n\n→ stepCompleted=true — advancing to next step…";
      await advanceSession();
    }
  } catch (err) {
    ui.inspectOut.textContent = String(err.message || err);
    ui.modeBadge.textContent = "ERROR";
    ui.modeBadge.className = "badge bad";
    setConn(String(err.message || err), false);
  } finally {
    tier1?.markInspected();
    tier1?.setInspecting(false);
    inspectBusy = false;
  }
}

async function runIdentifyPart(reason, frameBase64 = null) {
  if (!sessionId || inspectBusy) return;
  if (frameBase64 == null && !tier1) {
    setConn("Start camera first, or use Test: from BOM image", false);
    return;
  }

  inspectBusy = true;
  tier1?.setInspecting(true);
  ui.modeBadge.textContent = "IDENTIFY PART";
  ui.modeBadge.className = "badge inspecting";
  ui.inspectOut.textContent = `Identify part (${reason})…`;

  try {
    const payload = frameBase64 ?? captureWebcamBase64();
    const result = await api(`/v1/sessions/${sessionId}/inspect/part`, {
      method: "POST",
      body: JSON.stringify({ frameBase64: payload }),
    });
    ui.inspectOut.textContent = JSON.stringify(result, null, 2);
    ui.modeBadge.textContent = result.partId
      ? `PART ${result.partId}`
      : "PART UNKNOWN";
    ui.modeBadge.className = `badge ${result.partId ? "ok" : "bad"}`;
    const sess = await api(`/v1/sessions/${sessionId}`);
    sessionState = sess.state;
    renderSession();
  } catch (err) {
    ui.inspectOut.textContent = String(err.message || err);
    ui.modeBadge.textContent = "ERROR";
    ui.modeBadge.className = "badge bad";
    setConn(String(err.message || err), false);
  } finally {
    tier1?.markInspected();
    tier1?.setInspecting(false);
    inspectBusy = false;
  }
}

async function runInventory(reason, frameBase64 = null) {
  if (!sessionId || inspectBusy) return;
  if (frameBase64 == null && !tier1) {
    setConn("Start camera first, or use Test: scan BOM", false);
    return;
  }

  inspectBusy = true;
  tier1?.setInspecting(true);
  ui.modeBadge.textContent = "INVENTORY";
  ui.modeBadge.className = "badge inspecting";
  ui.inventoryOut.textContent = `Inventory (${reason})…`;

  try {
    const payload = frameBase64 ?? captureWebcamBase64();
    const result = await api(`/v1/sessions/${sessionId}/inspect/inventory`, {
      method: "POST",
      body: JSON.stringify({ frameBase64: payload }),
    });
    lastInventory = result;
    ui.inventoryOut.textContent = JSON.stringify(result, null, 2);
    ui.inspectOut.textContent =
      `Inventory inspect (${reason}) — counted/missing only.\n` +
      `Ordering is a separate endpoint/button if you want to buy replacements.`;
    const hasMissing = (result.missing || []).length > 0;
    ui.modeBadge.textContent = hasMissing ? "MISSING PARTS" : "BOM OK";
    ui.modeBadge.className = `badge ${hasMissing ? "bad" : "ok"}`;
    ui.orderOut.textContent = hasMissing
      ? "Inspect found missing parts. Ordering is optional — use Order missing parts."
      : "Inspect complete — nothing missing.";
    const sess = await api(`/v1/sessions/${sessionId}`);
    sessionState = sess.state;
    renderSession();
  } catch (err) {
    ui.inventoryOut.textContent = String(err.message || err);
    ui.modeBadge.textContent = "ERROR";
    ui.modeBadge.className = "badge bad";
    setConn(String(err.message || err), false);
  } finally {
    tier1?.markInspected();
    tier1?.setInspecting(false);
    inspectBusy = false;
  }
}

async function testMatchDiagram() {
  if (!sessionState) return;
  const b64 = await fetchStepDiagramBase64(
    sessionState.productId,
    sessionState.currentStep
  );
  await runOrientation("test-match-diagram", b64);
}

async function testMismatch() {
  if (!sessionState) return;
  const productId = sessionState.productId;
  const current = sessionState.currentStep;
  const steps = await api(`/v1/products/${productId}/steps`);
  const other = steps.find((s) => s.number !== current);
  if (other) {
    const b64 = await fetchStepDiagramBase64(productId, other.number);
    await runOrientation(`test-mismatch-step-${other.number}`, b64);
  } else {
    let b64 = await fetchStepDiagramBase64(productId, current);
    b64 = await scribbleFrameBase64(b64);
    await runOrientation("test-mismatch-scribble", b64);
  }
}

async function testPartFromBom() {
  if (!sessionState) return;
  const b64 = await fetchBomDiagramBase64(sessionState.productId);
  await runIdentifyPart("test-bom-image", b64);
}

async function scanInventoryFromBom() {
  if (!sessionState) return;
  const b64 = await fetchBomDiagramBase64(sessionState.productId);
  await runInventory("test-bom", b64);
}

function demoOneMissing() {
  if (!sessionState) return;
  lastInventory = {
    counted: [],
    missing: [
      {
        partId: "112357",
        name: "plastic plug",
        expectedQty: 3,
        seenQty: 2,
      },
    ],
    extras: [],
    notes: "Demo shortage (no vision call). Call the order endpoint separately to buy.",
  };
  ui.inventoryOut.textContent = JSON.stringify(lastInventory, null, 2);
  ui.orderOut.textContent =
    "Demo missing[] loaded. Press Order missing parts to hit the separate order API.";
  ui.modeBadge.textContent = "MISSING PARTS";
  ui.modeBadge.className = "badge bad";
  renderSession();
}

async function orderMissingParts() {
  if (!sessionId) return;
  const missing = lastInventory?.missing || [];
  if (!missing.length) {
    setConn("Nothing to order — run inventory inspect first (or Demo: 1 missing).", false);
    return;
  }
  const items = missing.map((m) => ({
    partId: m.partId,
    name: m.name || "",
    qty: Math.max(1, (m.expectedQty || 0) - (m.seenQty || 0)),
    unitPriceCents: 299,
  }));
  // Separate endpoint from inspect/inventory — only runs when this button is pressed.
  const order = await api(`/v1/sessions/${sessionId}/orders/replacement-parts`, {
    method: "POST",
    body: JSON.stringify({ items, confirm: true }),
  });
  ui.orderOut.innerHTML = `${order.message}<br/><a href="${order.checkoutUrl}" target="_blank" rel="noopener">Open mock checkout</a>`;
  ui.inspectOut.textContent =
    `Order endpoint response (not part of inspect):\n${JSON.stringify(order, null, 2)}`;
}

function bind() {
  ui.btnStartCam.addEventListener("click", () => {
    startCamera().catch((e) => setConn(e.message, false));
  });
  ui.btnStartSession.addEventListener("click", () => {
    startSession().catch((e) => setConn(e.message, false));
  });
  ui.btnAdvance.addEventListener("click", () => {
    advanceSession().catch((e) => setConn(e.message, false));
  });

  ui.btnInspectOrientation.addEventListener("click", () => {
    runOrientation("webcam").catch((e) => setConn(e.message, false));
  });
  ui.btnInspectPart.addEventListener("click", () => {
    runIdentifyPart("webcam").catch((e) => setConn(e.message, false));
  });
  ui.btnInspectInventory.addEventListener("click", () => {
    runInventory("webcam").catch((e) => setConn(e.message, false));
  });

  ui.btnTestMatch.addEventListener("click", () => {
    testMatchDiagram().catch((e) => setConn(e.message, false));
  });
  ui.btnTestMismatch.addEventListener("click", () => {
    testMismatch().catch((e) => setConn(e.message, false));
  });
  ui.btnTestPartBom.addEventListener("click", () => {
    testPartFromBom().catch((e) => setConn(e.message, false));
  });
  ui.btnScanBom.addEventListener("click", () => {
    scanInventoryFromBom().catch((e) => setConn(e.message, false));
  });
  ui.btnDemoMissing.addEventListener("click", () => {
    demoOneMissing();
  });
  ui.btnOrderMissing.addEventListener("click", () => {
    orderMissingParts().catch((e) => setConn(e.message, false));
  });

  ui.autoInspect.addEventListener("change", () => {
    syncAutoInspect();
  });
}

async function boot() {
  bind();
  // Explicit default: stability loop does not fire inspects.
  ui.autoInspect.checked = false;
  syncAutoInspect();
  try {
    await api("/health");
    setConn("API connected");
    await loadProducts();
  } catch (e) {
    setConn(`API unreachable: ${e.message}`, false);
  }
}

boot();
