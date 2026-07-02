"""
llm.py — Étape finale du pipeline RAG
======================================
Calls Gemini to generate the streaming/blocking answer, using the prompt
built by prompt_builder.py (system instructions, visual reminders, lesson
content, and teaching-controller injection).

Handles: history/memory assembly, retry logic on streaming errors, LangSmith
tracing (with reduce_fn to properly log streamed output), and a CLI for
manual testing (--preview to inspect the built prompt).

Note: prompt/template construction (mindmap, canvas, visual instructions,
build_prompt) lives in prompt_builder.py — this file only orchestrates the
actual Gemini calls.
"""

from __future__ import annotations

import os
import time
from typing import Generator, List, Dict, Optional

from google import genai
from google.genai import types

from langsmith import traceable

from prompt_builder import (
    build_prompt,
    _inject_visual_reminder,
    detect_language,
    _get_visual_type,
)

# ─────────────────────────── CONFIG ────────────────────────────

GEMINI_MODEL  = "gemini-2.5-flash"
MAX_TOKENS    = 16000
TEMPERATURE   = 0.7
TOP_P         = 0.95

HISTORY_LIMIT = 10

API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")


# ─────────────────────────── CLIENT ────────────────────────────

def _get_client() -> genai.Client:
    if not API_KEY:
        raise EnvironmentError(
            "[llm] GEMINI_API_KEY not set. "
            "Add it to your .env as GEMINI_API_KEY=..."
        )
    return genai.Client(api_key=API_KEY)


# ─────────────────────── HISTORY BUILDER ───────────────────────

def _build_memory(
    system  : str,
    question: str,
    history : List[Dict[str, str]],
) -> tuple[str, list]:
    memory  = []
    trimmed = history[-HISTORY_LIMIT:] if history else []

    for msg in trimmed:
        role    = msg.get("role", "user")
        content = msg.get("content", "").strip()
        if not content:
            continue
        memory.append(types.Content(
            role=role,
            parts=[types.Part(text=content)],
        ))

    memory.append(types.Content(
        role="user",
        parts=[types.Part(text=question)],
    ))

    return system, memory


# ─────────────────────── GENERATE ANSWER ───────────────────────

def _reduce_stream(chunks: List[str]) -> dict:
    """
    Used as reduce_fn for @traceable on streaming generator functions.
    LangSmith calls this with the full list of yielded chunks once the
    generator is exhausted, and logs the returned dict as the run's Output.
    Without this, a traced generator's Output stays null, because
    @traceable has no way to know how to collapse yielded chunks into a
    single "final" value on its own.
    """
    return {"output": "".join(chunks)}


@traceable(name="generate_answer", run_type="llm", reduce_fn=_reduce_stream)
def generate_answer(
    question     : str,
    lesson       : dict,
    stream       : bool = True,
    history      : List[Dict[str, str]] | None = None,
    tc_injection : str = "",
) -> str | Generator[str, None, None]:
    if not question.strip():
        return "" if not stream else iter([""])

    history_list  = history or []
    first_message = history_list[0]["content"] if history_list else question
    lang          = detect_language(first_message)

    visual_type    = _get_visual_type(lesson)
    lesson_content = (lesson.get("content") or "")
    question_with_reminder = _inject_visual_reminder(
        question, visual_type, lang, lesson_content=lesson_content
    )

    system = build_prompt(lesson, lang, question=question, tc_injection=tc_injection)
    client = _get_client()
    system, memory = _build_memory(system, question_with_reminder, history_list)

    return (
        _stream_response(client, system, memory)
        if stream
        else _blocking_response(client, system, memory)
    )


# ─────────────────────── GEMINI CALLS ──────────────────────────

@traceable(name="gemini_blocking_call", run_type="llm")
def _blocking_response(client: genai.Client, system: str, memory: list) -> str:
    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=memory,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                top_p=TOP_P,
            ),
        )
        candidate = response.candidates[0] if response.candidates else None
        if candidate and candidate.finish_reason.name == "MAX_TOKENS":
            print("[llm] ⚠️  WARNING: response cut off — MAX_TOKENS reached")
        return response.text or ""
    except Exception as e:
        print(f"[llm] Gemini error: {e}")
        raise


@traceable(name="gemini_stream_call", run_type="llm", reduce_fn=_reduce_stream)
def _stream_response(
    client: genai.Client,
    system: str,
    memory: list,
) -> Generator[str, None, None]:
    max_retries = 3
    for attempt in range(max_retries):
        try:
            for chunk in client.models.generate_content_stream(
                model=GEMINI_MODEL,
                contents=memory,
                config=types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=MAX_TOKENS,
                    temperature=TEMPERATURE,
                    top_p=TOP_P,
                ),
            ):
                if chunk.text:
                    yield chunk.text
            return

        except Exception as e:
            print(f"[llm] Gemini streaming error (attempt {attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"[llm] Retrying in {wait}s…")
                time.sleep(wait)
            else:
                print("[llm] All retries exhausted — raising")
                raise


# ─────────────────────── DEBUG / REPORT ────────────────────────

def print_prompt_preview(question: str, lesson: dict, tc_injection: str = ""):
    lang        = detect_language(question)
    visual_type = _get_visual_type(lesson)
    prompt      = build_prompt(lesson, lang, question=question, tc_injection=tc_injection)
    augmented_q = _inject_visual_reminder(
        question, visual_type, lang, lesson_content=(lesson.get("content") or "")
    )

    print("\n" + "=" * 60)
    print(f"  PROMPT PREVIEW  (lang={lang}, visual_type={visual_type})")
    print("=" * 60)
    print(prompt[:3000])
    if len(prompt) > 3000:
        print(f"\n  ... [{len(prompt) - 3000} chars truncated]")
    print("\n" + "-" * 60)
    print("  AUGMENTED QUESTION:")
    print("-" * 60)
    print(augmented_q)
    if tc_injection:
        print("\n" + "-" * 60)
        print("  TC_INJECTION:")
        print("-" * 60)
        print(tc_injection)
    print("=" * 60 + "\n")


# ─────────────────────────── CLI ───────────────────────────────

def main():
    import argparse, json

    parser = argparse.ArgumentParser(description="LLM step — Gemini 2.5 Flash")
    parser.add_argument("--question",    required=True)
    parser.add_argument("--lesson",      default=None)
    parser.add_argument("--no-stream",   action="store_true")
    parser.add_argument("--preview",     action="store_true")
    parser.add_argument("--tc-injection",default="", dest="tc_injection",
                        help="Simulate a teaching controller injection string")
    args = parser.parse_args()

    if args.lesson:
        with open(args.lesson, encoding="utf-8") as f:
            lesson = json.load(f)
    else:
        lesson = {
            "lesson_title":  "القوى في الأعداد الطبيعية N",
            "lesson_number": "2",
            "level":         "7eme",
            "subject":       "math",
            "content": (
                "- تعريف القوة: مفهوم القوة وكيفية كتابتها وحسابها\n"
                "- القيمة التقريبية: تقدير القيمة التقريبية للأعداد التي تحتوي على قوى\n"
                "- خصائص القوى: ضرب قوتين لهما نفس القاعدة، ضرب قوتين لهما نفس الدليل، قوة قوة\n"
                "- أولويات العمليات عند وجود القوى\n"
                "- المربعات الكاملة"
            ),
        }

    stream = not args.no_stream

    if args.preview:
        print_prompt_preview(args.question, lesson, tc_injection=args.tc_injection)
        return

    print(f"\n[llm] Model: {GEMINI_MODEL}  |  stream={stream}  |  temp={TEMPERATURE}")
    print(f"[llm] Language: {detect_language(args.question)}")
    print(f"[llm] Visual type: {_get_visual_type(lesson)}")
    if args.tc_injection:
        print(f"[llm] TC injection: {args.tc_injection[:80]}...")
    print("-" * 60)

    result = generate_answer(
        args.question, lesson,
        stream=stream,
        tc_injection=args.tc_injection,
    )

    if stream:
        for token in result:
            print(token, end="", flush=True)
        print()
    else:
        print(result)

    print("\n" + "-" * 60)


if __name__ == "__main__":
    main()