"""
api.py — FastAPI backend of Prof IA, a RAG tutor for 7th-grade math (in Arabic).

Endpoints
    GET  /health              liveness check
    POST /ask                 one-shot RAG answer, no session
    POST /ask/stream          full tutoring turn, streamed as Server-Sent Events
    POST /api/generate-title  short chat title from the first message
    GET  /debug/tts           TTS smoke test (only if ENABLE_DEBUG_ENDPOINTS=1)

One /ask/stream turn:
    router.classify -> retrieve + rerank (or reuse the session) ->
    teaching_controller -> llm.generate_answer ->
    stream_handler (splits [WIDGET] / [TEACHER] text, adds TTS audio)

Sessions and lessons live in memory: they are lost on restart and are not shared
between worker processes.

Run (from backend/rag):   uvicorn api:app --port 8001
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any, AsyncGenerator, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langsmith import traceable
from langsmith.run_helpers import get_current_run_tree
from pydantic import BaseModel, Field

load_dotenv()  # must run before the project modules below read os.environ

import pipeline  # noqa: E402
from stream_handler import handle_stream, extract_used_representations  # noqa: E402
from router import classify, configure_curriculum, out_of_program_reply  # noqa: E402
from teaching_controller import (  # noqa: E402
    LessonSession, parse_points, advance, get_prompt_injection, _significant_words,
    get_extra_mode_addendum,
)
from llm import detect_language, generate_answer, warm_model_chain  # noqa: E402

API_VERSION = "1.0.0"

# Sentinels sent through the token queue before any real token.
_NO_LESSON = "__NO_LESSON__"
_OUT_OF_PROGRAM_PREFIX = "__OUT_OF_PROGRAM__:"
_NO_LESSON_REPLY = "لم يتم العثور على درس مناسب لسؤالك."

_executor = ThreadPoolExecutor(max_workers=4)

# In-memory stores, keyed by session_id.
_SESSION_STORE: dict[str, LessonSession] = {}
_LESSON_STORE: dict[str, dict] = {}

app = FastAPI(title="Prof IA API", version=API_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # demo setting: restrict to the frontend URL in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Startup warmup ───────────────────────────────────────────────────────────

@app.on_event("startup")
async def warmup():
    """
    Load the retriever, the reranker and the Gemini clients before the first
    request. Building Gemini clients costs ~2-5 s per model, which otherwise
    lands on the first user.
    """
    loop = asyncio.get_running_loop()
    print("[startup] Warming up retriever, reranker, and LLM model chain in background …")

    # The router needs the real lesson list to tell when a question is NOT in the
    # curriculum. Override the path with the CURRICULUM_JSON env var.
    try:
        configure_curriculum(os.environ.get("CURRICULUM_JSON") or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "data", "mathgeo.json"))
    except Exception as exc:
        print(f"[startup] ⚠️ curriculum NOT loaded, out-of-program detection disabled: {exc}")
    await asyncio.gather(
        loop.run_in_executor(_executor, pipeline.get_retriever),
        loop.run_in_executor(_executor, pipeline.get_reranker),
        loop.run_in_executor(_executor, warm_model_chain),
    )
    print("[startup] ✅ Models ready — server is accepting requests.")


# ── Request / response models ────────────────────────────────────────────────

class HistoryMessage(BaseModel):
    role   : str
    content: str
    representation: Optional[str] = None

class AskRequest(BaseModel):
    question  : str
    session_id: str
    history   : Optional[list[HistoryMessage]] = Field(default_factory=list)

class AskResponse(BaseModel):
    answer    : str
    sources   : list[Any] = Field(default_factory=list)
    references: list[Any] = Field(default_factory=list)

class GenerateTitleRequest(BaseModel):
    message: str

class GenerateTitleResponse(BaseModel):
    title: str


class _TurnMeta:
    """
    Values computed in the worker thread (_handle_turn) that the async stream
    (_traced_stream) needs afterwards. Everything is written before the first
    token is queued and read after it arrives, so no lock is needed.
    """
    __slots__ = ("mode", "active_point_text", "active_point_idx")

    def __init__(self) -> None:
        self.mode = "explanation"
        self.active_point_text = ""
        self.active_point_idx  = None


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
    """
    Rebuild a lesson dict from the session. Fallback only: it loses level and
    keywords, which the session does not store.
    """
    return {
        "lesson_title" : session.lesson_title or "",
        "lesson_number": session.lesson_id   or "",
        "level"        : "",
        "subject"      : session.topic       or "",
        "content"      : "\n".join(session.lesson_points),
        "keywords"     : [],
    }


def _question_matches_stored_session(question: str, session: "LessonSession | None") -> bool:
    """
    Fallback test for "is this question about the lesson already in session?",
    used only when the router gives no verdict.

    True when there is nothing to compare (no session, no lesson content, or no
    significant words, e.g. a generic "yes, continue"). Otherwise True only if
    EVERY significant word of the question already appears in the stored lesson.
    "Any overlap" is not enough: a question can share one word with the stored
    lesson and still name a new topic.
    """
    if not session or not session.lesson_points:
        return True
    q_words = _significant_words(question or "")
    if not q_words:
        return True
    session_words = _significant_words(session.lesson_title or "")
    for point in session.lesson_points:
        session_words |= _significant_words(point)
    return q_words.issubset(session_words)


def _full_lesson_for_session(session_id: str, session: LessonSession) -> dict:
    """The lesson dict stored at retrieval time, or a rebuild from the session."""
    original = _LESSON_STORE.get(session_id)
    if original:
        return original
    return _build_lesson_from_session(session)


def _merge_lessons(lessons: list[dict], top: dict) -> dict:
    """
    Merge several retrieved lessons (a multi-topic question) into one dict so
    the session and the prompt both contain every topic's text: titles joined
    with " | ", contents joined by newlines, keyword rules de-duplicated.
    """
    return {
        "lesson_title" : " | ".join(
            l.get("lesson_title", "") for l in lessons if l.get("lesson_title")
        ),
        "lesson_number": top.get("lesson_number"),
        "level"        : top.get("level", ""),
        "subject"      : top.get("subject", ""),
        "content"      : "\n".join(l.get("content", "") for l in lessons),
        "keywords"     : list(dict.fromkeys(
            kw for l in lessons for kw in (l.get("keywords") or [])
        )),
    }


def _refuse_out_of_program(route, question: str) -> bool:
    """
    The router only SUSPECTS the question is outside the curriculum; a false
    refusal on a real lesson question is worse than answering a borderline one.
    So the suspicion is checked against retrieval: if the best rerank probability
    is >= OOP_MIN_SCORE (env var, default 0 = check disabled), the router is
    overridden. Returns True when the question should be refused.
    """
    refuse = True
    try:
        threshold = float(os.environ.get("OOP_MIN_SCORE", "0") or 0)
        ranked, _, _ = pipeline._retrieve_and_rerank(
            question=question, collection=pipeline.QDRANT_COLLECTION,
            top_k_retrieve=10, filters=None,
        )
        top = max((pipeline._chunk_score(c) for c in ranked), default=0.0)
        print(f"[oop-check] router=False topic={route.topic!r} "
              f"top_rerank_prob={top:.4f} OOP_MIN_SCORE={threshold}")
        if threshold > 0 and top >= threshold:
            refuse = False
    except Exception as exc:
        print(f"[oop-check] retrieval check failed ({exc}); trusting router")
    return refuse


# ── One tutoring turn ────────────────────────────────────────────────────────

@traceable(name="chat_turn", run_type="chain")
def _handle_turn(
    question   : str,
    history    : list[dict],
    session_id : str,
    token_queue: "asyncio.Queue[str | None]",
    loop       : asyncio.AbstractEventLoop,
    turn_meta  : "_TurnMeta",
) -> None:
    """
    Full turn: router -> retrieve or reuse session -> teaching controller -> generate.
    Runs in a worker thread and pushes tokens into `token_queue` (None = end).

    @traceable makes this the parent run in LangSmith:
      chat_turn
        └── router_classify
        └── retrieve_and_rerank   (only when needed)
        └── generate_answer
    """
    try:
        # ── 1. Route ─────────────────────────────────────────────────────────
        # The stored session is fetched first so the router can see which lesson
        # is active and judge whether a topic-less message ("I want an
        # exercise") continues it.
        stored_session = _SESSION_STORE.get(session_id)

        route = classify(
            question=question,
            history=history,
            api_key=os.environ.get("GEMINI_API_KEY", ""),
            current_lesson_title=stored_session.lesson_title if stored_session else None,
        )
        print(f"[router] intent={route.intent} action={route.action} "
              f"mode={route.mode} extra_modes={getattr(route, 'extra_modes', [])} "
              f"lesson_number={route.lesson_number} "
              f"difficulty={getattr(route, 'difficulty', None)} "
              f"continues_current_lesson={route.continues_current_lesson}")

        lang = detect_language(history[0]["content"] if history else question)

        # Out-of-curriculum guard (router suspicion, confirmed by retrieval).
        if getattr(route, "in_curriculum", None) is False:
            if _refuse_out_of_program(route, question):
                print("[oop-check] -> OUT OF PROGRAM, fixed reply")
                loop.call_soon_threadsafe(
                    token_queue.put_nowait,
                    _OUT_OF_PROGRAM_PREFIX + out_of_program_reply(route.topic, lang),
                )
                return
            print("[oop-check] -> retrieval has strong support, overriding router; normal flow")
            route.intent, route.action, route.mode = "lesson", "retrieve", "explanation"
            route.in_curriculum = None

        lesson  = _LESSON_STORE.get(session_id, {})
        session = stored_session

        # True once advance() has run this turn, so step 3 never advances twice
        # or skips advancing (the "re-explains the same point forever" bug).
        advanced_this_turn = False

        # Does a "retrieve" action really refer to the lesson already in
        # session? If so it is probably a misclassified follow-up, and we must
        # not rebuild the session and lose current_point / confirmed_points.
        # The router's own verdict (continues_current_lesson) wins; the lesson
        # number and word-overlap checks are only fallbacks when it gave none.
        same_lesson_as_stored = bool(
            stored_session and (
                route.continues_current_lesson is True
                or (
                    route.continues_current_lesson is None and (
                        (route.lesson_number and route.lesson_number == stored_session.lesson_id)
                        or (not route.lesson_number and _question_matches_stored_session(question, stored_session))
                    )
                )
            )
        )

        # ── 2. Retrieve or use memory ─────────────────────────────────────────
        if route.mode == "exercise_answer" and stored_session:
            # Grading turn: reuse the session, no retrieval, no reset.
            session = stored_session
            lesson  = _full_lesson_for_session(session_id, session)

        elif route.action in ("retrieve", "retrieve_if_no_lesson") and stored_session and same_lesson_as_stored:
            # Retrieval-worthy, but about the lesson already in progress: reuse it.
            session = stored_session
            lesson  = _full_lesson_for_session(session_id, session)

            # A fresh lesson request that NAMES a topic means "teach me this
            # lesson", so restart at point 1. Topic-less turns ("yes",
            # "continue") keep the saved progress.
            if (route.intent == "lesson" and route.mode == "explanation"
                    and (route.topic or route.lesson_number)):
                print(f"[session] fresh lesson request on stored session "
                      f"(was current_point={session.current_point} "
                      f"confirmed={session.confirmed_points}) -> restarting at point 1")
                session.current_point    = 0
                session.confirmed_points = []

        elif route.action in ("retrieve", "retrieve_if_no_lesson"):
            # Exercise requests always come back as "retrieve_if_no_lesson", so
            # both actions share this branch: retrieval runs whenever the topic
            # is not the stored one (or there is no session yet).
            filters = {"lesson_number": route.lesson_number} if route.lesson_number else None
            ranked, lesson, lessons = pipeline._retrieve_and_rerank(
                question       = question,
                collection     = pipeline.QDRANT_COLLECTION,
                top_k_retrieve = 10,
                filters        = filters,
            )
            if not ranked:
                loop.call_soon_threadsafe(token_queue.put_nowait, _NO_LESSON)
                return

            # Several relevant lessons (e.g. a two-topic exercise request):
            # merge them so the session AND the prompt contain every topic.
            stored_lesson = _merge_lessons(lessons, lesson) if len(lessons) > 1 else lesson

            session = LessonSession(
                lesson_id     = lesson.get("lesson_number"),
                lesson_title  = lesson.get("lesson_title"),
                lesson_points = parse_points(stored_lesson.get("content", "")),
                current_point = 0,
                topic         = lesson.get("subject"),
                mode          = route.mode,
            )
            _LESSON_STORE[session_id] = stored_lesson
            # The prompt must show the same merged lesson as the session.
            lesson = stored_lesson

        elif route.intent == "follow_up" and route.mode == "explanation" and stored_session:
            # The student did NOT understand: re-explain the same point, do not
            # advance. Must come before the general follow_up branch below.
            session = stored_session
            lesson  = _full_lesson_for_session(session_id, session)

        elif route.intent == "follow_up" and stored_session:
            # "yes" / "ok": advance, whatever exact action string the router used.
            session = advance(stored_session)
            lesson  = _full_lesson_for_session(session_id, session)
            advanced_this_turn = True

        elif route.action == "direct_llm":
            lesson = _full_lesson_for_session(session_id, stored_session) if stored_session else {
                "lesson_title": "", "lesson_number": "",
                "level": "", "subject": "", "content": "",
            }

        # ── 3. Teaching controller ────────────────────────────────────────────
        tc_injection      = ""
        active_point_text = ""
        active_point_idx  = None   # None = no single point in focus
        if session and route.mode in (
            "explanation", "continuation", "exercise", "exercise_answer", "summary"
        ):
            if route.mode == "continuation" and not advanced_this_turn:
                session = advance(session)
                advanced_this_turn = True

            tc_injection, active_point_text, active_point_idx = get_prompt_injection(
                session, route.mode, lang,
                question=question,
                difficulty=getattr(route, "difficulty", None),
            )

            # Combined requests (e.g. explanation + summary): each extra mode adds
            # a short widget-free instruction to the same injection string.
            for extra in getattr(route, "extra_modes", None) or []:
                tc_injection += get_extra_mode_addendum(session, extra, lang)

            _SESSION_STORE[session_id] = session

        # Hand the final mode and active point to the async stream.
        turn_meta.mode              = route.mode
        turn_meta.active_point_text = active_point_text
        turn_meta.active_point_idx  = active_point_idx

        print(f"[tc] current_point={getattr(session, 'current_point', '-')} "
              f"confirmed={getattr(session, 'confirmed_points', '-')} "
              f"advanced_this_turn={advanced_this_turn} mode={route.mode} "
              f"tc_len={len(tc_injection)}")

        # ── 4. Generate — inside the current LangSmith run ───────────────────
        current_run = get_current_run_tree()

        for token in generate_answer(
            question             = question,
            lesson               = lesson,
            stream               = True,
            history              = history,
            tc_injection         = tc_injection,
            active_point_text    = active_point_text,
            active_point_idx     = active_point_idx,
            mode                 = route.mode,
            extra_modes          = getattr(route, "extra_modes", None) or [],
            used_representations = extract_used_representations(history),
            langsmith_extra      = {"run_tree": current_run},
        ):
            loop.call_soon_threadsafe(token_queue.put_nowait, token)

    except Exception as exc:
        traceback.print_exc()
        loop.call_soon_threadsafe(token_queue.put_nowait, f"\n[ERROR] {exc}")
    finally:
        loop.call_soon_threadsafe(token_queue.put_nowait, None)


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if os.environ.get("ENABLE_DEBUG_ENDPOINTS") == "1":
    @app.get("/debug/tts")
    async def debug_tts():
        """Smoke test for the TTS provider (spends API credits, so off by default)."""
        from tts import fetch_audio_async
        try:
            b64 = await fetch_audio_async("مرحباً، هذا اختبار صوتي.")
            return {"ok": bool(b64), "bytes": len(b64) if b64 else 0}
        except Exception as e:
            return {"ok": False, "error": str(e)}


@app.websocket("/ws/avatar")
async def avatar_ws_disabled(websocket: WebSocket):
    """
    Avatar TTS over WebSocket is disabled for now: accept, then close at once, so
    the frontend never receives audio and does not trigger speech synthesis.
    """
    await websocket.accept()
    await websocket.close(code=1000, reason="avatar TTS disabled")


@app.post("/ask", response_model=AskResponse)
async def ask_route(body: AskRequest) -> AskResponse:
    """One-shot RAG answer with its sources (no session, no teaching flow)."""
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(
            _executor,
            lambda: pipeline.query_rag(question=body.question),
        )
        answer  = str(result.get("answer", ""))
        sources = _chunks_to_sources(result.get("chunks", []))
        return AskResponse(answer=answer, sources=sources)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/ask/stream")
async def ask_stream(body: AskRequest):
    """
    Stream one tutoring turn as Server-Sent Events. One JSON object per data line:

      {"type": "widget_chunk", "text": "..."}    incremental widget HTML
      {"type": "teacher_chunk", "text": "..."}   incremental teacher text
      {"type": "audio_chunk", "index": N, "text": "...",
       "audio_base64": "...", "alignment": {...}}
                                                 one TTS sentence, in order, as
                                                 soon as it is ready
      {"type": "audio_done", "count": N}         no more audio_chunk events
      {"type": "done", "widget": "...", "teacher": "..."}   full split output
      {"type": "stream_end"}                     generation complete
      {"type": "error", "message": "..."}        something went wrong
    """
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    history = [
        {"role": m.role, "content": m.content, "representation": m.representation}
        for m in (body.history or [])
    ]

    return StreamingResponse(
        _traced_stream(body.question, history, body.session_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


@app.post("/api/generate-title", response_model=GenerateTitleResponse)
async def generate_title_route(body: GenerateTitleRequest) -> GenerateTitleResponse:
    """Chat title = the first 42 characters of the first message."""
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message must not be empty")
    clean = body.message.strip().replace("\n", " ")
    title = clean[:42] + "…" if len(clean) > 42 else clean
    return GenerateTitleResponse(title=title)


# ── SSE streaming ────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def _traced_stream(
    question  : str,
    history   : list[dict],
    session_id: str,
) -> AsyncGenerator[str, None]:
    """
    Run _handle_turn() in a worker thread (keeping the LangSmith context), then
    pipe its token queue through stream_handler.handle_stream(), which splits
    [WIDGET] / [TEACHER] and yields SSE lines.
    """
    loop        = asyncio.get_running_loop()
    ctx         = contextvars.copy_context()
    token_queue: asyncio.Queue[str | None] = asyncio.Queue()
    turn_meta   = _TurnMeta()

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
                turn_meta,
            ),
        )
    )

    # The first item tells us whether this turn ends immediately (sentinels).
    first = await token_queue.get()

    if first == _NO_LESSON:
        yield _sse({"type": "done", "widget": "", "teacher": _NO_LESSON_REPLY})
        yield _sse({"type": "stream_end"})
        return

    if isinstance(first, str) and first.startswith(_OUT_OF_PROGRAM_PREFIX):
        text = first[len(_OUT_OF_PROGRAM_PREFIX):]
        yield _sse({"type": "done", "widget": "", "teacher": text})
        yield _sse({"type": "stream_end"})
        return

    if first is None:
        yield _sse({"type": "stream_end"})
        return

    # Put the first token back in front of the rest of the queue.
    async def _token_gen(first_token=first):
        yield first_token
        while True:
            tok = await token_queue.get()
            if tok is None:
                return
            yield tok

    async for sse_line in handle_stream(
        question          = question,
        lesson            = _LESSON_STORE.get(session_id, {}),
        history           = history,
        tc_injection      = "",   # already injected inside _handle_turn
        token_stream      = _token_gen(),
        mode              = turn_meta.mode,
        active_point_text = turn_meta.active_point_text,
        active_point_idx  = turn_meta.active_point_idx,
    ):
        yield sse_line


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8001, reload=False)