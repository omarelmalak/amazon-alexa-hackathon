/**
 * Minimal Streamable HTTP MCP client (JSON responses).
 * Speaks initialize → notifications/initialized → tools/list → tools/call.
 */
export class McpClient {
  constructor(url = "/mcp/") {
    this.url = url.endsWith("/") ? url : `${url}/`;
    this._id = 0;
    this.initialized = false;
    this.serverInfo = null;
    this.instructions = null;
    this.tools = [];
    this.onWire = null;
  }

  async initialize() {
    const result = await this._rpc("initialize", {
      protocolVersion: "2025-11-25",
      capabilities: {},
      clientInfo: { name: "flatpack-alexa-sim", version: "0.5.0" },
    });
    this.serverInfo = result.serverInfo;
    this.instructions = result.instructions || null;
    await this._notify("notifications/initialized");
    this.initialized = true;
    return result;
  }

  async listTools() {
    const result = await this._rpc("tools/list", {});
    this.tools = result.tools || [];
    return this.tools;
  }

  async callTool(name, args = {}) {
    const result = await this._rpc("tools/call", {
      name,
      arguments: args,
    });
    if (result?.isError) {
      const msg =
        result.content?.map((c) => c.text).filter(Boolean).join("\n") ||
        "tool error";
      throw new Error(msg);
    }
    return unwrapToolResult(result);
  }

  async _rpc(method, params) {
    const id = ++this._id;
    const payload = { jsonrpc: "2.0", id, method, params };
    this._emit("out", payload);
    const res = await fetch(this.url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify(payload),
    });
    const text = await res.text();
    let body;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    this._emit("in", body, res.status);
    if (!res.ok) {
      throw new Error(`MCP HTTP ${res.status}: ${text.slice(0, 240)}`);
    }
    if (body?.error) {
      throw new Error(body.error.message || JSON.stringify(body.error));
    }
    return body.result;
  }

  async _notify(method, params = {}) {
    const payload = { jsonrpc: "2.0", method, params };
    this._emit("out", payload);
    const res = await fetch(this.url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json, text/event-stream",
      },
      body: JSON.stringify(payload),
    });
    this._emit("in", { status: res.status, note: "notification ack" }, res.status);
  }

  _emit(dir, payload, status) {
    this.onWire?.({ dir, payload, status, at: Date.now() });
  }
}

export function unwrapToolResult(result) {
  if (!result) return null;
  const sc = result.structuredContent;
  if (sc && Object.prototype.hasOwnProperty.call(sc, "result") && Object.keys(sc).length === 1) {
    return sc.result;
  }
  if (sc && typeof sc === "object") return sc;
  const text = (result.content || [])
    .filter((c) => c.type === "text" && c.text)
    .map((c) => c.text)
    .join("\n");
  if (!text) return result;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function imageUrlToBase64(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Failed to load frame ${url}`);
  const blob = await res.blob();
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
  return String(dataUrl).replace(/^data:[^;]+;base64,/, "");
}
