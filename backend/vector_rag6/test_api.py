"""
test_api.py v4.2

FastAPI backend: routes questions through classify() → retrieval/memory →
teaching_controller → generate_answer, streaming the response via SSE.

Key fix: session advancement now trusts route.intent == "follow_up" rather
than requiring an exact action=="use_memory" match, which previously caused
the session to get stuck re-explaining the same point ("oui"/"ok" wasn't
always advancing).
"""

from __future__ import annotations

import asyncio
import contextvars
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel, Field

load_dotenv()

import pipeline
from stream_handler import handle_stream          # ← replaces ws_handler
from router import classify
from teaching_controller import (
    LessonSession, parse_points, advance, get_prompt_injection
)
from llm import detect_language, generate_answer

# ── Thread pool ──────────────────────────────────────────────────────────────
_executor = ThreadPoolExecutor(max_workers=4)

# ── Session stores ───────────────────────────────────────────────────────────
_SESSION_STORE: dict[str, LessonSession] = {}
_LESSON_STORE:  dict[str, dict]          = {}

# ── App ──────────────────────────────────────────────────────────────────────
app = FastAPI(title="Prof IA — RAG Test API", version="4.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup warmup ───────────────────────────────────────────────────────────
@app.on_event("startup")
async def warmup():
    loop = asyncio.get_event_loop()
    print("[startup] Warming up retriever and reranker in background …")
    await asyncio.gather(
        loop.run_in_executor(_executor, pipeline.get_retriever),
        loop.run_in_executor(_executor, pipeline.get_reranker),
    )
    print("[startup] ✅ Models ready — server is accepting requests.")


# ── Models ───────────────────────────────────────────────────────────────────

class HistoryMessage(BaseModel):
    role   : str
    content: str

class AskRequest(BaseModel):
    question  : str
    session_id: str
    history   : Optional[List[HistoryMessage]] = Field(default_factory=list)

class AskResponse(BaseModel):
    answer    : str
    sources   : list[Any] = Field(default_factory=list)
    references: list[Any] = Field(default_factory=list)

class GenerateTitleRequest(BaseModel):
    message: str

class GenerateTitleResponse(BaseModel):
    title: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def _chunks_to_sources(chunks: list[dict]) -> list[dict]:
    return [
        {
            "chunk_id"    : c.get("chunk_id"),
            "text"        : c.get("text", ""),
            "lesson"      : c.get("lesson"),
            "level"       : c.get("level"),
            "subject"     : c.get("subject"),
            "has_formula" : c.get("has_formula"),
            "rerank_score": c.get("rerank_score"),
        }
        for c in (chunks or [])
    ]

def _build_lesson_from_session(session: LessonSession) -> dict:
    return {
        "lesson_title" : session.lesson_title or "",
        "lesson_number": session.lesson_id   or "",
        "level"        : "",
        "subject"      : session.topic       or "",
        "content"      : "\n".join(session.lesson_points),
    }


def _full_lesson_for_session(session_id: str, session: LessonSession) -> dict:
    """
    Prefer the ORIGINAL full lesson dict captured at retrieval time
    (kept in _LESSON_STORE) over the stripped-down bullet-point
    reconstruction — this avoids losing context on follow_up turns
    (e.g. level, subject, any prose around the bullet points).
    Falls back to the stripped rebuild only if the original was never stored.
    """
    original = _LESSON_STORE.get(session_id)
    if original:
        return original
    return _build_lesson_from_session(session)


# ── Single parent trace per turn ─────────────────────────────────────────────

@traceable(name="chat_turn", run_type="chain")
def _handle_turn(
    question   : str,
    history    : list[dict],
    session_id : str,
    token_queue: "asyncio.Queue[str | None]",
    loop       : asyncio.AbstractEventLoop,
) -> None:
    """
    Full turn: router → retrieve → TC → generate.
    @traceable here is the parent. Because we capture the run_tree
    and pass it into the generate_answer thread, ALL children nest
    under this one row in LangSmith:

      chat_turn
        └── router_classify
        └── retrieve_and_rerank   (only when needed)
        └── generate_answer
    """
    try:
        # ── 1. Route ─────────────────────────────────────────────────────────
        route = classify(
            question=question,
            history=history,
            api_key=os.environ.get("GEMINI_API_KEY", ""),
        )
        print(f"[router] intent={route.intent} action={route.action} "
              f"mode={route.mode} lesson_number={route.lesson_number}")

        lang           = detect_language(history[0]["content"] if history else question)
        stored_session = _SESSION_STORE.get(session_id)
        lesson         = _LESSON_STORE.get(session_id, {})
        session        = stored_session

        # Tracks whether advance() already ran this turn, so step 3 never
        # double-advances OR silently skips advancing. This is the fix for
        # the "re-explains the same point forever" bug.
        advanced_this_turn = False

        # ── 2. Retrieve or use memory ─────────────────────────────────────────
        if route.action == "retrieve" or (
            route.action == "retrieve_if_no_lesson" and not stored_session
        ):
            filters = {"lesson_number": route.lesson_number} if route.lesson_number else None
            ranked, lesson = pipeline._retrieve_and_rerank(
                question       = question,
                collection     = pipeline.QDRANT_COLLECTION,
                top_k_retrieve = 10,
                filters        = filters,
            )
            if not ranked:
                loop.call_soon_threadsafe(token_queue.put_nowait, "__NO_LESSON__")
                return

            session = LessonSession(
                lesson_id     = lesson.get("lesson_number"),
                lesson_title  = lesson.get("lesson_title"),
                lesson_points = parse_points(lesson.get("content", "")),
                current_point = 0,
                topic         = lesson.get("subject"),
                mode          = route.mode,
            )
            _LESSON_STORE[session_id] = lesson

        elif route.intent == "follow_up" and stored_session:
            # FIX: trust intent over the exact action string the router
            # returned — "oui"/"ok" should always advance regardless of
            # whether action came back as exactly "use_memory".
            session = advance(stored_session)
            lesson  = _full_lesson_for_session(session_id, session)
            advanced_this_turn = True

        elif route.action == "retrieve_if_no_lesson" and stored_session:
            session = stored_session
            lesson  = _full_lesson_for_session(session_id, session)

        elif route.action == "direct_llm":
            lesson = _full_lesson_for_session(session_id, stored_session) if stored_session else {
                "lesson_title": "", "lesson_number": "",
                "level": "", "subject": "", "content": "",
            }

        # ── 3. Teaching controller ────────────────────────────────────────────
        tc_injection = ""
        if session and route.mode in ("explanation", "continuation", "exercise", "summary"):
            if route.mode == "continuation" and not advanced_this_turn:
                session = advance(session)
                advanced_this_turn = True
            tc_injection = get_prompt_injection(session, route.mode, lang)
            _SESSION_STORE[session_id] = session

        print(f"[tc] current_point={getattr(session, 'current_point', '-')} "
              f"confirmed={getattr(session, 'confirmed_points', '-')} "
              f"advanced_this_turn={advanced_this_turn} tc_len={len(tc_injection)}")

        # ── 4. Generate — run inside current LangSmith context ───────────────
        current_run = get_current_run_tree()

        for token in generate_answer(
            question     = question,
            lesson       = lesson,
            stream       = True,
            history      = history,
            tc_injection = tc_injection,
            langsmith_extra={"run_tree": current_run},
        ):
            loop.call_soon_threadsafe(token_queue.put_nowait, token)

    except Exception as exc:
        import traceback; traceback.print_exc()
        loop.call_soon_threadsafe(token_queue.put_nowait, f"\n[ERROR] {exc}")
    finally:
        loop.call_soon_threadsafe(token_queue.put_nowait, None)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

@app.get("/debug/tts")
async def debug_tts():
    from tts import fetch_audio_async
    try:
        b64 = await fetch_audio_async("مرحباً، هذا اختبار صوتي.")
        return {"ok": bool(b64), "bytes": len(b64) if b64 else 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.post("/ask", response_model=AskResponse)
async def ask_route(body: AskRequest) -> AskResponse:
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(
            _executor,
            lambda: pipeline.query_rag(question=body.question),
        )
        answer  = str(result.get("answer", ""))
        sources = _chunks_to_sources(result.get("chunks", []))
        return AskResponse(answer=answer, sources=sources)
    except Exception as exc:
        import traceback; traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc

@app.post("/ask/stream")
async def ask_stream(body: AskRequest):
    """
    Streams SSE events for a full chat turn.

    Event types (one JSON object per SSE data line):
      {"type": "widget_chunk", "text": "..."}   — incremental widget HTML
      {"type": "done", "widget": "...", "teacher": "..."}  — full split output
      {"type": "stream_end"}                     — generation complete
      {"type": "error", "message": "..."}        — something went wrong
    """
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    history    = [{"role": m.role, "content": m.content} for m in (body.history or [])]
    session_id = body.session_id

    return StreamingResponse(
        _traced_stream(body.question, history, session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )

@app.post("/api/generate-title", response_model=GenerateTitleResponse)
async def generate_title_route(body: GenerateTitleRequest) -> GenerateTitleResponse:
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    clean = body.message.strip().replace("\n", " ")
    title = clean[:42] + "…" if len(clean) > 42 else clean
    return GenerateTitleResponse(title=title)


# ── HTTP SSE streaming ────────────────────────────────────────────────────────

async def _traced_stream(
    question  : str,
    history   : list[dict],
    session_id: str,
) -> AsyncGenerator[str, None]:
    """
    Runs _handle_turn() in a thread (preserving LangSmith context),
    then pipes the token queue through stream_handler.handle_stream()
    which splits [WIDGET]/[TEACHER] and yields SSE lines.
    """
    loop        = asyncio.get_event_loop()
    ctx         = contextvars.copy_context()
    token_queue: asyncio.Queue[str | None] = asyncio.Queue()

    asyncio.ensure_future(
        loop.run_in_executor(
            _executor,
            lambda: ctx.run(
                _handle_turn,
                question,
                history,
                session_id,
                token_queue,
                loop,
            ),
        )
    )

    # Peek at first token to detect the __NO_LESSON__ sentinel
    first = await token_queue.get()

    if first == "__NO_LESSON__":
        import json
        yield f"data: {json.dumps({'type': 'done', 'widget': '', 'teacher': 'لم يتم العثور على درس مناسب لسؤالك.'}, ensure_ascii=False)}\n\n"
        yield "data: {\"type\": \"stream_end\"}\n\n"
        return

    if first is None:
        yield "data: {\"type\": \"stream_end\"}\n\n"
        return

    # Rebuild async generator from queue + first token
    async def _token_gen(first_token=first):
        yield first_token
        while True:
            tok = await token_queue.get()
            if tok is None:
                return
            yield tok

    # Pipe through stream_handler — yields SSE lines
    async for sse_line in handle_stream(
        question     = question,
        lesson       = _LESSON_STORE.get(session_id, {}),
        history      = history,
        tc_injection = "",          # already injected inside _handle_turn
        token_stream = _token_gen(),
    ):
        yield sse_line


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("test_api:app", host="0.0.0.0", port=8001, reload=False)