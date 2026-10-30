"""
llm.py — Answer generation with Gemini (through LangChain).

Builds the prompt (prompt_builder.py), calls Gemini and streams the tokens back.
If a model keeps failing, the call falls back to the next model in MODEL_CHAIN
(the primary model is retried with exponential backoff first). Every step is
traced in LangSmith.

Public API:
    generate_answer(question, lesson, stream=True, ...)  -> str | Generator[str]
    warm_model_chain()        build every model client once, at server startup
    print_prompt_preview(...) debug helper: show the prompt without calling the LLM

CLI:
    python llm.py --question "..." [--no-stream] [--preview] [--mode explanation]
"""

from __future__ import annotations

import argparse
import json
import os
import time
from typing import Dict, Generator, List, Optional

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langsmith import traceable

from prompt_builder import (
    build_prompt,
    _inject_visual_reminder,
    detect_language,  # noqa: F401  (re-exported: api.py imports it from here)
)

load_dotenv()

__all__ = ["generate_answer", "warm_model_chain", "print_prompt_preview", "detect_language"]

# ─────────────────────────── CONFIG ────────────────────────────

# Models tried in order. The first one is the primary; the others are fallbacks.
# Check every id against the current Gemini API model list: a wrong id only
# fails when the chain reaches it.
MODEL_CHAIN = [
    "gemini-2.5-flash",
    "gemini-3.5-flash",        # fallback 1
    "gemini-2.5-flash-lite",   # fallback 2
]

MAX_TOKENS    = 16000
TEMPERATURE   = 0.5
TOP_P         = 0.95
HISTORY_LIMIT = 10   # number of past messages sent to the model

# The tutor answers in Arabic only (7th-grade classes are Arabic-medium).
LANG = "ar"

API_KEY = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")

# Text-only modes: no visual-representation reminder is added to the question.
_NO_VISUAL_REMINDER_MODES = {"exercise", "exercise_answer"}


# ─────────────────────────── CHAT MODELS ───────────────────────

# One cached client per model name. Building a client has a one-time cost of a
# few seconds, so warm_model_chain() pays it at startup instead of on the first
# user request.
_MODEL_INSTANCES: Dict[str, ChatGoogleGenerativeAI] = {}


def _get_chat_model(model_name: str) -> ChatGoogleGenerativeAI:
    if not API_KEY:
        raise EnvironmentError(
            "[llm] GEMINI_API_KEY not set. Add it to your .env as GEMINI_API_KEY=..."
        )
    if model_name not in _MODEL_INSTANCES:
        t0 = time.time()
        _MODEL_INSTANCES[model_name] = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=API_KEY,
            max_output_tokens=MAX_TOKENS,
            temperature=TEMPERATURE,
            top_p=TOP_P,
            # Turns off Gemini 2.5's internal "thinking" pass: in live tests this
            # cut a call from ~13-15 s to ~2-7 s. It only applies to 2.5 models;
            # Gemini 3+ models use thinking_level instead.
            thinking_budget=0,
        )
        print(
            f"[llm:diag] built ChatGoogleGenerativeAI({model_name}) in "
            f"{time.time() - t0:.2f}s"
        )
    return _MODEL_INSTANCES[model_name]


def warm_model_chain() -> None:
    """
    Build and cache a client for every model in MODEL_CHAIN. Call once at server
    startup. Safe to call again (cached). Errors are not swallowed: a missing key
    or a bad model name should fail at startup, not on a user's first request.
    """
    for model_name in MODEL_CHAIN:
        _get_chat_model(model_name)


# ─────────────────────── MESSAGE BUILDER ───────────────────────

def _build_messages(
    system  : str,
    question: str,
    history : List[Dict[str, str]],
) -> List[BaseMessage]:
    """System prompt + the last HISTORY_LIMIT turns + the new question."""
    messages: List[BaseMessage] = [SystemMessage(content=system)]
    trimmed = history[-HISTORY_LIMIT:] if history else []

    for msg in trimmed:
        role    = msg.get("role", "user")
        content = msg.get("content", "").strip()
        if not content:
            continue
        if role in ("assistant", "model", "ai"):
            messages.append(AIMessage(content=content))
        else:
            messages.append(HumanMessage(content=content))

    messages.append(HumanMessage(content=question))
    return messages


# Traced wrappers, so prompt preparation shows up as its own steps in LangSmith.

@traceable(name="build_prompt_step", run_type="tool")
def _traced_build_prompt(
    lesson,
    lang,
    question,
    tc_injection,
    mode,
    active_point_text,
    point_idx,
    used_representations,
    extra_modes=None,
):
    return build_prompt(
        lesson, lang, question=question, tc_injection=tc_injection, mode=mode,
        active_point_text=active_point_text,
        point_idx=point_idx,
        used_representations=used_representations,
        extra_modes=extra_modes,
    )


@traceable(name="build_messages_step", run_type="tool")
def _traced_build_messages(system: str, question: str, history: List[Dict[str, str]]):
    return _build_messages(system, question, history)


# ─────────────────────── GENERATE ANSWER ───────────────────────

def _reduce_stream(chunks: List[str]) -> dict:
    """Reduce function so LangSmith records a streamed answer as one output."""
    return {"output": "".join(chunks)}


@traceable(name="generate_answer", run_type="llm", reduce_fn=_reduce_stream)
def generate_answer(
    question          : str,
    lesson            : dict,
    stream            : bool = True,
    history           : List[Dict[str, str]] | None = None,
    tc_injection      : str = "",
    active_point_text : str = "",
    active_point_idx  : Optional[int] = None,
    mode              : str = "explanation",
    used_representations: Optional[List[str]] = None,
    extra_modes       : Optional[List[str]] = None,
) -> str | Generator[str, None, None]:
    """
    Generate the tutor's answer for one turn.

    Returns a generator of text chunks when stream=True, otherwise the full text.
    `tc_injection` is the teaching-controller instruction text for this turn;
    `extra_modes` are secondary modes of a combined request (e.g. explanation + summary).
    """
    if not question.strip():
        return "" if not stream else iter([""])

    history_list = history or []

    if mode in _NO_VISUAL_REMINDER_MODES:
        question_with_reminder = question
    else:
        question_with_reminder = _inject_visual_reminder(
            question, LANG, active_point_text=active_point_text,
        )

    system = _traced_build_prompt(
        lesson, LANG, question, tc_injection, mode,
        active_point_text, active_point_idx, used_representations,
        extra_modes,
    )
    messages = _traced_build_messages(system, question_with_reminder, history_list)

    print(
        f"[llm:diag] mode={mode} system_chars={len(system)} "
        f"question_chars={len(question_with_reminder)} "
        f"history_msgs={len(history_list[-HISTORY_LIMIT:]) if history_list else 0}"
    )

    return (
        _stream_response(messages)
        if stream
        else _blocking_response(messages)
    )


# ─────────────────────── GEMINI CALLS ──────────────────────────

@traceable(name="gemini_blocking_call", run_type="llm")
def _blocking_response(messages: List[BaseMessage]) -> str:
    """
    Call the models in MODEL_CHAIN order. The primary model gets 2 attempts with
    backoff, each fallback 1. Re-raises the last error if every model fails.
    """
    last_err: Exception | None = None

    for model_name in MODEL_CHAIN:
        is_primary  = (model_name == MODEL_CHAIN[0])
        max_retries = 2 if is_primary else 1
        model       = _get_chat_model(model_name)

        for attempt in range(max_retries):
            attempt_start = time.time()
            try:
                response = model.invoke(messages)
                call_duration = time.time() - attempt_start
                print(
                    f"[llm:diag] {model_name} blocking call succeeded in "
                    f"{call_duration:.2f}s (attempt {attempt + 1}/{max_retries})"
                )
                finish_reason = (response.response_metadata or {}).get("finish_reason")
                if finish_reason == "MAX_TOKENS":
                    print("[llm] ⚠️  WARNING: response cut off — MAX_TOKENS reached")
                if not is_primary:
                    print(f"[llm] ✅ succeeded on fallback model: {model_name}")
                return response.content or ""

            except Exception as e:
                call_duration = time.time() - attempt_start
                last_err = e
                print(
                    f"[llm] {model_name} error (attempt {attempt + 1}/{max_retries}) "
                    f"after {call_duration:.2f}s: {e}"
                )
                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[llm:diag] Retrying {model_name} in {wait}s (backoff sleep)…")
                    time.sleep(wait)

        print(f"[llm] {model_name} exhausted — trying next model in chain")

    print("[llm] All models in chain exhausted — raising")
    raise last_err if last_err else RuntimeError("MODEL_CHAIN is empty")


@traceable(name="gemini_stream_call", run_type="llm", reduce_fn=_reduce_stream)
def _stream_response(
    messages: List[BaseMessage],
) -> Generator[str, None, None]:
    """
    Streaming version of _blocking_response: primary model 3 attempts with
    backoff, each fallback 1. If a model fails AFTER it already yielded text, we
    raise instead of retrying or falling back, because the client has already
    received part of the answer.
    """
    last_err: Exception | None = None

    for model_name in MODEL_CHAIN:
        is_primary  = (model_name == MODEL_CHAIN[0])
        max_retries = 3 if is_primary else 1
        model       = _get_chat_model(model_name)

        for attempt in range(max_retries):
            yielded_any   = False
            attempt_start = time.time()
            first_chunk_at: Optional[float] = None
            chunk_count   = 0

            try:
                for chunk in model.stream(messages):
                    text = chunk.content
                    if text:
                        if not yielded_any:
                            first_chunk_at = time.time()
                            print(
                                f"[llm:diag] {model_name} TTFT="
                                f"{first_chunk_at - attempt_start:.2f}s "
                                f"(attempt {attempt + 1}/{max_retries})"
                            )
                        yielded_any = True
                        chunk_count += 1
                        yield text

                total_duration = time.time() - attempt_start
                print(
                    f"[llm:diag] {model_name} stream finished — total="
                    f"{total_duration:.2f}s chunks={chunk_count} "
                    f"(attempt {attempt + 1}/{max_retries})"
                )
                if not is_primary:
                    print(f"[llm] ✅ succeeded on fallback model: {model_name}")
                return

            except Exception as e:
                total_duration = time.time() - attempt_start
                last_err = e
                print(
                    f"[llm] {model_name} streaming error (attempt {attempt + 1}/{max_retries}) "
                    f"after {total_duration:.2f}s (yielded_any={yielded_any}): {e}"
                )

                if yielded_any:
                    print("[llm] Partial content already streamed — aborting, no fallback")
                    raise

                if attempt < max_retries - 1:
                    wait = 2 ** attempt
                    print(f"[llm:diag] Retrying {model_name} in {wait}s (backoff sleep)…")
                    time.sleep(wait)

        print(f"[llm] {model_name} exhausted — trying next model in chain")

    print("[llm] All models in chain exhausted — raising")
    raise last_err if last_err else RuntimeError("MODEL_CHAIN is empty")


# ─────────────────────── DEBUG / REPORT ────────────────────────

def print_prompt_preview(
    question         : str,
    lesson           : dict,
    tc_injection     : str = "",
    active_point_text: str = "",
    active_point_idx : Optional[int] = None,
    mode             : str = "explanation",
    used_representations: Optional[List[str]] = None,
) -> None:
    """Print the system prompt and augmented question without calling the LLM."""
    prompt = build_prompt(
        lesson, LANG, question=question, tc_injection=tc_injection,
        active_point_text=active_point_text, mode=mode,
        point_idx=active_point_idx,
        used_representations=used_representations,
    )

    if mode in _NO_VISUAL_REMINDER_MODES:
        augmented_q = question
    else:
        augmented_q = _inject_visual_reminder(
            question, LANG, active_point_text=active_point_text,
        )

    print("\n" + "=" * 60)
    print(f"  PROMPT PREVIEW  (lang={LANG}, mode={mode}, "
          f"active_point_idx={active_point_idx})")
    print("=" * 60)
    print(f"  [diag] system_chars={len(prompt)} question_chars={len(augmented_q)}")
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

def main() -> None:
    parser = argparse.ArgumentParser(description="LLM step — Gemini (with fallback chain)")
    parser.add_argument("--question",    required=True)
    parser.add_argument("--lesson",      default=None, help="Path to a lesson JSON file")
    parser.add_argument("--no-stream",   action="store_true")
    parser.add_argument("--preview",     action="store_true")
    parser.add_argument("--tc-injection", default="", dest="tc_injection",
                        help="Simulate a teaching controller injection string")
    parser.add_argument("--active-point-text", default="", dest="active_point_text",
                        help="Simulate the active lesson point text (debug only)")
    parser.add_argument("--active-point-idx", type=int, default=None, dest="active_point_idx",
                        help="Simulate the active lesson point index (debug only)")
    parser.add_argument("--mode", default="explanation", dest="mode",
                        help="explanation / continuation / exercise / exercise_answer / summary")
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
        print_prompt_preview(
            args.question, lesson,
            tc_injection=args.tc_injection,
            active_point_text=args.active_point_text,
            active_point_idx=args.active_point_idx,
            mode=args.mode,
        )
        return

    print(f"\n[llm] Model chain: {MODEL_CHAIN}  |  stream={stream}  |  temp={TEMPERATURE}")
    print(f"[llm] Language: {LANG} (locked)")
    print(f"[llm] Mode: {args.mode}")
    if args.tc_injection:
        print(f"[llm] TC injection: {args.tc_injection[:80]}...")
    if args.active_point_text:
        print(f"[llm] Active point text: {args.active_point_text[:80]}...")
    if args.active_point_idx is not None:
        print(f"[llm] Active point idx: {args.active_point_idx}")
    print("-" * 60)

    result = generate_answer(
        args.question, lesson,
        stream=stream,
        tc_injection=args.tc_injection,
        active_point_text=args.active_point_text,
        active_point_idx=args.active_point_idx,
        mode=args.mode,
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