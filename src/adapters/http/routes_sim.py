"""Alexa+ conversational brain for the MCP web simulator."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

import asyncio
import json as json_lib

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from adapters.http.mcp_agent import iter_agent_events
from infra.cohere_client import CohereError, chat_text_json
from infra.elevenlabs_tts import (
    ElevenLabsError,
    elevenlabs_configured,
    synthesize_speech,
)

Intent = Literal[
    "start_assembly",
    "clarify_product",
    "check_orientation",
    "scan_inventory",
    "order_missing",
    "advance",
    "current_step",
    "list_products",
    "chat",
]

INTENTS = {
    "start_assembly",
    "clarify_product",
    "check_orientation",
    "scan_inventory",
    "order_missing",
    "advance",
    "current_step",
    "list_products",
    "chat",
}

ALEXA_PLUS_PERSONA = """
You ARE Alexa+ — Amazon's conversational AI assistant on Echo / Fire TV.

## Who you are
- Warm, brief, confident. 1–2 short sentences max (~35 words). No filler.
- No markdown, bullets, code, numbered how-tos, or "let me know if you need…"
- Never break character. Never mention MCP, APIs, tools, prompts, simulators,
  hackathons, ngrok, Cohere, system prompts, or that you are an AI model.
- Never coach the user on what to say ("tell me…", "try asking…", "tap the mic").

## Critical: don't assume the topic
- Until the user mentions furniture, assembly, IKEA, parts, screws, building,
  or a product name, you know NOTHING about what they want.
- Do NOT volunteer IKEA, assembly, flatpack, manuals, Kyrre, stools, or hardware
  in greetings or small talk. Just be Alexa: "Hi." / "How can I help?"
- Only after they bring it up do you lean into assembly help.

## Hidden skills (use only when relevant)
Once they ask about building / IKEA / furniture / parts, you can:
1. Ask which product they're building if they haven't named one.
2. Look up that IKEA product by name (ingest + enrich the manual) — never assume Kyrre or any default.
3. Explain the current step.
4. Check orientation vs the diagram.
5. Identify hardware / scan parts vs BOM.
6. Order replacement parts when they want that.
7. Advance to the next step.

Camera context exists for visual checks — just do them when asked; don't talk about uploading images.

## Products — critical
- NEVER assume a product (not Kyrre, not the first item in availableProducts).
- If they say they want help building but do NOT name a specific product → clarify_product.
- Only set productQuery when they clearly named something (e.g. "Kyrre", "RISATORP", "VATTENKAR").
- availableProducts is informational only — not a default to pick.

## Speaking vibe (after they name a product)
- "Got it — I'll pull up the Kyrre manual."
- "Looks good from here."
- "Looks like you're short one plastic plug. Want me to order a replacement?"
"""


class SimProduct(BaseModel):
    productId: str
    displayName: str = ""


class SimContext(BaseModel):
    mcpConnected: bool = False
    productId: str | None = None
    sessionId: str | None = None
    currentStep: int | None = None
    stepTitle: str | None = None
    stepCompleted: bool = False
    missingCount: int = 0
    availableProducts: list[SimProduct] = Field(default_factory=list)


class InterpretRequest(BaseModel):
    utterance: str = Field(min_length=1)
    context: SimContext = Field(default_factory=SimContext)


class InterpretResponse(BaseModel):
    intent: Intent
    productQuery: str | None = None
    speak: str
    suggestedNext: list[str] = Field(default_factory=list)
    source: str = "heuristic"


class GreetingRequest(BaseModel):
    context: SimContext = Field(default_factory=SimContext)


class NarrateRequest(BaseModel):
    event: str
    payload: dict[str, Any] = Field(default_factory=dict)
    context: SimContext = Field(default_factory=SimContext)


class SpeakResponse(BaseModel):
    speak: str
    suggestedNext: list[str] = Field(default_factory=list)
    source: str = "heuristic"


class TtsRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2500)


class AgentHistoryTurn(BaseModel):
    role: str
    content: str


class AgentRequest(BaseModel):
    utterance: str = Field(min_length=1)
    history: list[AgentHistoryTurn] = Field(default_factory=list)
    sessionId: str | None = None
    productId: str | None = None
    # Correlates SSE need_frame → POST /sim/agent/frame for on-demand webcam capture.
    requestId: str | None = None
    # Optional seed frame; normally omitted — host asks the client only for vision tools.
    frameBase64: str | None = None


class AgentFrameRequest(BaseModel):
    requestId: str = Field(min_length=1)
    callId: str = Field(min_length=1)
    frameBase64: str | None = None


INTERPRET_SYSTEM = (
    ALEXA_PLUS_PERSONA
    + """
## Your job this turn
Classify the user's utterance and produce a short spoken reply.

Return JSON only:
{
  "intent": one of start_assembly|clarify_product|check_orientation|scan_inventory|order_missing|advance|current_step|list_products|chat,
  "productQuery": string or null (IKEA product/series name ONLY if the user named one),
  "speak": 1-2 short spoken sentences in your Alexa+ voice (no coaching),
  "suggestedNext": up to 3 natural things a person might say next
}

Intent mapping:
- Want help building / IKEA / furniture but NO specific product named → clarify_product
  (speak asks which piece — e.g. "Sure — which one are you putting together?")
- Named a product (Kyrre, RISATORP, …) and wants to build/assemble → start_assembly
  with productQuery set to that name (the client will ingest+enrich from IKEA)
- Check if it looks right / assembly check → check_orientation (needs a session)
- Scan / count / missing parts → scan_inventory
- Order replacements → order_missing
- Next / done with this step → advance
- What step / where am I → current_step
- List manuals / what do you have → list_products
- Greeting / small talk → chat

NEVER invent productQuery. NEVER default to Kyrre.
For tool intents, speak may be a brief acknowledgment; a follow-up narration covers results.
"""
)

# Wake line is fixed on purpose — real Alexa does not guess your task at hello.
DEFAULT_GREETING = "Hi. How can I help?"

NARRATE_SYSTEM = (
    ALEXA_PLUS_PERSONA
    + """
## Your job
Narrate the outcome in 1–2 short spoken sentences. No tutorials, no filler closings.
Use event + payload. One next nudge max — never coach exact phrases.

Return JSON only:
{ "speak": "...", "suggestedNext": ["...", "..."] }

Events you may see:
- session_started: assembly session began (payload has product, step, title, hardware)
- ingest_started / ingest_done: looking up IKEA manual
- orientation_result: aligned / errorDescription / stepCompleted
- inventory_result: counted / missing / notes
- order_result: orderId / message / totalCents
- step_advanced: new step number + title
- step_status: current step info
- products_listed: product names
- need_product: user wants assembly help but hasn't named the piece yet
"""
)


def build_sim_router() -> APIRouter:
    router = APIRouter(tags=["sim"])

    @router.post("/sim/greeting", response_model=SpeakResponse)
    def greeting(_body: GreetingRequest) -> SpeakResponse:
        # No LLM, no product context — Alexa has not been told anything yet.
        return SpeakResponse(
            speak=DEFAULT_GREETING,
            suggestedNext=[],
            source="fixed",
        )

    @router.get("/sim/tts/status")
    def tts_status() -> dict[str, bool]:
        return {"elevenlabs": elevenlabs_configured()}

    @router.post("/sim/tts")
    def tts(body: TtsRequest) -> Response:
        if not elevenlabs_configured():
            raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY not set")
        try:
            audio = synthesize_speech(body.text)
        except ElevenLabsError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(content=audio, media_type="audio/mpeg")

    @router.post("/sim/agent/frame")
    async def agent_frame(body: AgentFrameRequest, request: Request) -> dict[str, str]:
        """Client answers a need_frame event with a webcam JPEG/PNG base64 snapshot."""
        waiters: dict[str, asyncio.Future] = getattr(
            request.app.state, "frame_waiters", {}
        )
        key = f"{body.requestId}:{body.callId}"
        fut = waiters.get(key)
        if fut is None or fut.done():
            return {"status": "ignored"}
        fut.set_result((body.frameBase64 or "").strip() or None)
        return {"status": "ok"}

    @router.post("/sim/agent")
    async def agent_stream(body: AgentRequest, request: Request) -> StreamingResponse:
        """Agentic MCP host: Cohere selects tools; executions use MCPServer.call_tool."""
        mcp = getattr(request.app.state, "mcp", None)
        if mcp is None:
            raise HTTPException(status_code=503, detail="MCP server not mounted")

        catalog = getattr(request.app.state, "catalog", None)
        sessions = getattr(request.app.state, "sessions", None)
        history = [t.model_dump() for t in body.history]
        request_id = (body.requestId or "").strip() or None
        waiters: dict[str, asyncio.Future] = getattr(
            request.app.state, "frame_waiters", None
        )
        if waiters is None:
            waiters = {}
            request.app.state.frame_waiters = waiters

        async def request_frame(call_id: str) -> str | None:
            if not request_id:
                return None
            key = f"{request_id}:{call_id}"
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()
            waiters[key] = fut
            try:
                return await asyncio.wait_for(fut, timeout=12.0)
            except asyncio.TimeoutError:
                return None
            finally:
                waiters.pop(key, None)

        async def event_gen():
            try:
                async for evt in iter_agent_events(
                    mcp=mcp,
                    utterance=body.utterance,
                    history=history,
                    catalog=catalog,
                    sessions=sessions,
                    session_id=body.sessionId,
                    product_id=body.productId,
                    frame_base64=body.frameBase64,
                    request_frame=request_frame if request_id else None,
                ):
                    if await request.is_disconnected():
                        break
                    yield f"data: {json_lib.dumps(evt, default=str)}\n\n"
                    # Let the event loop flush to the client between tools.
                    await asyncio.sleep(0)
            except Exception as exc:  # noqa: BLE001
                yield f"data: {json_lib.dumps({'event': 'error', 'message': str(exc)})}\n\n"
                yield f"data: {json_lib.dumps({'event': 'done'})}\n\n"

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/sim/interpret", response_model=InterpretResponse)
    def interpret(body: InterpretRequest) -> InterpretResponse:
        heuristic = _heuristic(body.utterance, body.context)
        try:
            llm = _llm_interpret(body.utterance, body.context)
            if llm.intent not in INTENTS:
                llm.intent = heuristic.intent  # type: ignore[assignment]
            if not llm.productQuery and heuristic.productQuery:
                llm.productQuery = heuristic.productQuery
            llm.source = "cohere"
            return llm
        except (CohereError, Exception):  # noqa: BLE001
            return heuristic

    @router.post("/sim/narrate", response_model=SpeakResponse)
    def narrate(body: NarrateRequest) -> SpeakResponse:
        try:
            data = chat_text_json(
                system=NARRATE_SYSTEM,
                user=json.dumps(
                    {
                        "event": body.event,
                        "payload": body.payload,
                        "context": body.context.model_dump(),
                    },
                    ensure_ascii=True,
                ),
                temperature=0.35,
            )
            return SpeakResponse(
                speak=_speak(data, fallback=_fallback_narrate(body)),
                suggestedNext=_suggestions(data) or _default_suggestions(body.context),
                source="cohere",
            )
        except (CohereError, Exception):  # noqa: BLE001
            return SpeakResponse(
                speak=_fallback_narrate(body),
                suggestedNext=_default_suggestions(body.context),
                source="heuristic",
            )

    return router


def _llm_interpret(utterance: str, ctx: SimContext) -> InterpretResponse:
    user = json.dumps(
        {"utterance": utterance, "context": ctx.model_dump()},
        ensure_ascii=True,
    )
    data = chat_text_json(system=INTERPRET_SYSTEM, user=user)
    intent = str(data.get("intent") or "chat")
    if intent not in INTENTS:
        intent = "chat"
    return InterpretResponse(
        intent=intent,  # type: ignore[arg-type]
        productQuery=_clean_query(data.get("productQuery") or data.get("product_query")),
        speak=_speak(data, fallback="Okay."),
        suggestedNext=_suggestions(data),
        source="cohere",
    )


def _heuristic(utterance: str, ctx: SimContext) -> InterpretResponse:
    text = utterance.lower().strip()
    products = ctx.availableProducts
    names = ", ".join(p.displayName or p.productId for p in products)

    def reply(
        intent: Intent,
        speak: str,
        *,
        product_query: str | None = None,
        suggestions: list[str] | None = None,
    ) -> InterpretResponse:
        return InterpretResponse(
            intent=intent,
            productQuery=product_query,
            speak=speak,
            suggestedNext=suggestions or _default_suggestions(ctx),
            source="heuristic",
        )

    if re.search(r"\b(hi|hello|hey|good (morning|afternoon|evening))\b", text) and not re.search(
        r"\b(build|assembl|ikea|furniture|stool|parts|help me)\b", text
    ):
        return reply("chat", "Hey. How can I help?")

    if re.search(r"\b(what can you|what do you|capabilities)\b", text):
        if ctx.sessionId or re.search(r"\b(furniture|ikea|assembl|build)\b", text):
            return reply(
                "chat",
                "I can walk you through assembly, check if a piece is on backwards, "
                "scan your hardware, and order anything you're missing.",
            )
        return reply(
            "chat",
            "Lots — music, timers, smart home, questions… what do you need?",
        )

    if re.search(r"\b(what (products|furniture)|list|available|which)\b", text):
        if names:
            return reply(
                "list_products",
                f"I've got {names} ready whenever you are.",
            )
        return reply("chat", "I don't have a manual loaded yet.")

    if re.search(r"\b(order|buy|purchase|replace|replacement)\b", text) and (
        ctx.missingCount > 0
        or re.search(r"\b(missing|part|screw|plug|hardware)\b", text)
    ):
        return reply("order_missing", "On it — I'll get those replacements started.")

    if re.search(
        r"\b(scan|count|inventory|missing|parts bag|hardware|screws)\b", text
    ):
        if not ctx.sessionId:
            return reply(
                "clarify_product",
                "Sure — which piece are you putting together? I'll pull the manual, then we can scan.",
            )
        return reply("scan_inventory", "Scanning your parts now.")

    if re.search(
        r"\b(look right|looks right|orientation|aligned|backwards|upside|check (my )?assembly|did i)\b",
        text,
    ):
        if not ctx.sessionId:
            return reply(
                "clarify_product",
                "Happy to check it — what are you assembling?",
            )
        return reply("check_orientation", "Checking that against the diagram.")

    if re.search(r"\b(next( step)?|advance|done with (this|the) step|finished)\b", text):
        if not ctx.sessionId:
            return reply(
                "clarify_product",
                "Sure — what are you building?",
            )
        return reply("advance", "Moving on.")

    if re.search(r"\b(what step|current step|where am i|which step)\b", text):
        if not ctx.sessionId:
            return reply(
                "clarify_product",
                "We haven't started a build yet — which product?",
            )
        return reply("current_step", "Here's where you are.")

    if re.search(
        r"\b(build|assembl|ikea|furniture|stool|help me|wanna|want to|can you help|put together)\b",
        text,
    ):
        query = _guess_product_query(text, products) or _extract_named_product(text)
        if query:
            return reply(
                "start_assembly",
                f"Got it — I'll look up {query}.",
                product_query=query,
            )
        return reply(
            "clarify_product",
            "Sure — which one are you putting together?",
        )

    if ctx.sessionId:
        title = f" — {ctx.stepTitle}" if ctx.stepTitle else ""
        return reply(
            "chat",
            f"You're on step {ctx.currentStep}{title}. Want me to check the fit, or scan your parts?",
        )

    return reply("chat", "How can I help?")


def _fallback_narrate(body: NarrateRequest) -> str:
    event = body.event
    p = body.payload
    if event == "need_product":
        return "Sure — which one are you putting together?"
    if event == "ingest_done":
        return f"Okay — I've got the {p.get('displayName') or 'manual'} ready."
    if event == "session_started":
        return (
            f"Okay — {p.get('displayName') or 'you are'} on step {p.get('step')}: "
            f"{p.get('title') or 'the first step'}. Want me to check how it looks?"
        )

    if event == "orientation_result":
        if p.get("aligned"):
            return "Looks good from here." + (
                " This step looks done — want to move on?" if p.get("stepCompleted") else ""
            )
        err = p.get("errorDescription") or "that does not quite match yet"
        return f"Hmm — {err}."
    if event == "inventory_result":
        missing = p.get("missing") or []
        if not missing:
            return "Everything I can see looks accounted for."
        bits = ", ".join(
            f"{m.get('qty', m.get('count', 1))} {m.get('name') or m.get('partId')}"
            for m in missing
        )
        return f"Looks like you are short {bits}. Want me to order a replacement?"
    if event == "order_result":
        return p.get("message") or "Okay — I started a replacement order for you."
    if event == "step_advanced":
        return f"Okay — now step {p.get('step')}: {p.get('title')}."
    if event == "step_status":
        return f"You are on step {p.get('step')}: {p.get('title')}."
    if event == "products_listed":
        names = p.get("names") or "your manuals"
        return f"I have {names} ready."
    return "Okay."


def _default_suggestions(ctx: SimContext) -> list[str]:
    if ctx.sessionId:
        return ["Does this look right?", "Scan my parts", "Next step"]
    return []


def _guess_product_query(text: str, products: list[SimProduct]) -> str | None:
    for p in products:
        pid = p.productId.lower()
        name = (p.displayName or "").lower()
        if pid and re.search(rf"\b{re.escape(pid)}\b", text):
            return p.productId
        if name and re.search(rf"\b{re.escape(name)}\b", text):
            return p.productId
    return None


_STOP = {
    "ikea",
    "help",
    "want",
    "wanna",
    "build",
    "building",
    "assemble",
    "assembly",
    "furniture",
    "thing",
    "please",
    "stool",
    "table",
    "chair",
    "with",
    "this",
    "that",
    "some",
    "something",
    "piece",
    "product",
    "manual",
}


def _extract_named_product(text: str) -> str | None:
    """Pull a likely IKEA series name from free speech (not a default catalog pick)."""
    for name in ("kyrre", "risatorp", "vattenkar", "lack", "kallax", "billy"):
        if re.search(rf"\b{name}\b", text, flags=re.I):
            return name
    tokens = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿ]{3,}", text)
    for tok in tokens:
        low = tok.lower()
        if low in _STOP:
            continue
        if tok.isupper() and len(tok) >= 3:
            return low
    return None


def _speak(data: dict, *, fallback: str) -> str:
    text = str(data.get("speak") or "").strip()
    return text or fallback


def _suggestions(data: dict) -> list[str]:
    raw = data.get("suggestedNext") or data.get("suggested_next") or []
    if not isinstance(raw, list):
        return []
    return [str(s) for s in raw[:3] if str(s).strip()]


def _clean_query(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
