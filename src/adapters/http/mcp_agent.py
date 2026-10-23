"""Agentic MCP host — Cohere picks tools; executions go through MCPServer.call_tool."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from mcp.server import MCPServer

from core.services.catalog import FilesystemProductCatalog
from core.services.session import InMemoryAssemblySession, SessionNotFoundError
from infra.cohere_agent import (
    chat_with_tools,
    extract_assistant_message,
    extract_text,
    extract_tool_calls,
    tool_result_message,
)
from infra.cohere_client import CohereError

VISION_TOOLS = {"inspect_orientation", "inspect_part", "inspect_inventory"}

AGENT_SYSTEM = """
You ARE Alexa+ on Echo / Fire TV. You are an agentic host with MCP tools for IKEA
flatpack assembly (Flatpack Co-Pilot).

## Brevity (critical)
- Final spoken replies: 1–2 short sentences. Cap ~35 words unless they ask for detail.
- No recap of the whole step. No "let me know if you need further assistance."
- No numbered how-to lists aloud — one action at a time.
- After tools: say only the outcome + next nudge (e.g. "Looks good — ready for step 2?").

## Personality
- Warm, tight Alexa voice. No markdown, bullets, code, or JSON aloud.
- Never mention MCP, APIs, tools, prompts, Cohere, simulators, or that you are an AI.
- Never coach the user on what exact phrase to say; just ask naturally.

## How to talk about parts (critical)
- Everyday words only. Never say confidence, percent, similarity, scores,
  "detection #", GroundingDINO, model, or part ID numbers aloud
  (e.g. do NOT say "130915" or "95% confident").
- When telling them which fastener to use, describe how it LOOKS and feels:
  length, shape, material — e.g. "the long silver hex screw", "the short
  plastic push plug", "the L-shaped allen key" — not a bare catalog name alone.
- If two parts could be confused, contrast them in one short line
  ("the long screw, not the short plug").
- After orientation checks: if aligned=false, say clearly what is wrong and
  how to fix it (e.g. "the leg is lying on the seat — stand it up so the
  holes line up"). Do not green-light a messy or half-placed setup.

## Clarify before any tools (critical)
- If the user wants help building / assembling / IKEA furniture but has NOT named a
  specific product or series (e.g. KYRRE, RISATORP, BESTÅ), do NOT call any tools.
  Reply in spoken English and ask which piece they have. Wait for their answer.
- Vague lines like "I want to build something from IKEA", "help me assemble", or
  "I just got furniture" are NOT product names — ask first, tools second.
- NEVER invent or guess a product. NEVER default to Kyrre or any catalog item.
- Until they bring up furniture/assembly/IKEA at all, stay general ("How can I help?").

## Sessions (critical)
- session_id is a long hex UUID from create_session — NEVER pass a product name
  like "KYRRE" or "kyrre" as session_id.
- If Active assembly context below lists a sessionId, REUSE it for inspect_*,
  get_session, advance_session, and orders. Do NOT call create_session again
  for the same product unless the user explicitly wants to start over.
- After naming a product the first time: ingest_product → create_session →
  list_bom, then STOP and offer a parts/tools check (see Kickoff below).
  Do NOT call get_assembly_step or jump into step 1 on that same turn.
- On later turns, skip create_session if a session is already active.

## Kickoff before step 1 (critical)
When the product is first loaded / session just created (or they just named it
and you found it in the catalog):
1) list_bom so you know the kit (and tools like the allen key).
2) Speak briefly: confirm the product + ask checklist or start.
   Example: "Got the KYRRE manual. Parts check first, or jump to step one?"
3) STOP and wait. Do not start step 1 until they answer.
- If checklist: one short BOM line (counts + look). Offer camera count if useful.
- If skip: get_assembly_step and begin with one short instruction.
- Never dump full step-1 instructions in the first "found it" reply.

## How to use tools
Only after they give a clear product/series name:
- Call ingest_product with that short name only (e.g. query="KYRRE"), enrich=true,
  force=false first. Never pass the whole sentence as query.
- If alreadyExists=true or you get a productId and there is NO active session,
  call create_session, then list_bom — do not loop forever on ingest, and do
  not call get_assembly_step yet.
- If ingest_product errors that the query is not a product name, ask the user
  again — do not retry with the same vague text.
- For next step: advance_session. For status: get_session / get_assembly_step.
- Prefer list_products only when they ask what you have loaded.

Ordering replacement parts (IKEA spare parts — simulated checkout):
- After missing[], offer the shortfall once; wait for yes.
- Then order_replacement_parts with [{partId, name, qty}] — real BOM names.
- Never ask for email/payment. After yes, place the mock order immediately.
- Speak one line: what + part # + qty + ETA (demo order).

In-person help (Taskrabbit — simulated booking):
- If they want Taskrabbit / a handyman / someone to come over: offer once, wait for yes.
- Then book_taskrabbit_help (task, notes, urgency). Never ask for email/payment.
- Speak one line: tasker + arrival window + total (demo booking).
- Do NOT say you cannot book Taskrabbit.

## Vision tools (critical — decide carefully)
Call inspect_part / inspect_orientation / inspect_inventory ONLY when the user is
clearly showing or asking you to look at something on camera right now, e.g.:
- "I'm showing you these two screws — which do I need?"
- "does this look right?" / "check my assembly"
- "can you count what's on the table?"
Do NOT call vision tools for ordinary chat, naming a product, asking for the next
step verbally, or general assembly questions with no show-and-tell. The host only
grabs a webcam frame when YOU call a vision tool — never invent frame data.
Pass the active session UUID; omit frame_base64 (the host attaches the live frame).

When identifying hardware (which screw / which part):
1) get_assembly_step for requiredHardware partIds
2) optionally get_part_image(product_id, part_id) so you know what each looks like
3) inspect_part — GroundingDINO finds parts via detect_prompt, crops each box,
   compares to the correct step part photo, returns the highest-similarity match.
   detect_prompt MUST use SINGULAR class names only
   (e.g. "hex socket screw . screw . bolt ."), never plurals like "screws"/"bolts"
   (plurals wrap multiple fasteners in one box). Match the prompt to what they hold.
Trust inspect_part's winner. Speak one look-based line — never scores.

When checking assembly (does this look right?):
- Call inspect_orientation. Trust aligned / stepCompleted / errorDescription.
- aligned=true → brief OK (no tightness talk). aligned=false → one fix line.
  Never say it looks good when aligned is false.

When counting kit contents on the table:
- Call inspect_inventory. Speak a short tally (seen/expected). If scene rejected,
  say the camera isn't on the parts — don't invent a kit.

If you still need a product name, speak and stop. Otherwise use tools, then give
a short final spoken answer with no tool calls.
""".strip()

EventCallback = Callable[[dict[str, Any]], None]
FrameWaiter = Callable[[str], Awaitable[str | None]]  # async (call_id) -> frame


def mcp_tools_to_cohere(mcp_tools: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in mcp_tools:
        data = t.model_dump(mode="json") if hasattr(t, "model_dump") else dict(t)
        name = data.get("name")
        if not name:
            continue
        schema = data.get("input_schema") or data.get("inputSchema") or {
            "type": "object",
            "properties": {},
        }
        # Cohere expects a plain object schema under parameters.
        if schema.get("type") != "object":
            schema = {"type": "object", "properties": {}, "additionalProperties": True}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": data.get("description") or name,
                    "parameters": schema,
                },
            }
        )
    return out


def _tool_result_payload(result: Any) -> Any:
    if hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    elif isinstance(result, dict):
        data = result
    else:
        return str(result)
    if data.get("is_error") or data.get("isError"):
        texts = []
        for block in data.get("content") or []:
            if isinstance(block, dict) and block.get("text"):
                texts.append(block["text"])
        return {"error": True, "message": "\n".join(texts) or "tool error"}
    if data.get("structured_content") is not None:
        return data["structured_content"]
    if data.get("structuredContent") is not None:
        return data["structuredContent"]
    texts = []
    for block in data.get("content") or []:
        if isinstance(block, dict) and block.get("text"):
            texts.append(block["text"])
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except json.JSONDecodeError:
            return texts[0]
    return texts or data


def _inject_vision_frame(
    name: str,
    arguments: dict[str, Any],
    *,
    client_frame_base64: str | None = None,
    catalog: FilesystemProductCatalog | None = None,
    sessions: InMemoryAssemblySession | None = None,
) -> dict[str, Any]:
    """
    Attach image bytes for vision tools.

    Prefer the live webcam frame from the client. Never substitute the step
    diagram as the "webcam" frame (that made inspect_part ignore what the user
    was holding up). Diagrams are loaded separately inside orientation checks.
    """
    args = dict(arguments)
    if name not in VISION_TOOLS:
        return args

    # Resolve product-name-as-session-id mistakes early.
    session_id = args.get("session_id")
    if session_id and sessions is not None:
        try:
            args["session_id"] = sessions.resolve_session_id(str(session_id))
        except SessionNotFoundError:
            pass

    if args.get("frame_base64"):
        args["_frame_source"] = "tool_argument"
        return args

    live = (client_frame_base64 or "").strip()
    if live:
        args["frame_base64"] = live
        args["_frame_source"] = "webcam"
        return args

    args["_frame_source"] = "missing"
    return args


def _assembly_context_block(
    *,
    session_id: str | None,
    product_id: str | None,
    sessions: InMemoryAssemblySession | None,
) -> str:
    """Host-injected facts so the model reuses the live session."""
    sid = (session_id or "").strip() or None
    pid = (product_id or "").strip() or None
    state = None
    if sessions is not None and sid:
        try:
            sid = sessions.resolve_session_id(sid)
            state = sessions.get_state(sid)
            pid = state.productId
        except SessionNotFoundError:
            sid = None
    if sessions is not None and sid is None and pid:
        found = sessions.find_latest_for_product(pid)
        if found is not None:
            sid, state = found

    if not sid:
        return (
            "Active assembly context: none. No session is open yet. "
            "Only create_session after the user names a product."
        )
    step = state.currentStep if state else "?"
    mode = state.activeMode if state else "?"
    return (
        f"Active assembly context: sessionId={sid}; productId={pid}; "
        f"currentStep={step}; activeMode={mode}. "
        f"Reuse sessionId={sid} for all session/inspect tools. "
        f"Do not create_session again for productId={pid}."
    )


async def run_mcp_agent(
    *,
    mcp: MCPServer,
    utterance: str,
    history: list[dict[str, str]] | None = None,
    catalog: FilesystemProductCatalog | None = None,
    sessions: InMemoryAssemblySession | None = None,
    session_id: str | None = None,
    product_id: str | None = None,
    frame_base64: str | None = None,
    request_frame: FrameWaiter | None = None,
    max_rounds: int = 8,
    on_event: EventCallback | None = None,
) -> str:
    """Run an agentic loop; emit events; return final spoken text."""
    import asyncio

    def emit(event: str, **payload: Any) -> None:
        if on_event:
            on_event({"event": event, **payload})

    mcp_tools = await mcp.list_tools()
    cohere_tools = mcp_tools_to_cohere(mcp_tools)
    emit(
        "tools_ready",
        tools=[t.get("function", {}).get("name") for t in cohere_tools],
    )

    context_block = _assembly_context_block(
        session_id=session_id, product_id=product_id, sessions=sessions
    )
    system = (
        f"{AGENT_SYSTEM}\n\n## {context_block}\n"
        "## Camera\n"
        "A live webcam may be available. Only call vision tools when the user is "
        "showing something; the host will then capture one frame for that tool."
    )

    messages: list[dict[str, Any]] = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": utterance})

    # Optional seed frame (rare); normally captured on-demand via request_frame.
    live_frame = (frame_base64 or "").strip() or None

    final_text = ""
    for round_i in range(max_rounds):
        emit("status", message=f"reasoning (round {round_i + 1})")
        try:
            body = chat_with_tools(
                messages=messages,
                tools=cohere_tools,
                system=system,
            )
        except CohereError as exc:
            emit("error", message=str(exc))
            raise

        assistant = extract_assistant_message(body)
        tool_calls = extract_tool_calls(assistant)
        text = extract_text(assistant)
        plan = assistant.get("tool_plan")
        if plan:
            emit("plan", text=str(plan))

        # Append assistant message for multi-turn tool use (Cohere expects the object).
        messages.append(assistant)

        if not tool_calls:
            final_text = text or "Okay."
            emit("speak", text=final_text)
            break

        for call in tool_calls:
            name = call["name"]
            args = call["arguments"]
            display_args = {
                k: v for k, v in args.items() if k != "frame_base64"
            }
            emit("tool_call", name=name, arguments=display_args, id=call["id"])

            client_frame = live_frame
            if name in VISION_TOOLS and not client_frame and request_frame is not None:
                emit("need_frame", tool=name, id=call["id"])
                try:
                    grabbed = await request_frame(str(call["id"]))
                except Exception:  # noqa: BLE001
                    grabbed = None
                if grabbed:
                    client_frame = grabbed.strip()
                    live_frame = client_frame  # reuse within this turn if more vision tools

            prepared = _inject_vision_frame(
                name,
                args,
                client_frame_base64=client_frame,
                catalog=catalog,
                sessions=sessions,
            )
            frame_source = prepared.pop("_frame_source", None)
            if name in VISION_TOOLS:
                emit(
                    "frame_used",
                    tool=name,
                    source=frame_source or "unknown",
                    id=call["id"],
                    hasFrame=bool(prepared.get("frame_base64")),
                )
            if name in VISION_TOOLS and not prepared.get("frame_base64"):
                err = {
                    "error": True,
                    "message": (
                        "No webcam frame available. Ask the user to enable Debug "
                        "mode camera and show the parts again."
                    ),
                }
                emit("tool_result", name=name, ok=False, result=err, id=call["id"])
                messages.append(tool_result_message(call["id"], err))
                continue

            try:
                result = await mcp.call_tool(name, prepared)
                payload = _tool_result_payload(result)
                if isinstance(payload, dict) and frame_source:
                    payload = {**payload, "frameSource": frame_source}
                # Fan out per-crop debug cards before the summary tool_result.
                if name == "inspect_part" and isinstance(payload, dict):
                    for i, step in enumerate(payload.get("debugSteps") or []):
                        if not isinstance(step, dict):
                            continue
                        emit(
                            "debug_step",
                            id=f"{call['id']}-dbg-{i}",
                            tool=name,
                            parentId=call["id"],
                            step=_compact_debug_step(step),
                        )
                        await asyncio.sleep(0)
                if name == "inspect_orientation" and isinstance(payload, dict):
                    for i, step in enumerate(_orientation_debug_steps(payload)):
                        emit(
                            "debug_step",
                            id=f"{call['id']}-ori-{i}",
                            tool=name,
                            parentId=call["id"],
                            step=step,
                        )
                        await asyncio.sleep(0)
                if name == "inspect_inventory" and isinstance(payload, dict):
                    for i, step in enumerate(payload.get("debugSteps") or []):
                        if not isinstance(step, dict):
                            continue
                        emit(
                            "debug_step",
                            id=f"{call['id']}-inv-{i}",
                            tool=name,
                            parentId=call["id"],
                            step=_compact_debug_step(step),
                        )
                        await asyncio.sleep(0)
                if name == "inspect_part" and isinstance(payload, dict):
                    ui_payload = _ui_inspect_part_payload(payload)
                elif name == "inspect_orientation" and isinstance(payload, dict):
                    ui_payload = _ui_orientation_payload(payload)
                elif name == "inspect_inventory" and isinstance(payload, dict):
                    ui_payload = _ui_inventory_payload(payload)
                else:
                    ui_payload = _shrink_for_ui(payload)
                emit(
                    "tool_result",
                    name=name,
                    ok=True,
                    result=ui_payload,
                    id=call["id"],
                )
                messages.append(
                    tool_result_message(call["id"], _shrink_for_llm(payload))
                )
            except Exception as exc:  # noqa: BLE001
                err = {"error": True, "message": str(exc)}
                emit("tool_result", name=name, ok=False, result=err, id=call["id"])
                messages.append(tool_result_message(call["id"], err))
            # Yield to event loop so need_frame SSE can flush before next tool.
            await asyncio.sleep(0)
    else:
        final_text = final_text or "Sorry — I hit a limit. Try again."
        emit("speak", text=final_text)

    emit("done")
    return final_text


_IMAGE_KEYS = frozenset(
    {
        "annotatedFrameBase64",
        "diagramPreviewBase64",
        "imageBase64",
        "refImageBase64",
        "frame_base64",
        "frameBase64",
        "diagram",
    }
)


def _compact_debug_step(step: dict[str, Any]) -> dict[str, Any]:
    """One debug chat card — keep images only if they fit in SSE."""
    out: dict[str, Any] = {
        "kind": step.get("kind"),
        "title": step.get("title"),
        "text": step.get("text"),
        "detectionIndex": step.get("detectionIndex"),
        "partId": step.get("partId"),
        "similarity": step.get("similarity"),
    }
    for key in ("imageBase64", "refImageBase64"):
        val = step.get(key)
        if isinstance(val, str) and val.startswith("data:") and len(val) <= 140_000:
            out[key] = val
    return out


def _orientation_debug_steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Prove the checker saw both webcam + diagram."""
    steps: list[dict[str, Any]] = []
    steps.append(
        _compact_debug_step(
            {
                "kind": "status",
                "title": "Orientation inputs",
                "text": (
                    f"Compared webcam to diagram step "
                    f"{payload.get('diagramStep')} "
                    f"({payload.get('diagramTitle') or 'untitled'}) "
                    f"— diagram {payload.get('diagramBytes') or '?'} bytes"
                ),
            }
        )
    )
    if payload.get("diagramPreviewBase64"):
        steps.append(
            _compact_debug_step(
                {
                    "kind": "compare",
                    "title": "Image B — step diagram actually used",
                    "text": payload.get("diagramObserved")
                    or "Model description of the manual diagram",
                    "refImageBase64": payload.get("diagramPreviewBase64"),
                }
            )
        )
    if payload.get("diagramObserved") or payload.get("webcamObserved"):
        steps.append(
            _compact_debug_step(
                {
                    "kind": "status",
                    "title": "What the model saw",
                    "text": (
                        f"Diagram: {payload.get('diagramObserved') or '—'}\n"
                        f"Webcam: {payload.get('webcamObserved') or '—'}"
                    ),
                }
            )
        )
    steps.append(
        _compact_debug_step(
            {
                "kind": "winner" if payload.get("aligned") else "status",
                "title": (
                    "Aligned"
                    if payload.get("aligned")
                    else "Not aligned"
                )
                + (
                    " · step complete"
                    if payload.get("stepCompleted")
                    else " · step incomplete"
                ),
                "text": payload.get("rawNotes") or payload.get("errorDescription"),
            }
        )
    )
    return steps


def _ui_orientation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact orientation result for SSE (previews already streamed as debug_step)."""
    ann = payload.get("annotatedFrameBase64")
    if isinstance(ann, str) and len(ann) > 140_000:
        ann = None
    return {
        "aligned": payload.get("aligned"),
        "errorDescription": payload.get("errorDescription"),
        "boundingBox": payload.get("boundingBox"),
        "confidence": payload.get("confidence"),
        "stepCompleted": payload.get("stepCompleted"),
        "rawNotes": payload.get("rawNotes"),
        "diagramStep": payload.get("diagramStep"),
        "diagramTitle": payload.get("diagramTitle"),
        "diagramBytes": payload.get("diagramBytes"),
        "diagramObserved": payload.get("diagramObserved"),
        "webcamObserved": payload.get("webcamObserved"),
        "annotatedFrameBase64": ann,
        "frameSource": payload.get("frameSource"),
    }


def _ui_inventory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact inventory result — per-crop trail already emitted as debug_step."""
    ann = payload.get("annotatedFrameBase64")
    if isinstance(ann, str) and len(ann) > 140_000:
        ann = None
    return {
        "counted": payload.get("counted") or [],
        "missing": payload.get("missing") or [],
        "extras": payload.get("extras") or [],
        "notes": payload.get("notes"),
        "detector": payload.get("detector"),
        "detectPrompt": payload.get("detectPrompt"),
        "annotatedFrameBase64": ann,
        "debugStepCount": len(payload.get("debugSteps") or []),
        "frameSource": payload.get("frameSource"),
    }


def _ui_inspect_part_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Guaranteed-small SSE payload for the debug transcript.

    Per-crop images are emitted separately as debug_step events.
    """
    ann = payload.get("annotatedFrameBase64")
    if isinstance(ann, str) and len(ann) > 140_000:
        ann = None
    comparisons = payload.get("comparisons") or []
    if isinstance(comparisons, list):
        comparisons = [
            {
                "detectionIndex": c.get("detectionIndex"),
                "partId": c.get("partId"),
                "partName": c.get("partName"),
                "similarity": c.get("similarity"),
            }
            for c in comparisons
            if isinstance(c, dict)
        ]
    detections = payload.get("detections") or []
    if isinstance(detections, list):
        detections = [
            {
                "index": d.get("index"),
                "label": d.get("label"),
                "score": d.get("score"),
                "box": d.get("box"),
            }
            for d in detections
            if isinstance(d, dict)
        ]
    pipeline = payload.get("debugPipeline") or []
    if not pipeline and (detections or comparisons):
        pipeline = [
            f"1. GroundingDINO isolated {len(detections)} screw(s)",
            f"2. Compared to correct part ({payload.get('targetPartId')})",
            f"3. Winner → {payload.get('partId')}",
        ]
    return {
        "partId": payload.get("partId"),
        "name": payload.get("name"),
        "matchesCurrentStep": payload.get("matchesCurrentStep"),
        "confidence": payload.get("confidence"),
        "notes": payload.get("notes"),
        "detector": payload.get("detector"),
        "detectPrompt": payload.get("detectPrompt"),
        "targetPartId": payload.get("targetPartId"),
        "detections": detections,
        "comparisons": comparisons,
        "winnerDetectionIndex": payload.get("winnerDetectionIndex"),
        "referencePartIds": payload.get("referencePartIds") or [],
        "debugPipeline": pipeline,
        "annotatedFrameBase64": ann,
        "frameSource": payload.get("frameSource"),
        "debugStepCount": len(payload.get("debugSteps") or []),
    }


def _shrink_for_ui(payload: Any) -> Any:
    """Keep annotated/part images for the debug transcript; trim bulky extras."""
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            if k in {"manifest"}:
                out[k] = "…"
            elif k in _IMAGE_KEYS and isinstance(v, str) and len(v) > 140_000:
                out[k] = f"[image omitted, {len(v)} chars]"
            elif k == "result" and isinstance(v, list) and len(v) > 8:
                out[k] = v[:8] + ["…"]
            else:
                out[k] = _shrink_for_ui(v)
        return out
    if isinstance(payload, list) and len(payload) > 12:
        return [_shrink_for_ui(x) for x in payload[:12]] + ["…"]
    return payload


# Keep scores in the debug UI; strip them from what the LLM hears so it
# cannot parrot "95% confidence" in spoken replies.
_LLM_DROP_KEYS = {
    "confidence",
    "similarity",
    "comparisons",
    "detections",
    "debugPipeline",
    "debugSteps",
    "debugStepCount",
    "annotatedFrameBase64",
    "diagramPreviewBase64",
}


def _shrink_for_llm(payload: Any) -> Any:
    """Drop images + numeric scores; keep plain fields the agent should speak."""
    if isinstance(payload, dict):
        out = {}
        for k, v in payload.items():
            if k in _LLM_DROP_KEYS or k in _IMAGE_KEYS or k in {"manifest"}:
                if isinstance(v, str) and v.startswith("data:"):
                    out[k] = "[image omitted — shown in debug UI]"
                continue
            elif k == "result" and isinstance(v, list) and len(v) > 8:
                out[k] = v[:8] + ["…"]
            else:
                out[k] = _shrink_for_llm(v)
        return out
    if isinstance(payload, list) and len(payload) > 12:
        return [_shrink_for_llm(x) for x in payload[:12]] + ["…"]
    return payload


async def iter_agent_events(
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """Async generator wrapper that streams events from the agent run."""
    import asyncio

    q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def push(evt: dict[str, Any]) -> None:
        q.put_nowait(evt)

    async def runner() -> None:
        try:
            await run_mcp_agent(on_event=push, **kwargs)
        except Exception as exc:  # noqa: BLE001
            await q.put({"event": "error", "message": str(exc)})
            await q.put({"event": "done"})
        finally:
            await q.put(None)

    task = asyncio.create_task(runner())
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            yield item
    finally:
        await task
