"""
stream_handler.py — RAG pipeline
==================================

Responsibilities:
  - Accepts a token stream (AsyncGenerator) produced by _handle_turn()
  - Runs it through stream_splitter to separate [WIDGET] / [TEACHER]
  - Yields SSE-formatted lines for FastAPI StreamingResponse

SSE event format (one JSON object per line, blank line as separator):
  data: {"type": "widget_chunk", "text": "..."}
  data: {"type": "done", "widget": "...", "teacher": "..."}
  data: {"type": "stream_end"}
  data: {"type": "error", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import threading
import traceback
import queue as _queue
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, Optional

from llm             import generate_answer
from stream_splitter import split_stream, WidgetChunk, TeacherChunk, DoneEvent


_handler_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stream_llm")


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── Token source helpers ──────────────────────────────────────────────────────

async def _stream_to_queue(
    token_stream: AsyncGenerator[str, None],
) -> asyncio.Queue[str | None]:
    """Drains an async generator into a queue."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _load():
        try:
            async for token in token_stream:
                await queue.put(token)
        except Exception as exc:
            await queue.put(f"\n[STREAM ERROR] {exc}")
        finally:
            await queue.put(None)

    asyncio.create_task(_load())
    return queue


async def _iter_llm_stream(
    question    : str,
    lesson      : dict,
    history     : list[dict],
    tc_injection: str = "",
) -> asyncio.Queue[str | None]:
    """
    Fallback: calls generate_answer() in a thread and pushes tokens into a queue.
    Used only when no pre-generated token_stream is provided.
    """
    loop  = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _produce():
        try:
            for token in generate_answer(
                question     = question,
                lesson       = lesson,
                stream       = True,
                history      = history,
                tc_injection = tc_injection,
            ):
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, f"\n[LLM ERROR] {exc}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(_handler_executor, _produce)
    return queue


# ── Async bridge for split_stream (sync generator → async) ───────────────────

async def _async_split(token_queue: asyncio.Queue[str | None]):
    sync_q:   _queue.Queue[str | None] = _queue.Queue()
    result_q: asyncio.Queue            = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def _run_splitter():
        def _token_iter():
            while True:
                tok = sync_q.get()
                if tok is None:
                    return
                yield tok
        try:
            for event in split_stream(_token_iter()):
                loop.call_soon_threadsafe(result_q.put_nowait, event)
        except Exception as exc:
            loop.call_soon_threadsafe(result_q.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(result_q.put_nowait, None)

    thread = threading.Thread(target=_run_splitter, daemon=True)
    thread.start()

    async def _forward():
        while True:
            tok = await token_queue.get()
            sync_q.put(tok)
            if tok is None:
                break

    asyncio.create_task(_forward())

    while True:
        event = await result_q.get()
        if event is None:
            break
        if isinstance(event, Exception):
            raise event
        yield event


# ── Main entry point ──────────────────────────────────────────────────────────

async def handle_stream(
    question     : str,
    lesson       : dict,
    history      : list[dict] | None = None,
    tc_injection : str = "",
    token_stream : Optional[AsyncGenerator[str, None]] = None,
) -> AsyncGenerator[str, None]:
    """
    Splits the token stream and yields SSE-formatted strings.

    token_stream:
      - If provided (from test_api._handle_turn), tokens come from it.
        generate_answer() is NOT called here — already called inside
        _handle_turn() so everything nests under one LangSmith parent.
      - If None (backwards compatible), calls _iter_llm_stream() which
        calls generate_answer() internally.

    Yields SSE lines consumed by FastAPI StreamingResponse.
    """
    history = history or []

    # Choose token source
    if token_stream is not None:
        token_queue = await _stream_to_queue(token_stream)
    else:
        token_queue = await _iter_llm_stream(question, lesson, history, tc_injection)

    try:
        async for event in _async_split(token_queue):

            if isinstance(event, WidgetChunk):
                yield _sse({"type": "widget_chunk", "text": event.text})

            elif isinstance(event, TeacherChunk):
                # Audio disabled — teacher text still reaches frontend via DoneEvent.
                pass

            elif isinstance(event, DoneEvent):
                yield _sse({
                    "type"   : "done",
                    "widget" : event.widget,
                    "teacher": event.teacher,
                })

    except Exception:
        traceback.print_exc()
        yield _sse({"type": "error", "message": "Internal server error"})
        return

    yield _sse({"type": "stream_end"})