import { McpClient } from "./mcp-client.js";

const $ = (id) => document.getElementById(id);

const ui = {
  chat: $("chat"),
  wire: $("wire"),
  chips: $("chips"),
  sayForm: $("sayForm"),
  sayInput: $("sayInput"),
  mcpPill: $("mcpPill"),
  toolPill: $("toolPill"),
  sessionPill: $("sessionPill"),
  voicePill: $("voicePill"),
  metaProduct: $("metaProduct"),
  metaSession: $("metaSession"),
  metaStep: $("metaStep"),
  metaMode: $("metaMode"),
  stepDiagram: $("stepDiagram"),
  stepTitle: $("stepTitle"),
  btnMic: $("btnMic"),
  micLabel: $("micLabel"),
  handsFree: $("handsFree"),
  listenHint: $("listenHint"),
  btnClearWire: $("btnClearWire"),
};

const state = {
  client: new McpClient("/mcp/"),
  busy: false,
  history: [],
  productId: null,
  sessionId: null,
  sessionState: null,
  lastStep: null,
  listening: false,
  recognition: null,
  openToolCards: new Map(),
};

function addChat(role, text) {
  const el = document.createElement("div");
  el.className = `bubble ${role}`;
  if (role === "alexa") {
    el.innerHTML = `<div class="who">Alexa+</div>`;
    el.append(document.createTextNode(text));
  } else {
    el.textContent = text;
  }
  ui.chat.appendChild(el);
  ui.chat.scrollTop = ui.chat.scrollHeight;
  return el;
}

function addSystem(text) {
  addChat("system", text);
}

function setPill(el, text, mode) {
  el.textContent = text;
  el.classList.remove("on", "off");
  if (mode) el.classList.add(mode);
}

function setSuggestions(phrases) {
  ui.chips.innerHTML = "";
  for (const label of phrases || []) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip";
    btn.textContent = label;
    btn.addEventListener("click", () => void handleUserTurn(label));
    ui.chips.appendChild(btn);
  }
}

function refreshMeta() {
  ui.metaProduct.textContent = state.productId || "—";
  ui.metaSession.textContent = state.sessionId
    ? `${state.sessionId.slice(0, 8)}…`
    : "—";
  ui.metaStep.textContent = state.sessionState?.currentStep ?? "—";
  ui.metaMode.textContent = state.sessionState?.activeMode ?? "—";
  setPill(
    ui.sessionPill,
    state.sessionId ? `session ${state.sessionId.slice(0, 6)}…` : "no session",
    state.sessionId ? "on" : undefined
  );
  if (state.productId && state.sessionState?.currentStep) {
    const n = state.sessionState.currentStep;
    ui.stepDiagram.hidden = false;
    ui.stepDiagram.src = `/v1/products/${state.productId}/steps/${n}/diagram?t=${Date.now()}`;
    ui.stepTitle.textContent = state.lastStep?.title || `Step ${n}`;
  }
}

function wireLine(evt) {
  const line = document.createElement("div");
  line.className = `wire-line ${evt.dir}`;
  const tag = document.createElement("span");
  tag.className = "tag";
  tag.textContent =
    evt.dir === "out" ? "→ POST /mcp" : `← ${evt.status ?? ""}`.trim();
  const pre = document.createElement("pre");
  const slim =
    typeof evt.payload === "string"
      ? evt.payload
      : JSON.stringify(evt.payload, null, 2);
  pre.textContent = slim.length > 1800 ? slim.slice(0, 1800) + "…" : slim;
  line.append(tag, pre);
  ui.wire.prepend(line);
}

function beginToolCard(name, args, id) {
  const el = document.createElement("div");
  el.className = "bubble tool";
  el.innerHTML = `<div class="tool-head"><span>MCP tools/call · ${escapeHtml(
    name
  )}</span><span class="status">running…</span></div>`;
  const pre = document.createElement("pre");
  pre.textContent = JSON.stringify(args || {}, null, 2);
  el.appendChild(pre);
  ui.chat.appendChild(el);
  ui.chat.scrollTop = ui.chat.scrollHeight;
  if (id) state.openToolCards.set(id, el);
  return el;
}

function finishToolCard(id, { ok, result, name }) {
  const el = (id && state.openToolCards.get(id)) || beginToolCard(name || "tool", {});
  const status = el.querySelector(".status");
  if (status) {
    status.textContent = ok ? "ok" : "error";
    status.classList.add(ok ? "ok" : "err");
  }
  const pre = document.createElement("pre");
  pre.textContent =
    "→ " + JSON.stringify(result, null, 2).slice(0, 1400);
  el.appendChild(pre);
  ui.chat.scrollTop = ui.chat.scrollHeight;
  if (id) state.openToolCards.delete(id);
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function updateContextFromTool(name, result) {
  if (!result || typeof result !== "object") return;
  const data = result.result !== undefined ? result.result : result;

  if (name === "ingest_product" && data.productId) {
    state.productId = data.productId;
  }
  if (name === "create_session") {
    state.sessionId = data.sessionId || state.sessionId;
    state.sessionState = data.state || state.sessionState;
    if (data.state?.productId) state.productId = data.state.productId;
  }
  if (name === "get_session" || name === "advance_session") {
    state.sessionId = data.sessionId || state.sessionId;
    state.sessionState = data.state || state.sessionState;
  }
  if (name === "get_assembly_step") {
    state.lastStep = data;
    if (data.productId) state.productId = data.productId;
  }
  refreshMeta();
}

/* ---------- Voice ---------- */

const voiceState = { elevenlabs: null, audio: null };

async function probeElevenLabs() {
  if (voiceState.elevenlabs !== null) return voiceState.elevenlabs;
  try {
    const res = await fetch("/v1/sim/tts/status");
    const body = await res.json();
    voiceState.elevenlabs = Boolean(body.elevenlabs);
  } catch {
    voiceState.elevenlabs = false;
  }
  return voiceState.elevenlabs;
}

function stopSpeech() {
  window.speechSynthesis?.cancel();
  if (voiceState.audio) {
    voiceState.audio.pause();
    voiceState.audio.src = "";
    voiceState.audio = null;
  }
}

function speakBrowser(text) {
  return new Promise((resolve) => {
    if (!window.speechSynthesis) {
      resolve();
      return;
    }
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.rate = 1.02;
    const voices = window.speechSynthesis.getVoices();
    const preferred =
      voices.find((v) => /Samantha|Google US English|Karen|Moira/i.test(v.name)) ||
      voices.find((v) => v.lang?.startsWith("en")) ||
      null;
    if (preferred) u.voice = preferred;
    u.onend = () => resolve();
    u.onerror = () => resolve();
    window.speechSynthesis.speak(u);
  });
}

async function speakElevenLabs(text) {
  const res = await fetch("/v1/sim/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) throw new Error(`TTS HTTP ${res.status}`);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  await new Promise((resolve, reject) => {
    const audio = new Audio(url);
    voiceState.audio = audio;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      voiceState.audio = null;
      resolve();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      voiceState.audio = null;
      reject(new Error("audio playback failed"));
    };
    audio.play().catch(reject);
  });
}

async function speak(text) {
  setPill(ui.voicePill, "speaking…", "on");
  stopSpeech();
  try {
    if (await probeElevenLabs()) {
      try {
        await speakElevenLabs(text);
        return;
      } catch (err) {
        console.warn("ElevenLabs TTS failed, falling back:", err);
      }
    }
    await speakBrowser(text);
  } finally {
    setPill(ui.voicePill, "voice idle");
  }
}

async function alexaSay(text, { listenAfter = true } = {}) {
  if (!text?.trim()) return;
  addChat("alexa", text);
  state.history.push({ role: "assistant", content: text });
  if (state.history.length > 24) state.history.splice(0, state.history.length - 24);
  await speak(text);
  if (listenAfter && ui.handsFree.checked && !state.busy) startListening();
}

function getRecognition() {
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Ctor) return null;
  if (state.recognition) return state.recognition;
  const rec = new Ctor();
  rec.lang = "en-US";
  rec.interimResults = false;
  rec.maxAlternatives = 1;
  rec.continuous = false;
  rec.onstart = () => {
    state.listening = true;
    ui.btnMic.classList.add("listening");
    ui.micLabel.textContent = "Listening…";
    setPill(ui.voicePill, "listening", "on");
  };
  rec.onend = () => {
    state.listening = false;
    ui.btnMic.classList.remove("listening");
    ui.micLabel.textContent = "Hold to talk";
    if (!window.speechSynthesis?.speaking) setPill(ui.voicePill, "voice idle");
  };
  rec.onerror = () => {};
  rec.onresult = (ev) => {
    const text = ev.results?.[0]?.[0]?.transcript?.trim();
    if (text) void handleUserTurn(text);
  };
  state.recognition = rec;
  return rec;
}

function startListening() {
  const rec = getRecognition();
  if (!rec) {
    ui.listenHint.textContent =
      "Speech recognition needs Chrome or Edge — you can still type below.";
    return;
  }
  if (state.listening || state.busy) return;
  stopSpeech();
  try {
    rec.start();
  } catch {
    /* already started */
  }
}

function stopListening() {
  try {
    state.recognition?.stop();
  } catch {
    /* ignore */
  }
}

/* ---------- Agentic MCP host (SSE) ---------- */

async function ensureMcpProbe() {
  if (state.client.initialized) return;
  await state.client.initialize();
  await state.client.listTools();
  setPill(ui.mcpPill, "MCP connected", "on");
  setPill(ui.toolPill, `${state.client.tools.length} tools`);
}

async function runAgent(utterance) {
  const res = await fetch("/v1/sim/agent", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({
      utterance,
      history: state.history.slice(0, -1), // exclude the user turn we just pushed visually
    }),
  });
  if (!res.ok || !res.body) {
    const t = await res.text();
    throw new Error(t.slice(0, 240) || `agent HTTP ${res.status}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let spoke = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      const line = chunk
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trim())
        .join("");
      if (!line) continue;
      let evt;
      try {
        evt = JSON.parse(line);
      } catch {
        continue;
      }
      await handleAgentEvent(evt);
      if (evt.event === "speak") spoke = true;
    }
  }
  return spoke;
}

async function handleAgentEvent(evt) {
  const type = evt.event;
  if (type === "status") {
    addSystem(evt.message || "working…");
  } else if (type === "plan") {
    addSystem(`plan: ${evt.text}`);
  } else if (type === "tools_ready") {
    addSystem(`agent tools: ${(evt.tools || []).join(", ")}`);
    setPill(ui.toolPill, `${(evt.tools || []).length} tools`);
  } else if (type === "tool_call") {
    beginToolCard(evt.name, evt.arguments, evt.id);
    wireLine({
      dir: "out",
      payload: {
        jsonrpc: "2.0",
        method: "tools/call",
        params: { name: evt.name, arguments: evt.arguments },
      },
    });
  } else if (type === "tool_result") {
    finishToolCard(evt.id, {
      ok: evt.ok !== false,
      result: evt.result,
      name: evt.name,
    });
    wireLine({
      dir: "in",
      status: evt.ok === false ? 500 : 200,
      payload: { result: evt.result },
    });
    updateContextFromTool(evt.name, evt.result);
  } else if (type === "speak") {
    await alexaSay(evt.text, { listenAfter: true });
  } else if (type === "error") {
    addSystem(`error: ${evt.message}`);
  } else if (type === "done") {
    /* noop */
  }
}

async function handleUserTurn(utterance) {
  const text = utterance.trim();
  if (!text || state.busy) return;
  state.busy = true;
  stopListening();
  stopSpeech();
  addChat("user", text);
  state.history.push({ role: "user", content: text });
  try {
    await ensureMcpProbe();
    const spoke = await runAgent(text);
    if (!spoke) {
      await alexaSay("Okay.");
    }
  } catch (err) {
    console.error(err);
    await alexaSay("Sorry — I hit a snag. Give me a second and try again.");
  } finally {
    state.busy = false;
  }
}

/* ---------- Wire UI ---------- */

state.client.onWire = wireLine;
setSuggestions();
setPill(ui.voicePill, "voice idle");

ui.sayForm.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = ui.sayInput.value.trim();
  ui.sayInput.value = "";
  if (text) void handleUserTurn(text);
});

let micPointerDown = false;
ui.btnMic.addEventListener("pointerdown", (e) => {
  e.preventDefault();
  micPointerDown = true;
  ui.btnMic.setPointerCapture?.(e.pointerId);
  startListening();
});
ui.btnMic.addEventListener("pointerup", () => {
  if (!micPointerDown) return;
  micPointerDown = false;
  setTimeout(() => stopListening(), 350);
});
ui.btnMic.addEventListener("pointercancel", () => {
  micPointerDown = false;
  stopListening();
});
ui.btnMic.addEventListener("click", (e) => {
  if (e.detail !== 0) return;
  if (state.listening) stopListening();
  else startListening();
});

ui.btnClearWire.addEventListener("click", () => {
  ui.wire.innerHTML = "";
});

if (window.speechSynthesis) {
  window.speechSynthesis.getVoices();
  window.speechSynthesis.onvoiceschanged = () =>
    window.speechSynthesis.getVoices();
}

void (async () => {
  try {
    await ensureMcpProbe();
    const g = await fetch("/v1/sim/greeting", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ context: {} }),
    }).then((r) => r.json());
    await alexaSay(g.speak || "Hi. How can I help?");
  } catch (err) {
    setPill(ui.mcpPill, "MCP failed", "off");
    console.error(err);
    await alexaSay("I'm having trouble connecting right now.");
  }
})();
