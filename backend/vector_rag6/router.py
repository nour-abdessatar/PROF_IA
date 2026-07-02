"""
router.py — Intent classification layer
One fast Gemini call before retrieval runs.

Classifies student messages into: lesson request, follow-up, exercise,
summary, or general action — determining whether to retrieve a lesson,
use conversation memory, or answer directly.

Key details:
- Hard pre-check catches obvious lesson requests (درس/leçon + number)
  before calling the LLM, avoiding misclassification as follow_up.
- Thinking disabled (thinking_budget=0) + forced JSON output to prevent
  truncated/malformed responses from gemini-2.5-flash-lite.
- _call_router_llm() is a separate traced function so LangSmith shows
  the exact prompt sent to Gemini, not just classify()'s arguments.
"""

from __future__ import annotations
import json, re
from dataclasses import dataclass
from typing import Literal, Optional, List, Dict
from google import genai
from google.genai import types
from langsmith import traceable

ROUTER_MODEL  = "gemini-2.5-flash-lite"
ROUTER_TOKENS = 512

@dataclass
class RouterOutput:
    intent       : Literal["action", "lesson", "follow_up", "exercise", "summary", "unknown"]
    action       : Literal["direct_llm", "retrieve", "retrieve_if_no_lesson", "use_memory"]
    mode         : Literal["explanation", "continuation", "exercise", "summary", "conversational"]
    need_visual  : bool
    visual_type  : Optional[Literal["geometry", "graph", "diagram", "animation"]]
    topic        : Optional[str]
    lesson_number: Optional[str]


_ROUTER_SYSTEM = """
You are a routing classifier for a Tunisian middle-school math assistant.
The assistant teaches in Arabic or French.
The LLM already knows the math — the retriever only fetches *which lesson* to teach.

Given the student message and the last 2 conversation turns, return ONLY the raw JSON object.
No backticks, no markdown, no explanation, no preamble.
Start your response with { and end with }.

JSON schema:
{
  "intent":        "action" | "lesson" | "follow_up" | "exercise" | "summary" | "unknown",
  "action":        "direct_llm" | "retrieve" | "retrieve_if_no_lesson" | "use_memory",
  "mode":          "explanation" | "continuation" | "exercise" | "summary" | "conversational",
  "need_visual":   true | false,
  "visual_type":   "geometry" | "graph" | "diagram" | "animation" | null,
  "topic":         "<subject string>" | null,
  "lesson_number": "<digit string e.g. '2'>" | null
}

Classification rules:

LESSON REQUEST (intent=lesson, action=retrieve, mode=explanation):
  Trigger when student asks to learn/explain a lesson. Examples:
  - "فسرلي الدرس 1" / "فسرلي الدرس الأول" / "درس 2" / "الدرس الثالث"
  - "شرح الدرس" / "اشرح لي" / "علمني" / "فهمني"
  - "leçon 1" / "explique moi la leçon" / "cours sur les fractions"
  - ANY message containing "درس" + a number or ordinal
  - ANY message containing "leçon" or "cours" + a number
  → action=retrieve, mode=explanation

FOLLOW UP (intent=follow_up, action=use_memory, mode=continuation):
  Trigger ONLY when student sends a SHORT confirmation with NO lesson reference.
  Examples:
  - Arabic: نعم, فهمت, تمام, أكيد, يلا, واضح, حسناً, ايه, اه
  - French: oui, ok, compris, j'ai compris, d'accord, allez, vas-y, ouais
  WARNING: If the message contains "درس" or "leçon" or "cours" alongside
  confirmation words, it is STILL a LESSON REQUEST, not a follow_up.

EXERCISE (intent=exercise, action=retrieve_if_no_lesson, mode=exercise):
  Trigger when student asks for practice. Examples:
  - "تمرين" / "مسألة" / "exercice" / "donne moi un exercice"

SUMMARY (intent=summary, action=use_memory, mode=summary):
  Trigger when student asks for recap. Examples:
  - "ملخص" / "لخص" / "résumé" / "récapitule"

ACTION (intent=action, action=direct_llm, mode=conversational):
  Greetings, off-topic, simple questions NOT about a lesson.
  - "مرحبا" / "شكرا" / "bonjour" / "merci"

UNKNOWN -> action=direct_llm, mode=conversational

RULES:
- "فسرلي الدرس X" ALWAYS -> intent=lesson, action=retrieve
- lesson_number: extract the digit from "درس 2", "الدرس الثاني"->"2", "leçon 3"->"3"
- need_visual=true ONLY when geometry keywords present or student says "ارسم"/"dessine"
- When in doubt between lesson and unknown, choose lesson
- NEVER classify as follow_up if the message contains "درس", "leçon", or "cours"

Arabic ordinals mapping:
الأول->1, الثاني->2, الثالث->3, الرابع->4, الخامس->5,
السادس->6, السابع->7, الثامن->8, التاسع->9, العاشر->10
"""

_JSON_RE = re.compile(r"\{.*\}", re.S)

_FALLBACK = RouterOutput(
    intent="unknown", action="direct_llm", mode="conversational",
    need_visual=False, visual_type=None, topic=None, lesson_number=None
)

_ORDINALS = {
    "الأول": "1", "الثاني": "2", "الثالث": "3", "الرابع": "4", "الخامس": "5",
    "السادس": "6", "السابع": "7", "الثامن": "8", "التاسع": "9", "العاشر": "10",
}

_LESSON_KEYWORDS_AR = ["درس", "فسرلي", "فسر لي", "شرح", "اشرح", "علمني", "فهمني"]
_LESSON_KEYWORDS_FR = ["leçon", "cours", "explique", "apprends"]


def _hard_lesson_check(question: str) -> Optional[RouterOutput]:
    """
    Pre-check BEFORE calling the LLM.
    If the message unambiguously requests a lesson, return a RouterOutput immediately.
    Prevents misclassification when history makes the LLM think it's a follow_up.
    """
    q = question.strip()

    has_lesson_kw = (
        any(kw in q for kw in _LESSON_KEYWORDS_AR) or
        any(kw in q.lower() for kw in _LESSON_KEYWORDS_FR)
    )
    if not has_lesson_kw:
        return None

    digit_match = re.search(r'\d+', q)
    lesson_num = digit_match.group() if digit_match else None

    if not lesson_num:
        for ordinal, num in _ORDINALS.items():
            if ordinal in q:
                lesson_num = num
                break

    strong_kws = ["فسرلي", "فسر لي", "اشرح", "علمني", "فهمني", "explique", "apprends"]
    has_strong_kw = any(kw in q.lower() for kw in strong_kws)

    if lesson_num or has_strong_kw:
        print(f"[router] hard-classified as LESSON (lesson_number={lesson_num}) — skipped LLM")
        return RouterOutput(
            intent        = "lesson",
            action        = "retrieve",
            mode          = "explanation",
            need_visual   = True,
            visual_type   = "geometry",
            topic         = None,
            lesson_number = lesson_num,
        )

    return None


@traceable(name="router_llm_call", run_type="llm")
def _call_router_llm(
    client            : genai.Client,
    system_instruction: str,
    user_msg          : str,
) -> str:
    """
    Isolated so LangSmith logs the ACTUAL prompt sent to Gemini
    (system_instruction + user_msg) as this run's Input, instead of only
    showing classify()'s own arguments (question/history/api_key).
    """
    response = client.models.generate_content(
        model=ROUTER_MODEL,
        contents=[types.Content(role="user", parts=[types.Part(text=user_msg)])],
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            max_output_tokens=ROUTER_TOKENS,
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
            response_mime_type="application/json",
        ),
    )

    finish_reason = None
    if response.candidates:
        finish_reason = getattr(response.candidates[0], "finish_reason", None)
    if finish_reason and str(finish_reason) not in ("STOP", "FinishReason.STOP"):
        print(f"[router] WARNING: generation finished with reason={finish_reason}")

    return response.text or ""


@traceable(name="router_classify", run_type="chain")
def classify(
    question: str,
    history : List[Dict[str, str]] | None = None,
    api_key : str = "",
) -> RouterOutput:
    """
    Run the router and return a RouterOutput.
    Tries hard pre-check first (no LLM call needed for clear lesson requests).
    Falls back to LLM classification, then to _FALLBACK on any error.
    """
    hard = _hard_lesson_check(question)
    if hard is not None:
        return hard

    try:
        client = genai.Client(api_key=api_key)

        recent = (history or [])[-4:]
        context_lines = "\n".join(
            f"{m['role'].upper()}: {m['content'][:200]}" for m in recent
        )
        user_msg = f"{context_lines}\n\nSTUDENT NOW: {question}" if context_lines else question

        raw = _call_router_llm(client, _ROUTER_SYSTEM, user_msg)
        print(f"[router] raw response: {raw}")

        if not raw.strip():
            print("[router] empty response (likely truncated/blocked) — using fallback")
            return _FALLBACK

        m = _JSON_RE.search(raw)
        if not m:
            print(f"[router] no JSON found — using fallback")
            return _FALLBACK

        try:
            data = json.loads(m.group())
        except json.JSONDecodeError as e:
            print(f"[router] JSON parse failed ({e}) on raw: {raw!r} — using fallback")
            return _FALLBACK

        return RouterOutput(
            intent        = data.get("intent",        "unknown"),
            action        = data.get("action",        "direct_llm"),
            mode          = data.get("mode",          "conversational"),
            need_visual   = bool(data.get("need_visual", False)),
            visual_type   = data.get("visual_type"),
            topic         = data.get("topic"),
            lesson_number = data.get("lesson_number"),
        )

    except Exception as e:
        print(f"[router] classify() failed: {e} — using fallback")
        return _FALLBACK