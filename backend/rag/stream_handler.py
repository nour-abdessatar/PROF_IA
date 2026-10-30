"""
stream_handler.py — turns the LLM token stream into SSE events for FastAPI.

Pipeline for one turn:
  token stream -> stream_splitter ([WIDGET] / [SPEECH] / [TEACHER])
               -> widget parse / build / repair -> optional TTS -> SSE lines

SSE events (one JSON object per `data:` line, blank line as separator):
  {"type": "widget_chunk",  "text": "..."}     progress only (raw widget JSON fragments)
  {"type": "teacher_chunk", "text": "..."}     raw teacher text, streamed live
  {"type": "audio_chunk",   "index": 0, "text": "...", "audio_base64": "...", "alignment": ...}
  {"type": "audio_done",    "count": 0 | 1}
  {"type": "done", "widget": "...", "teacher": "...", "representation": "..." | null}
  {"type": "stream_end"}
  {"type": "error", "message": "..."}

The "done" event is the source of truth: its teacher text is post-processed
(bidi isolation, exercise layout) and its widget is the rendered canvas HTML.

Widget contract: the model writes a small JSON object
{"representation": "<key>", "params": {...}} chosen from the registry menu;
registry.build_widget() computes the geometry deterministically. The one
escape hatch is "freeform_svg" (raw steps, validated locally here). If the
widget is invalid, one cheap widget-only repair call is made (menu + error +
the teacher text already shown, no lesson content); if that fails too, an empty
widget is rendered and the failure is logged.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import threading
import traceback
import queue as _queue
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncGenerator, Optional

from langchain_core.messages import SystemMessage, HumanMessage

from llm             import generate_answer, _blocking_response
from prompt_builder  import (
    detect_language,
    render_canvas_widget,
    isolate_inline_math,        # bidi isolation of inline math
    normalize_exercise_layout,  # exercise header / explicit $$ layout
)
from registry import build_widget, build_menu, UnknownRepresentation, InvalidParams, get_variant_carrying_representations
from stream_splitter import split_stream, WidgetChunk, TeacherChunk, SpeechChunk, DoneEvent
# TTS: a single ElevenLabs call per turn, on the complete [SPEECH] text (one
# request per sentence ran into 429 rate limits).
from tts import synthesize_with_timestamps
from tts_text import prepare_for_tts

# _blocking_response() is private to llm.py; it is imported directly because the
# widget-only repair call skips generate_answer()'s full prompt assembly. It
# takes a LangChain message list and walks MODEL_CHAIN internally.


_handler_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="stream_llm")


# ── SSE helpers ───────────────────────────────────────────────────────────────

def _sse(payload: dict) -> str:
    """Format a dict as a single SSE data line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── Widget JSON extraction + dispatch ─────────────────────────────────────────
# [WIDGET] is a single JSON object {"representation", "params"}. The model may
# wrap it in a ```json fence, a bare ``` fence, or no fence: accept all three.

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```")

# Canvas size targeted by the layout functions in registry/representations.py
# (keep in sync with its W/H constants). Currently unused here.
_CANVAS_W, _CANVAS_H = 480, 300


# ── LaTeX -> Unicode for widget params ────────────────────────────────────────
# The canvas draws text as plain text (no LaTeX engine), but the model sometimes
# writes LaTeX in params (e.g. venn "notation_lines": "A \\cap B"). Convert the
# common commands to Unicode symbols. Applied to every string param except
# freeform_svg (raw SVG/steps).
_LATEX_SYMBOLS = [
    (r"\\not\s*\\subseteq", "⊈"), (r"\\not\s*\\subset", "⊄"),
    (r"\\not\s*\\supseteq", "⊉"), (r"\\not\s*\\supset", "⊅"),
    (r"\\not\s*=", "≠"), (r"\\not\s*\\in", "∉"),
    (r"\\subseteq", "⊆"), (r"\\supseteq", "⊇"),
    (r"\\subset", "⊂"), (r"\\supset", "⊃"),
    (r"\\cap", "∩"), (r"\\cup", "∪"), (r"\\notin", "∉"), (r"\\in", "∈"),
    (r"\\emptyset", "∅"), (r"\\varnothing", "∅"),
    (r"\\times", "×"), (r"\\div", "÷"), (r"\\cdot", "·"),
    (r"\\leq?", "≤"), (r"\\geq?", "≥"), (r"\\neq", "≠"), (r"\\approx", "≈"),
    (r"\\angle", "∠"), (r"\\perp", "⊥"), (r"\\parallel", "∥"),
    (r"\\Leftrightarrow", "⇔"), (r"\\Rightarrow", "⇒"), (r"\\rightarrow", "→"),
    (r"\\degree", "°"), (r"\^\s*\{?\\circ\}?", "°"),
    (r"\\mathbb\{N\}", "ℕ"), (r"\\mathbb\{Z\}", "ℤ"),
    (r"\\mathbb\{Q\}", "ℚ"), (r"\\mathbb\{R\}", "ℝ"),
    (r"\\\{", "{"), (r"\\\}", "}"),
]
_LATEX_SYMBOLS_RE = [(re.compile(p + r"(?![A-Za-z])" if p[-1].isalpha() else p), r)
                     for p, r in _LATEX_SYMBOLS]
_SUB = str.maketrans("0123456789+-n", "₀₁₂₃₄₅₆₇₈₉₊₋ₙ")
_SUP = str.maketrans("0123456789+-n", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ⁿ")


def _latex_to_unicode(text: str) -> str:
    if "\\" not in text and "_{" not in text and "^{" not in text:
        return text
    out = text
    out = re.sub(r"\\(?:text|mathrm|mathit|operatorname)\{([^{}]*)\}", r"\1", out)
    for rx, rep in _LATEX_SYMBOLS_RE:
        out = rx.sub(rep, out)
    out = re.sub(r"\\sqrt\{([^{}]*)\}", r"√\1", out)
    out = re.sub(r"_\{([0-9+\-n]+)\}|_([0-9n])",
                 lambda m: (m.group(1) or m.group(2)).translate(_SUB), out)
    out = re.sub(r"\^\{([0-9+\-n]+)\}|\^([0-9n])",
                 lambda m: (m.group(1) or m.group(2)).translate(_SUP), out)
    out = re.sub(r"\\[;,!: ]", " ", out)
    return out


def _latex_clean_params(value):
    if isinstance(value, str):
        return _latex_to_unicode(value)
    if isinstance(value, list):
        return [_latex_clean_params(v) for v in value]
    if isinstance(value, dict):
        return {k: _latex_clean_params(v) for k, v in value.items()}
    return value


def _extract_widget_json_text(widget_text: str) -> str:
    if not widget_text:
        return widget_text
    m = _JSON_FENCE_RE.search(widget_text)
    if m:
        return m.group(1).strip()
    return widget_text.strip()


def _angle_validation_error(op, ops, i, j, angle_tol_deg: float = 12.0,
                             vertex_tol: float = 6.0, vertex_near: float = 20.0) -> str:
    """
    Cross-check one "arc" op against the "line" ops of the same step.

    Catches an arc whose centre (cx, cy) is not on the vertex of the rays it is
    meant to sit between, or whose startAngle/endAngle point at no ray actually
    drawn from that vertex. Such an arc renders as a valid-looking but wrong
    shape. Deliberately shallow: it does not check the arc's magnitude. Returns
    "" on success, an error string otherwise.
    """
    cx, cy = op.get("cx"), op.get("cy")
    a0, a1 = op.get("startAngle"), op.get("endAngle")
    if not isinstance(a0, (int, float)) or not isinstance(a1, (int, float)):
        return f"step ? op {j}: 'startAngle'/'endAngle' missing or not numeric"
    if not (math.isfinite(a0) and math.isfinite(a1)):
        return f"step ? op {j}: 'startAngle'/'endAngle' must be finite radians"

    # Direction (radians, same cx + r*cos / cy + r*sin convention as the canvas
    # engine) of every line starting at this arc's vertex, plus the distance
    # from each line start to the vertex.
    ray_angles = []
    nearest_line_dist = None
    for other in ops:
        if not isinstance(other, dict) or other.get("type") != "line":
            continue
        frm = other.get("from")
        if not (isinstance(frm, (list, tuple)) and len(frm) == 2):
            continue
        fx, fy = frm
        if not (isinstance(fx, (int, float)) and isinstance(fy, (int, float))):
            continue
        dist = math.hypot(fx - cx, fy - cy)
        if nearest_line_dist is None or dist < nearest_line_dist:
            nearest_line_dist = dist
        if dist <= vertex_tol:
            to = other.get("to")
            if isinstance(to, (list, tuple)) and len(to) == 2:
                tx, ty = to
                if isinstance(tx, (int, float)) and isinstance(ty, (int, float)):
                    ray_angles.append(math.atan2(ty - fy, tx - fx))

    if not ray_angles:
        # No line starts within vertex_tol of (cx, cy):
        #  - nearest line far away or none: a decorative arc, do not flag it;
        #  - nearest line fairly close (<= vertex_near): a line was meant to
        #    start here and the coordinates are slightly off, so flag it.
        if nearest_line_dist is not None and nearest_line_dist <= vertex_near:
            return (f"step ? op {j}: arc center ({cx},{cy}) is "
                    f"{nearest_line_dist:.1f}px from the nearest ray's start "
                    f"point — looks like a vertex mismatch, not a deliberate "
                    f"detached arc")
        return ""

    def _closest_ray_diff(angle):
        best = min(
            abs(math.atan2(math.sin(angle - r), math.cos(angle - r)))
            for r in ray_angles
        )
        return math.degrees(best)

    tol = angle_tol_deg
    for a in (a0, a1):
        if _closest_ray_diff(a) > tol:
            return (f"step ? op {j}: arc angle {a:.4f} rad doesn't match any "
                    f"ray drawn from vertex ({cx},{cy}) within {tol}° — arc is "
                    f"likely detached from the angle it's meant to show")
    return ""


def _validate_freeform_steps(steps, margin: int = 5) -> str:
    """
    Minimal safety net for the rare "freeform_svg" escape hatch (every other
    representation is validated by its own layout function in registry/).
    Deliberately shallow; if a pattern fires often it belongs in the registry.

    Checks only what the client cannot degrade from gracefully:
      - structure: each op is a dict with a known "type" (line, arc, dot, text)
        and numeric [x, y] coordinates (the client silently drops a bad op);
      - arcs detached from their vertex or rays (_angle_validation_error()).

    There is no bounds check on purpose: the SVG viewBox clips out-of-range ops,
    and rejecting them sent valid diagrams to a repair call that could swap in an
    unrelated representation. `margin` is unused, kept for compatibility.

    Returns "" on success, an error string on failure.
    """
    if not isinstance(steps, list) or not steps:
        return "'steps' must be a non-empty JSON array"

    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return f"step {i} is not a JSON object"
        ops = step.get("ops", [])
        if not isinstance(ops, list):
            return f"step {i} 'ops' must be a JSON array"
        for j, op in enumerate(ops):
            if not isinstance(op, dict):
                return f"step {i} op {j} is not a JSON object"
            t = op.get("type")
            if t not in ("line", "arc", "dot", "text"):
                return f"step {i} op {j}: unknown type {t!r}"

            coords = []
            if t == "line":
                coords = [op.get("from"), op.get("to")]
            elif t in ("dot", "text"):
                coords = [op.get("at")]
            elif t == "arc":
                coords = [[op.get("cx"), op.get("cy")]]

            for c in coords:
                if not (isinstance(c, (list, tuple)) and len(c) == 2
                        and all(isinstance(n, (int, float)) for n in c)):
                    return f"step {i} op {j}: missing/invalid coordinates"
                # no bounds check (see docstring)

            if t == "arc":
                err = _angle_validation_error(op, ops, i, j)
                if err:
                    return err.replace("step ?", f"step {i}", 1)
    return ""


def _parse_and_build_widget(widget_text: str) -> tuple[Optional[list], str, Optional[str]]:
    """
    Parse the model's {"representation", "params"} choice and build the steps.

    Returns (steps, "", representation) on success, or
    (None, error_message, representation_or_None) on failure. `steps` has the
    [{"label", "ops"}] shape expected by render_canvas_widget(). The
    representation is also returned so "done" can send it to the frontend, which
    stores it in `history` (see extract_used_representations()). On failure it is
    None only when the JSON never named one (empty, invalid, or missing key).
    """
    raw = _extract_widget_json_text(widget_text)
    if not raw:
        return None, "empty [WIDGET] content", None

    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return None, f"invalid JSON ({exc})", None

    if not isinstance(parsed, dict) or "representation" not in parsed:
        return None, ('expected {"representation": "<key>", "params": {...}}, '
                       'got something else'), None

    representation = parsed.get("representation")
    params = parsed.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return None, "'params' must be a JSON object", representation

    # Convert model-written LaTeX in text params to Unicode (not for raw SVG).
    if representation != "freeform_svg":
        params = _latex_clean_params(params)

    # "none" means "no widget needed for this point": a valid, encouraged choice,
    # not a failure, so it must not trigger a repair retry.
    if representation == "none":
        return [], "", representation

    if representation == "freeform_svg":
        steps = params.get("steps")
        if steps is None and "ops" in params:
            steps = [{"label": "", "ops": params["ops"]}]
        err = _validate_freeform_steps(steps)
        if err:
            return None, err, representation
        return steps, "", representation

    try:
        steps = build_widget(representation, params)
        return steps, "", representation
    except UnknownRepresentation as exc:
        return None, f"unknown representation ({exc})", representation
    except InvalidParams as exc:
        return None, str(exc), representation
    except Exception as exc:
        # A layout function raised something other than its own declared
        # InvalidParams: treat it as an invalid widget (triggers the repair
        # retry) instead of taking down the whole turn.
        return None, f"layout function crashed ({exc})", representation


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
    question          : str,
    lesson            : dict,
    history           : list[dict],
    tc_injection      : str = "",
    mode              : str = "explanation",
    active_point_text : str = "",
    active_point_idx  : Optional[int] = None,
    used_representations: Optional[list[str]] = None,
) -> asyncio.Queue[str | None]:
    """
    Fallback used only when handle_stream() gets no pre-generated token_stream:
    runs generate_answer() in a thread and pushes its tokens into a queue.
    `used_representations` is forwarded to generate_answer().
    """
    loop  = asyncio.get_event_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _produce():
        try:
            for token in generate_answer(
                question          = question,
                lesson            = lesson,
                stream            = True,
                history           = history,
                tc_injection      = tc_injection,
                mode              = mode,
                active_point_text = active_point_text,
                active_point_idx  = active_point_idx,
                used_representations = used_representations,
            ):
                loop.call_soon_threadsafe(queue.put_nowait, token)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, f"\n[LLM ERROR] {exc}")
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    loop.run_in_executor(_handler_executor, _produce)
    return queue


# ── Async bridge for split_stream (sync generator -> async) ───────────────────

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


# ── Widget validation + repair ──────────────────────────────────────────

# Widget repair: up to MAX_REPAIR_ATTEMPTS non-streaming retries when the widget
# JSON is malformed or names an unknown / mis-parameterised representation. A
# retry only asks for a corrected {"representation", "params"} object.
# Kept at 1: in the logs a second attempt never recovered a turn that the first
# had failed (the model switched representation and still got params wrong).
MAX_REPAIR_ATTEMPTS = 1

# Modes that are text-only by design: widget parsing/repair never runs for them.
#   - "summary" carries a raw mindmap ```interactive_svg``` block written by the
#     model, passed through unchanged;
#   - "exercise" / "exercise_answer" have an empty [WIDGET].
_TEXT_ONLY_MODES = ("exercise", "exercise_answer", "summary")

# Subset of _TEXT_ONLY_MODES that must never carry widget content.
# (summary is excluded: it keeps its mindmap widget.)
_NO_WIDGET_MODES = ("exercise", "exercise_answer")


# ── Repair metrics ──────────────────────────────────────────────────────────
# One structured JSON log line per decision point, prefixed with a grep-able tag:
#   grep '\[repair_metric\]' server.log | sed 's/.*\[repair_metric\] //' | jq -s '...'
# Not routed through LangSmith: the repair call runs in an executor thread, and
# contextvars do not cross that boundary, so it would appear as a disconnected
# root trace.
def _repair_metric(event: dict) -> None:
    print(f"[repair_metric] {json.dumps(event, ensure_ascii=False)}")


def extract_used_representations(history: list[dict]) -> list[str]:
    """
    Return the de-duplicated, order-preserved list of representation keys used
    by past assistant turns in `history`.

    The frontend stores the `representation` of each assistant message and
    resends it in `history`; prompt_builder uses this list to build a concrete
    "already used: X, Y" note, so the model is not left to guess it from text
    that never contained it. Also used by the API for the initial generation.
    """
    used: list[str] = []
    for msg in history or []:
        role = msg.get("role")
        rep  = msg.get("representation")
        # "none" means "no widget was needed", not "a representation was used":
        # tracking it would discourage choosing "none" again next turn.
        if role in ("assistant", "model", "ai") and rep and rep != "none":
            used.append(rep)
    return list(dict.fromkeys(used))  # dedupe, preserve order


def _build_widget_repair_reminder(lang: str, error: str, original_teacher: str = "") -> str:
    """
    Corrective prompt for the repair retry: the previous [WIDGET] was invalid,
    resend a single {"representation", "params"} JSON object.

    When `original_teacher` is non-empty it pins the retry to the exact
    example and numbers already shown to the student in [TEACHER], so the repair
    cannot drift to a different example.
    """
    preserve = ""
    if original_teacher.strip():
        if lang == "ar":
            preserve = (
                "\n🔴🔴 التلميذ قرأ بالفعل هذا الشرح — يجب أن يطابق [WIDGET] "
                f"نفس الأرقام والمثال الوارد فيه بالضبط، فقط اختر representation/"
                f"params مناسبين لهذا المثال تحديداً:\n---\n{original_teacher.strip()}\n---\n"
            )
        else:
            preserve = (
                "\n🔴🔴 L'élève a déjà lu cette explication — [WIDGET] doit "
                "correspondre exactement aux mêmes chiffres/exemple, choisis "
                f"juste une representation/params adaptés à cet exemple précis:"
                f"\n---\n{original_teacher.strip()}\n---\n"
            )

    if lang == "ar":
        return (
            f"\n[تنبيه: [WIDGET] السابق غير صالح — الخطأ: {error}\n"
            "أعد إرسال [WIDGET] ككائن JSON واحد فقط بالضبط بهذه الصيغة:\n"
            '{"representation": "<مفتاح من القائمة أعلاه>", "params": {...}}\n'
            f"بلا أي إحداثيات يدوية، وبلا أي نص آخر خارج هذا الكائن.{preserve}]"
        )
    return (
        f"\n[Rappel: le [WIDGET] précédent était invalide — erreur: {error}\n"
        "Renvoie [WIDGET] comme UN SEUL objet JSON, exactement sous cette forme:\n"
        '{"representation": "<clé du menu ci-dessus>", "params": {...}}\n'
        f"sans aucune coordonnée manuelle, et sans aucun autre texte en dehors "
        f"de cet objet.{preserve}]"
    )


def _build_widget_only_system(
    lang: str, err: str, original_teacher: str,
    used_representations: Optional[list[str]] = None,
    subject: str = "",
) -> str:
    """
    System prompt for a widget-only repair call: just the representation menu
    plus the repair reminder, with no lesson content, history or [TEACHER].
    The menu is lesson-independent, so lesson text is not needed to pick one.

    `used_representations` carries the same "already used" note as the main
    prompt, so a repair on a confusion follow-up still avoids re-picking an old
    representation. `subject` is forwarded to build_menu() so the repair menu is
    subject-gated like the main one.
    """
    menu = build_menu(lang, subject)
    reminder = _build_widget_repair_reminder(lang, err, original_teacher=original_teacher)
    # Variant-carrying representations (angle_pair, mirror_symmetry, ...) are
    # meant to be reused once per variant, so they are exempt from the ban
    # (same rule as prompt_builder._build_visual_instructions()).
    _VARIANT_CARRYING_REPRESENTATIONS = get_variant_carrying_representations()
    used = [
        r for r in (used_representations or [])
        if r and r not in _VARIANT_CARRYING_REPRESENTATIONS
    ]
    if used:
        used_list = ", ".join(dict.fromkeys(used))
        already_used = (
            f"\n🔴🔴 تم استعمال representation التالية بالفعل في هذه المحادثة: "
            f"{used_list} — لا تعد استعمال أي منها.\n"
            if lang == "ar" else
            f"\n🔴🔴 Déjà utilisées dans cette conversation: {used_list} — "
            f"n'en réutilise aucune.\n"
        )
    else:
        already_used = ""
    if lang == "ar":
        return (
            "أنت نظام يُصلح فقط كائن JSON الخاص بـ [WIDGET]. لا تكتب أي نص "
            "آخر، ولا أي شرح، ولا [TEACHER].\n"
            f"{already_used}\n"
            f"القائمة المتاحة (اختر واحدة فقط):\n{menu}\n\n"
            "📦 أعد فقط كائن JSON واحد بالضبط بهذه الصيغة، بلا أي نص قبله أو "
            'بعده:\n{"representation": "<المفتاح>", "params": {...}}\n'
            f"{reminder}"
        )
    return (
        "Tu es un système qui corrige UNIQUEMENT l'objet JSON [WIDGET]. "
        "N'écris aucun autre texte, aucune explication, aucun [TEACHER].\n"
        f"{already_used}\n"
        f"Menu disponible (choisis-en UNE):\n{menu}\n\n"
        "📦 Renvoie SEULEMENT un objet JSON, exactement sous cette forme, "
        'sans aucun texte avant ou après:\n{"representation": "<clé>", '
        f'"params": {{...}}}}\n{reminder}'
    )


def _call_widget_only_repair(
    question: str, lang: str, err: str, original_teacher: str,
    used_representations: Optional[list[str]] = None,
    subject: str = "",
) -> str:
    """
    The actual repair call: a single non-streaming llm._blocking_response() (so
    MODEL_CHAIN fallback/retry still applies) with a short system prompt (menu +
    reminder, original_teacher pinned) and no [TEACHER] generation.
    `used_representations` and `subject` are forwarded to the system prompt.
    """
    system = _build_widget_only_system(
        lang, err, original_teacher,
        used_representations=used_representations, subject=subject,
    )
    messages = [SystemMessage(content=system), HumanMessage(content=question)]
    return _blocking_response(messages)


async def _repair_widget_if_needed(
    widget_text       : str,
    question          : str,
    lesson            : dict,
    history           : list[dict],
    tc_injection      : str,
    mode              : str = "explanation",
    original_teacher  : str = "",
    active_point_text : str = "",
    active_point_idx  : Optional[int] = None,
    used_representations: Optional[list[str]] = None,
) -> tuple[str, Optional[str]]:
    """
    Validate and render the model's widget, repairing it if needed.

    Text-only modes (_TEXT_ONLY_MODES) return (widget_text, None) unchanged.
    For every other mode, the {"representation", "params"} choice is built
    through registry.build_widget() (or the local freeform_svg validator) and
    rendered with render_canvas_widget(). If invalid, up to MAX_REPAIR_ATTEMPTS
    widget-only repair calls are made, pinned to the example already shown in
    [TEACHER]. If all fail, an empty widget is rendered (never raw JSON) and the
    failure is logged with lesson number and mode: repeated hits point at a
    representation missing from registry/.

    Returns (rendered_widget_html, representation_key_or_None); the key goes
    into the "done" event so the frontend can resend it in `history`.
    `active_point_text` / `active_point_idx` are accepted for call-site
    compatibility only; the repair call does not use them.
    """
    if mode in _TEXT_ONLY_MODES:
        return widget_text, None

    # Language is locked to Arabic, as in llm.py (collège 7ème is Arabic-medium);
    # detecting it from the question could build the repair prompt in another
    # language than the [TEACHER] text already shown.
    lang = "ar"

    lesson_number = (lesson.get("lesson_number") or "?") if isinstance(lesson, dict) else "?"
    # Subject is forwarded so the repair menu is gated like the main prompt.
    subject = (lesson.get("subject") or "") if isinstance(lesson, dict) else ""

    steps, err, representation = _parse_and_build_widget(widget_text)
    if steps is not None:
        _repair_metric({
            "event": "ok_no_repair", "lesson_number": lesson_number, "mode": mode,
            "representation": representation,
        })
        return render_canvas_widget(steps), representation

    _repair_metric({
        "event": "invalid_first_attempt", "lesson_number": lesson_number, "mode": mode,
        "attempted_representation": representation, "error": err,
    })

    for attempt in range(1, MAX_REPAIR_ATTEMPTS + 1):
        print(f"[stream_handler] ⚠️ widget invalid ({err}) — "
              f"repair attempt {attempt}/{MAX_REPAIR_ATTEMPTS} (widget-only call)")

        try:
            # Widget-only, non-streaming: no lesson / history / [TEACHER].
            loop = asyncio.get_event_loop()
            raw_widget = await loop.run_in_executor(
                _handler_executor,
                _call_widget_only_repair,
                question, lang, err, original_teacher, used_representations, subject,
            )

            steps, err, representation = _parse_and_build_widget(raw_widget)
            if steps is not None:
                print(f"[stream_handler] ✅ repair attempt {attempt} produced a valid widget")
                _repair_metric({
                    "event": "repair_ok", "lesson_number": lesson_number, "mode": mode,
                    "attempt": attempt, "representation": representation,
                })
                return render_canvas_widget(steps), representation

            print(f"[stream_handler] ⚠️ repair attempt {attempt} still invalid ({err})")
            _repair_metric({
                "event": "repair_still_invalid", "lesson_number": lesson_number, "mode": mode,
                "attempt": attempt, "attempted_representation": representation, "error": err,
            })
        except Exception as exc:
            print(f"[stream_handler] ⚠️ repair attempt {attempt} failed")
            traceback.print_exc()
            _repair_metric({
                "event": "repair_call_crashed", "lesson_number": lesson_number, "mode": mode,
                "attempt": attempt, "error": str(exc),
            })

    print(f"[stream_handler] ⚠️ all {MAX_REPAIR_ATTEMPTS} repair attempts exhausted "
          f"(lesson={lesson_number}, mode={mode}) — rendering empty fallback widget. "
          f"Log this: repeated fallbacks for the same lesson/point are the signal "
          f"to add a dedicated representation to registry/.")
    _repair_metric({
        "event": "repair_exhausted", "lesson_number": lesson_number, "mode": mode,
        "last_error": err,
    })
    return render_canvas_widget([]), None


# ── Main entry point ──────────────────────────────────────────────────────────

async def handle_stream(
    question          : str,
    lesson            : dict,
    history           : list[dict] | None = None,
    tc_injection      : str = "",
    token_stream      : Optional[AsyncGenerator[str, None]] = None,
    mode              : str = "explanation",
    active_point_text : str = "",
    active_point_idx  : Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    Split the token stream and yield SSE-formatted strings for FastAPI's
    StreamingResponse.

    token_stream: tokens produced by the caller (the API's _handle_turn()), so
      everything nests under one LangSmith parent. If None, tokens come from
      _iter_llm_stream(), which calls generate_answer() itself.
    mode: teaching_controller mode (explanation / continuation / exercise /
      exercise_answer / summary). Exercise modes never carry widget content.
    active_point_text / active_point_idx: context of the active lesson point,
      forwarded to the fallback generation call.

    `used_representations` is derived from `history` and passed to the fallback
    generation and the widget repair. The representation chosen this turn is
    returned in the "done" event so the frontend can send it back next turn.
    """
    history = history or []
    used_representations = extract_used_representations(history)

    # [SPEECH] arrives in a few fragments: they are buffered here and sent to TTS
    # once, at DoneEvent.
    speech_text_parts: list[str] = []

    if token_stream is not None:
        token_queue = await _stream_to_queue(token_stream)
    else:
        token_queue = await _iter_llm_stream(
            question, lesson, history, tc_injection,
            mode=mode, active_point_text=active_point_text,
            active_point_idx=active_point_idx,
            used_representations=used_representations,
        )

    try:
        async for event in _async_split(token_queue):

            if isinstance(event, WidgetChunk):
                # Raw widget-JSON fragments: a progress signal only. The
                # frontend renders from the "done" event's widget field.
                yield _sse({"type": "widget_chunk", "text": event.text})

            elif isinstance(event, TeacherChunk):
                # Forward teacher text live so the bubble fills while the model
                # generates. This is raw text: isolate_inline_math() and
                # normalize_exercise_layout() run once, on the full text, in
                # the DoneEvent branch, and the "done" teacher field is the
                # source of truth. [TEACHER] is on-screen text only; it is
                # never sent to TTS.
                if event.text:
                    yield _sse({"type": "teacher_chunk", "text": event.text})

            elif isinstance(event, SpeechChunk):
                # [SPEECH] arrives before [TEACHER]. It is audio-only: buffered
                # for the single TTS call in the DoneEvent branch and never
                # sent to the client as text.
                if event.text:
                    speech_text_parts.append(event.text)

            elif isinstance(event, DoneEvent):
                widget_text, representation = await _repair_widget_if_needed(
                    widget_text       = event.widget,
                    question          = question,
                    lesson            = lesson,
                    history           = history,
                    tc_injection      = tc_injection,
                    mode              = mode,
                    original_teacher  = event.teacher,
                    active_point_text = active_point_text,
                    active_point_idx  = active_point_idx,
                    used_representations = used_representations,
                )

                # exercise / exercise_answer must never carry widget content:
                # the frontend derives hasWidget from widget.trim() alone, so
                # leftovers would flip the message to the wrong bubble style.
                # If the teacher section is empty but the widget is not,
                # recover the widget text as teacher text (fail-open).
                teacher_text = event.teacher
                if mode in _NO_WIDGET_MODES:
                    if not teacher_text.strip() and widget_text.strip():
                        print("[stream_handler] ⚠️ exercise-mode turn had "
                              "content in [WIDGET] and nothing in [TEACHER] — "
                              "recovering it as teacher text instead of "
                              "dropping it")
                        teacher_text = widget_text
                    widget_text = ""

                # Post-processing is formatting only: an exception in either step
                # must never lose the response, so each is guarded and falls
                # back to the last good text.


                try:
                    teacher_text = isolate_inline_math(teacher_text)
                except Exception:
                    print("[stream_handler] ⚠️ isolate_inline_math() failed — "
                          "shipping unisolated teacher text instead of erroring out")
                    traceback.print_exc()

                try:
                    teacher_text = normalize_exercise_layout(teacher_text)
                except Exception:
                    print("[stream_handler] ⚠️ normalize_exercise_layout() failed — "
                          "shipping text without layout normalization instead of "
                          "erroring out")
                    traceback.print_exc()

                # ── TTS: one ElevenLabs call for the whole turn ──────
                # Wrapped so a TTS failure never blocks the "done" event, which
                # is the source of truth for widget / teacher text.
                try:
                    # Prefer the accumulated [SPEECH]; if the model sent none,
                    # fall back to the [TEACHER] text so the turn is not silent.
                    speech_text = "".join(speech_text_parts).strip()
                    source_text = speech_text or event.teacher

                    audio_result = None
                    if source_text.strip():
                        lang = detect_language(source_text)
                        tts_text = prepare_for_tts(source_text, lang=lang)
                        if tts_text:
                            audio_result = await synthesize_with_timestamps(
                                tts_text, lang=lang,
                            )

                    if audio_result:
                        yield _sse({
                            "type"         : "audio_chunk",
                            "index"        : 0,
                            "text"         : source_text,
                            "audio_base64" : audio_result["audio_base64"],
                            "alignment"    : audio_result.get("alignment"),
                        })
                    yield _sse({
                        "type" : "audio_done",
                        "count": 1 if audio_result else 0,
                    })
                except Exception:
                    print("[stream_handler] ⚠️ TTS synthesis failed — "
                          "shipping text/widget with no audio instead of "
                          "erroring out")
                    traceback.print_exc()

                yield _sse({
                    "type"          : "done",
                    "widget"        : widget_text,
                    "teacher"       : teacher_text,
                    "representation": representation,
                })

    except Exception:
        traceback.print_exc()
        yield _sse({"type": "error", "message": "Internal server error"})
        return

    yield _sse({"type": "stream_end"})