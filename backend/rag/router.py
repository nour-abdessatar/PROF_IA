"""
router.py — intent classification, one fast Gemini call before retrieval.

classify() returns a RouterOutput:
  - intent / action / mode: what the student wants (lesson, exercise,
    exercise_answer, summary, follow-up, ...) and what the caller should do;
  - extra_modes: other asks in the same message (e.g. "exercise" + "summary");
    "exercise_answer" is always solo;
  - continues_current_lesson: whether the message still concerns the lesson
    already in progress (given via current_lesson_title);
  - in_curriculum: False when the student asks about a concept that is not in
    the loaded curriculum. classify() then forces intent="out_of_program",
    action="direct_llm", mode="conversational"; callers must not retrieve or
    build a widget, and should reply with out_of_program_reply().

The curriculum catalog sent to the router is built once at startup with
configure_curriculum(). Every call goes to the LLM (no keyword pre-checks); on
repeated failure classify() returns _FALLBACK (follow_up / use_memory /
continuation).
"""
from __future__ import annotations
import json, re, time, traceback
from dataclasses import dataclass, field
from typing import Literal, Optional, List, Dict
from google import genai
from google.genai import types
from langsmith import traceable

ROUTER_MODEL  = "gemini-2.5-flash-lite"
ROUTER_TOKENS = 512

# Retries before falling back to _FALLBACK. The call is cheap (512 tokens,
# thinking off), so a couple of retries cover transient empty/blocked answers.
ROUTER_MAX_RETRIES = 2


@dataclass
class RouterOutput:
    intent       : Literal["action", "lesson", "follow_up", "exercise", "exercise_answer", "summary", "out_of_program", "unknown"]
    action       : Literal["direct_llm", "retrieve", "retrieve_if_no_lesson", "use_memory"]
    mode         : Literal["explanation", "continuation", "exercise", "exercise_answer", "summary", "conversational"]
    topic        : Optional[str]
    lesson_number: Optional[str]
    difficulty   : Optional[Literal["easy", "medium", "hard"]] = None
    # Topic-continuity verdict, only meaningful when a lesson is already active:
    #   True  -> still about the lesson in progress (even if phrased generically,
    #            e.g. "give me an exercise"): the caller should reuse the session.
    #   False -> the student names something else: retrieve the new lesson.
    #   None  -> no current lesson was given (cold start): not applicable.
    continues_current_lesson: Optional[bool] = None
    # Other modes present alongside `mode` (e.g. "exercise" + ["summary"]).
    # Empty on a single-mode turn; never contains "exercise_answer". `mode`
    # alone drives retrieval/session reuse; widget ownership
    # (prompt_builder.resolve_widget_owner) and the extra TEACHER addenda
    # (teaching_controller.get_extra_mode_addendum) read this list.
    extra_modes: List[str] = field(default_factory=list)
    # False -> the student asked about a concept that is not in the curriculum.
    in_curriculum: Optional[bool] = None


# ── curriculum catalog (built from the lessons JSON) ─────────────
_CURRICULUM_CATALOG = ""


def build_curriculum_catalog(lessons: List[dict]) -> str:
    """One compact line per lesson: number, title, subject, main topics."""
    lines = []
    for l in lessons:
        # Keep the (truncated) full text of each point, not just its heading, so
        # abbreviations and sub-terms after the ":" (ق.م.أ, pgcd, ...) are visible.
        pts = []
        for item in l.get("content", []):
            t = " ".join(item.split())
            if t:
                pts.append(t[:160])
        lines.append(
            f"{l.get('lesson_number','?')}. {l.get('lesson_title','')} "
            f"[{l.get('subject','')}] — " + " | ".join(pts)
        )
    return "\n".join(lines)


def configure_curriculum(source) -> None:
    """Call once at startup with the lessons JSON path OR the parsed list."""
    global _CURRICULUM_CATALOG
    lessons = source
    if isinstance(source, str):
        with open(source, encoding="utf-8") as f:
            lessons = json.load(f)
    _CURRICULUM_CATALOG = build_curriculum_catalog(lessons)
    print(f"[router] curriculum catalog loaded ({len(lessons)} lessons)")


OUT_OF_PROGRAM_MESSAGE_AR = (
    "{topic} ليس ضمن البرنامج الحالي، وستدرسه لاحقاً. "
    "يمكنني الآن مساعدتك في دروس البرنامج الحالي — أي درس تريد أن نبدأ به؟"
)
OUT_OF_PROGRAM_MESSAGE_FR = (
    "{topic} ne fait pas partie du programme actuel, tu le verras plus tard. "
    "Je peux t'aider dès maintenant avec les leçons du programme — par laquelle veux-tu commencer ?"
)


def out_of_program_reply(topic: Optional[str] = None, lang: str = "ar") -> str:
    """Fixed reply for route.in_curriculum is False (no LLM, no widget)."""
    if lang == "fr":
        t = (topic or "").strip() or "Ce sujet"
        return OUT_OF_PROGRAM_MESSAGE_FR.format(topic=t)
    t = (topic or "").strip() or "هذا الموضوع"
    return OUT_OF_PROGRAM_MESSAGE_AR.format(topic=t)


_ROUTER_SYSTEM = """
You are a routing classifier for a Tunisian middle-school math assistant.
The assistant teaches in Arabic or French.
The LLM already knows the math — the retriever only fetches *which lesson* to teach.

Given the student message and the last 2 conversation turns, return ONLY the raw JSON object.
No backticks, no markdown, no explanation, no preamble.
Start your response with { and end with }.

You may also be given a line "CURRENT LESSON IN PROGRESS: <title>" before the
conversation turns. This tells you which lesson the student is already
partway through, in this same session, right now. Use it ONLY to fill
"continues_current_lesson" (see below) — it does not change any of the
intent/action/mode rules elsewhere in this prompt.

JSON schema:
{
  "intent":        "action" | "lesson" | "follow_up" | "exercise" | "exercise_answer" | "summary" | "unknown",
  "action":        "direct_llm" | "retrieve" | "retrieve_if_no_lesson" | "use_memory",
  "mode":          "explanation" | "continuation" | "exercise" | "exercise_answer" | "summary" | "conversational",
  "topic":         "<subject string>" | null,
  "lesson_number": "<digit string e.g. '2'>" | null,
  "difficulty":    "easy" | "medium" | "hard" | null,
  "continues_current_lesson": true | false | null,
  "in_curriculum": true | false | null,
  "extra_modes":   ["explanation" | "continuation" | "exercise" | "summary", ...]
}

EXTRA_MODES — combined requests:
  A single message can ask for more than one thing at once, e.g.
  "أعطني تمرين ولخصلي الدرس" (give me an exercise AND summarize the
  lesson for me) or "فسرلي هذه النقطة واعطيني تمرين عليها" (explain this
  point AND give me an exercise on it).
  - `mode` stays whichever ask is grammatically PRIMARY (usually the
    first one, or the one the sentence is actually built around).
  - `extra_modes` lists every OTHER distinct ask in the same message, as
    a plain array of mode strings. Empty array `[]` when there is only
    one ask (the common case — most messages).
  - Only combine when the asks are genuinely distinct requests with no
    shared retrieval/grading dependency — e.g. "explain the lesson"
    (explanation) + "give me an exercise" (exercise) are independent
    asks. Do NOT invent a combo out of a single request just because it
    touches two ideas (e.g. "explain the difference between X and Y" is
    still ONE explanation request, extra_modes=[]).
  - 🔴 "exercise_answer" may NEVER appear in `extra_modes`, and may
    NEVER coexist with anything else at all — neither as `mode` alongside
    a non-empty `extra_modes`, nor as an entry in `extra_modes` alongside
    any `mode`. It depends on reading the previous assistant turn to know
    whether the student is answering an OLD exercise — a combo turn would
    make that ambiguous (answering the old one, or the new one just given
    in the same breath?). If a message looks like both an exercise
    answer/correction request AND something else, classify it as plain
    EXERCISE ANSWER with extra_modes=[] and ignore the "something else".

CONTINUES_CURRENT_LESSON — fill this whenever a "CURRENT LESSON IN PROGRESS"
line was given to you above; otherwise leave it null.
  - true  -> the student's message is still about that same lesson, even if
    it names no topic at all (e.g. a bare practice request like "أريد
    تمريناً" / "give me an exercise", a short confirmation, or a question
    that only makes sense as a continuation of what's already being taught).
    A message can genuinely name nothing new and still obviously continue
    the current lesson — don't require topic words to overlap; read intent.
  - false -> the student is clearly asking about a DIFFERENT concept/lesson
    than the one in progress (e.g. the current lesson is "الزوايا" and the
    student asks for exercises on "الكسور", or names a different lesson
    number, or a different math concept entirely).
  - Default to true when genuinely unsure and the message names no
    competing topic — a wrong "stays on lesson" costs one re-explanation at
    worst, a wrong "switches lesson" silently discards the student's
    progress, which is worse.

OUT OF PROGRAM — CHECK THIS FIRST (fill "in_curriculum"):
  The CURRICULUM list at the end of this prompt is the ONLY thing this
  assistant teaches. When the student asks to be taught / explained /
  drilled on a math concept (lesson request, topic question, exercise):
  - in_curriculum=true  -> the concept is covered by one of the listed lessons.
  - in_curriculum=false -> the concept is NOT covered by any listed lesson
    (e.g. fractions الكسور/الأعداد الكسرية, relative numbers/integers
    الأعداد النسبية, decimals, proportionality, equations... whenever they
    are not in the list). Then output: intent="out_of_program",
    action="direct_llm", mode="conversational", topic=<concept named>,
    lesson_number=null, extra_modes=[].
  - in_curriculum=null  -> no concept involved (greeting, confirmation,
    exercise answer, summary of current lesson, etc.).
  🔴 SPECIFIC POINTS COUNT: a question about ANY point, term, symbol or
  abbreviation written inside a lesson's text (not only its title) is
  in_curriculum=true — e.g. "المربعات الكاملة", "pgcd", "الباقي".
  Asymmetric cost: wrongly rejecting a real lesson question is MUCH worse
  than answering a borderline one. When in doubt -> true.
  🔴 SLIP vs. DIFFERENT CONCEPT: a wording slip is only when the term as
  written is NOT a real math concept and one listed concept is obviously
  meant — e.g. "القاسم المشترك الأصغر" (no such thing) = ق.م.أ → in_curriculum=true.
  If the term as written IS itself a real math concept (even from a later
  grade), it is a different concept: NEVER "correct" it into a
  similar-sounding listed lesson. "الأعداد النسبية" is NOT "الأعداد الأولية";
  "الأعداد الكسرية" is NOT "القسمة الإقليدية" → in_curriculum=false.
  When genuinely unsure whether a concept is covered, prefer false only if
  no listed lesson's title or topics mention it.

Classification rules:

LESSON REQUEST (intent=lesson, action=retrieve, mode=explanation):
  Trigger when student asks to learn/explain a lesson. Examples:
  - "فسرلي الدرس 1" / "فسرلي الدرس الأول" / "درس 2" / "الدرس الثالث"
  - "شرح الدرس" / "اشرح لي" / "علمني" / "فهمني"
  - "leçon 1" / "explique moi la leçon" / "cours sur les fractions"
  - ANY message containing "درس" + a number or ordinal
  - ANY message containing "leçon" or "cours" + a number
  → action=retrieve, mode=explanation

  TOPIC/CONCEPT QUESTION (also intent=lesson, action=retrieve, mode=explanation):
  Trigger when the student names a specific math concept, property, or rule
  as a bare question — with NO lesson-word and NO teach-me verb at all.
  This is a lesson request ONLY IF the concept is covered by the CURRICULUM
  listed at the end of this prompt (see OUT OF PROGRAM). Examples:
  - "ماهي خصائص القوة" / "ما هي خصائص القوة" / "ما هو العدد الأولي" /
    "شنية المربع الكامل" / "شنو التناسبية"
  - "c'est quoi une fraction" / "qu'est-ce que la distributivité" /
    "c'est quoi les nombres premiers"
  → action=retrieve, mode=explanation, topic=<the concept named>
  ⚠️ Do NOT downgrade this to ACTION just because it's phrased as a bare
  question instead of an explicit "explain/teach me" request — a math
  concept named by the student is never a generic/off-topic question
  (but it may be OUT OF PROGRAM — check that rule first).
  ⚠️ EXCEPTION — EXERCISE BEATS TOPIC/CONCEPT QUESTION: if the student
  names a math concept AND is also asking to practice/train/drill it
  (rather than have it explained), classify as EXERCISE instead, not as a
  TOPIC/CONCEPT QUESTION. Example: "تمرن في خصائص القوة" / "دربني في
  المعادلات" / "entraîne moi sur les fractions" name a concept but the verb
  is a practice request, not a teach-me request → intent=exercise,
  action=retrieve_if_no_lesson, mode=exercise, topic=<the concept named>.
  Only route to TOPIC/CONCEPT QUESTION (lesson/explanation) when the intent
  is to be TAUGHT the concept, not to be DRILLED on it.

FOLLOW UP (intent=follow_up, action=use_memory, mode=continuation):
  Trigger ONLY when student sends a SHORT confirmation with NO lesson reference.
  Examples:
  - Arabic: نعم, فهمت, تمام, أكيد, يلا, واضح, حسناً, ايه, اه
  - French: oui, ok, compris, j'ai compris, d'accord, allez, vas-y, ouais
  WARNING: If the message contains "درس" or "leçon" or "cours" alongside
  confirmation words, it is STILL a LESSON REQUEST, not a follow_up.

CONFUSION ABOUT THE CURRENT LESSON POINT (intent=follow_up, action=use_memory, mode=explanation):
  🔴 Trigger when BOTH are true:
  1. The student's message expresses non-understanding — "مافهمتش" /
     "ما فهمتش" / "لم أفهم" / "مش فاهم" / "ما فهمت" / "j'ai pas compris" /
     "je ne comprends pas" — possibly followed by a concept name.
  2. That concept (or the point currently being explained) ALREADY appears
     in the last 2 conversation turns provided to you — i.e. the assistant
     was just explaining it, not something the student is bringing up out
     of nowhere.
  → intent=follow_up, action=use_memory, mode=explanation, topic=null,
  lesson_number=null.
  This means: re-explain the SAME point differently (simpler wording or
  another example). Do NOT re-retrieve the lesson from scratch and do NOT
  advance to the next point — that is what mode=continuation is for
  (confirmed understanding), and this is the opposite case.
  🔴 PRIORITY: this beats TOPIC/CONCEPT QUESTION whenever condition 2
  holds. Only fall through to TOPIC/CONCEPT QUESTION (action=retrieve) if
  the named concept does NOT appear anywhere in the last 2 turns — i.e.
  it's a genuinely new topic the student is asking about, unrelated to
  what was just being taught.
  🔴 SUBORDINATE TO EXERCISE ANSWER: if the LAST assistant turn was an
  exercise (not an explanation) — see the EXERCISE ANSWER rule's condition
  1 below — classify as EXERCISE ANSWER instead, even if the student's
  message is phrased as non-understanding (e.g. "مافهمتش هذا التمرين").
  This rule only applies when the thing the student didn't understand was
  an EXPLANATION, not an exercise they're stuck on.
  This is NOT the same as FOLLOW UP above: FOLLOW UP is a short
  CONFIRMATION (نعم/فهمت/تمام...) with no lesson reference at all; this
  rule is the opposite statement (non-understanding) and always targets
  the point just explained, never a bare "yes".

EXERCISE (intent=exercise, action=retrieve_if_no_lesson, mode=exercise):
  Trigger when student asks for practice, in any of these forms:
  - Noun form: "تمرين" / "تمارين" / "مسألة" / "مسائل" / "exercice" / "exercices"
  - Verb/imperative form (practice-me / drill-me phrasing): "اتمرن" /
    "تمرن" / "دربني" / "درّبني" / "مرّني" / "مرني" / "تدرب" / "تدريب" /
    "entraîne moi" / "entraine moi" / "entraîne-moi" / "exerce moi"
  - "donne moi un exercice" / "أعطني تمرين"
  🔴 PRIORITY: EXERCISE always beats TOPIC/CONCEPT QUESTION when a practice
  word (either form above) is present alongside a named concept — see the
  exception under TOPIC/CONCEPT QUESTION above. Never route a practice
  request to plain lesson/explanation just because it also names a topic.
  ⚠️ EXCEPTION — EXERCISE ANSWER BEATS EXERCISE: if the message asks for a
  CORRECTION/SOLUTION of an exercise already given (e.g. contains "إصلاح" /
  "تصحيح" / "صحح" / "الحل" / "الإجابة" alongside "تمرين", or "correction" /
  "corrige" / "solution" alongside "exercice"), and the recent history
  shows a previous exercise was actually given, this is NOT a request for a
  brand-new exercise — see EXERCISE ANSWER below instead.

EXERCISE ANSWER (intent=exercise_answer, action=use_memory, mode=exercise_answer):
  🔴 Trigger when BOTH of these are true:
  1. In the recent history provided to you, the LAST assistant/model turn
     contains exercise content — numbered exercises, wording like "تمرين
     رقم" / "التمرين" / "exercice n°" / "exercice", or a list of
     calculation/practice questions.
  2. The student's CURRENT message looks like an attempt to answer that
     exercise, OR is asking for its correction/solution — e.g. numeric
     results ("3⁴=81"), short calculations, computed values, "الجواب
     هو..." / "la réponse est...", explicit correction requests like
     "اعطني إصلاح تمرين 2" / "صحح لي التمرين" / "donne-moi la correction
     de l'exercice 2" / "corrige l'exercice", or any direct reply that
     does NOT introduce a new topic, a new lesson request, or a request
     for a genuinely NEW/different exercise.
  This applies EVEN IF the student's message has no explicit "here is my
  answer" phrasing at all — bare numbers or computed results right after
  an exercise turn are enough, and so is a bare "give me the correction"
  request naming an exercise already given. Examples:
  - Previous assistant turn gave "التمرين رقم 1: احسب 3⁴, 5³, 2⁶"
    → student replies "3⁴=81 et 5³=125 et 2⁶=64"
    → intent=exercise_answer, action=use_memory, mode=exercise_answer
  - Previous assistant turn gave an equation exercise
    → student replies "x=5" or "x = 5"
    → intent=exercise_answer, action=use_memory, mode=exercise_answer
  - Previous assistant turn gave "التمرين رقم 2: ..."
    → student replies "اعطني إصلاح تمرين 2" (asking for the correction,
    not attempting it themselves)
    → intent=exercise_answer, action=use_memory, mode=exercise_answer
  🔴 PRIORITY: EXERCISE ANSWER beats LESSON REQUEST / TOPIC-CONCEPT
  QUESTION / FOLLOW UP / EXERCISE / CONFUSION ABOUT THE CURRENT LESSON
  POINT whenever condition 1 (previous turn was an exercise) holds and the
  student's message is plausibly an attempt at it, or a request for its
  correction, rather than a brand-new unrelated request. This includes a
  non-understanding message like "مافهمتش" directed at an exercise, not
  just numeric/attempt replies — see the CONFUSION rule above, which
  explicitly defers to this one in that case. Only classify as something
  else if the student's message clearly introduces a different, unrelated
  request (a new lesson keyword, a new topic never mentioned in the
  exercise, an explicit request for a DIFFERENT/additional exercise, etc.)
  instead of engaging with the given exercise.
  Do NOT trigger this rule if the previous assistant turn was NOT an
  exercise (no exercise wording/numbering) — a bare number or short reply
  with no preceding exercise context should fall through to the other
  rules normally (most likely FOLLOW UP or ACTION).

SUMMARY (intent=summary, action=use_memory, mode=summary):
  Trigger when student asks for recap. Examples:
  - "ملخص" / "لخص" / "résumé" / "récapitule"

ACTION (intent=action, action=direct_llm, mode=conversational):
  Greetings, thanks, or messages with NO math content at all — small talk
  or off-topic chat only.
  - "مرحبا" / "شكرا" / "bonjour" / "merci"
  ⚠️ Do NOT use ACTION for a message that names a specific math concept,
  property, or rule — even when phrased as a bare "what is" question with
  no lesson-word and no teach-me verb (see TOPIC/CONCEPT QUESTION above).
  That is always a LESSON REQUEST (or an EXERCISE, if it's a practice
  request — see the priority rule above), never ACTION.

UNKNOWN -> action=direct_llm, mode=conversational

RULES:
- "فسرلي الدرس X" ALWAYS -> intent=lesson, action=retrieve
- A bare "what is <math concept>" question (ماهي/ما هي/ما هو/شنو/شنية/
  c'est quoi/qu'est-ce que + a math term) is ALWAYS intent=lesson,
  action=retrieve — never intent=action, even without "درس"/"شرح"/"leçon" —
  (unless OUT OF PROGRAM, see above) UNLESS it's also a practice request, in which case EXERCISE wins (see
  priority rule above).
- If the previous assistant turn was an exercise and the student's current
  message is plausibly an attempt to answer it, OR is asking for its
  correction/solution (see EXERCISE ANSWER rule above), that ALWAYS wins
  over LESSON REQUEST / TOPIC-CONCEPT QUESTION / FOLLOW UP / EXERCISE,
  even if the message contains no explicit "answer" phrasing, and even if
  it contains the bare word "تمرين"/"exercice" (e.g. "إصلاح تمرين 2" is
  EXERCISE ANSWER, not a new EXERCISE request).
- lesson_number: extract the digit from "درس 2", "الدرس الثاني"->"2", "leçon 3"->"3"
- NEVER classify as follow_up if the message contains "درس", "leçon", or
  "cours" — EXCEPT for the CONFUSION ABOUT THE CURRENT LESSON POINT rule
  above, which is also intent=follow_up and is allowed to fire even if the
  student's non-understanding message happens to contain "درس" (e.g.
  "مافهمتش الدرس"), as long as its own two conditions are met. This
  exception does NOT extend to LESSON REQUEST's own trigger words
  (فسرلي/اشرح/علمني/etc.) — if the student uses one of those alongside
  non-understanding wording, treat it as a fresh LESSON REQUEST instead,
  since an explicit teach-me verb signals they want the retrieval path,
  not a same-point re-explanation.
- difficulty: extract from explicit difficulty words only — سهل/سهلة/facile->"easy",
  متوسط/متوسطة/moyen/moyenne->"medium", متقدم/متقدمة/صعب/صعبة/difficile/avancé/avancée->"hard".
  Leave null if the student didn't state a difficulty.

Arabic ordinals mapping:
الأول->1, الثاني->2, الثالث->3, الرابع->4, الخامس->5,
السادس->6, السابع->7, الثامن->8, التاسع->9, العاشر->10
"""

_JSON_RE = re.compile(r"\{.*\}", re.S)

_FALLBACK = RouterOutput(
    intent="follow_up", action="use_memory", mode="continuation",
    topic=None, lesson_number=None, difficulty=None,
    continues_current_lesson=None,
    extra_modes=[],
)


@traceable(name="router_llm_call", run_type="llm")
def _call_router_llm(
    client            : genai.Client,
    system_instruction: str,
    user_msg          : str,
) -> str:
    """
    Isolated so LangSmith logs the actual prompt sent to Gemini as this run's
    input. Raises on an empty/blank response so classify()'s retry loop has a
    signal to retry on.
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

    text = response.text or ""
    if not text.strip():
        raise ValueError(
            f"router LLM returned an empty/blank response "
            f"(finish_reason={finish_reason})"
        )
    return text


@traceable(name="router_classify", run_type="chain")
def classify(
    question             : str,
    history              : List[Dict[str, str]] | None = None,
    api_key              : str = "",
    current_lesson_title : str | None = None,
) -> RouterOutput:
    """
    Run the router and return a RouterOutput. Always calls the LLM and falls
    back to _FALLBACK on any failure (after ROUTER_MAX_RETRIES attempts).

    `current_lesson_title` is the lesson already active in this session (None on
    a cold start). It is injected into the LLM context so it can return an
    explicit `continues_current_lesson` verdict.
    """
    client = None
    try:
        client = genai.Client(api_key=api_key)
    except Exception as e:
        print(f"[router] failed to create genai.Client: {e} — using fallback")
        traceback.print_exc()
        return _FALLBACK

    recent = (history or [])[-4:]
    context_lines = "\n".join(
        f"{m['role'].upper()}: {m['content'][:200]}" for m in recent
    )
    lesson_line = (
        f"CURRENT LESSON IN PROGRESS: {current_lesson_title}\n\n"
        if current_lesson_title else ""
    )
    user_msg = (
        f"{lesson_line}{context_lines}\n\nSTUDENT NOW: {question}"
        if context_lines else f"{lesson_line}{question}"
    )

    system_prompt = _ROUTER_SYSTEM
    if _CURRICULUM_CATALOG:
        system_prompt += (
            "\n\nCURRICULUM (the only lessons this assistant teaches):\n"
            + _CURRICULUM_CATALOG
        )

    last_exc: Optional[Exception] = None
    raw = ""

    for attempt in range(1, ROUTER_MAX_RETRIES + 1):
        try:
            raw = _call_router_llm(client, system_prompt, user_msg)
            print(f"[router] raw response (attempt {attempt}/{ROUTER_MAX_RETRIES}): {raw}")
            break
        except Exception as e:
            last_exc = e
            print(f"[router] LLM call failed on attempt {attempt}/{ROUTER_MAX_RETRIES}: {e}")
            traceback.print_exc()
            raw = ""
            if attempt < ROUTER_MAX_RETRIES:
                time.sleep(0.3 * attempt)
    else:
        # Loop exhausted without a successful break — all retries failed.
        pass

    if not raw.strip():
        print(f"[router] all {ROUTER_MAX_RETRIES} attempts failed "
              f"(last error: {last_exc}) — using fallback")
        return _FALLBACK

    m = _JSON_RE.search(raw)
    if not m:
        print(f"[router] no JSON found in raw response: {raw!r} — using fallback")
        return _FALLBACK

    try:
        data = json.loads(m.group())
    except json.JSONDecodeError as e:
        print(f"[router] JSON parse failed ({e}) on raw: {raw!r} — using fallback")
        traceback.print_exc()
        return _FALLBACK

    # Re-validate extra_modes even though the prompt forbids it: "exercise_answer"
    # may never appear in extra_modes nor be combined with anything. A prompt
    # rule is not a guarantee, so force extra_modes=[] in those cases.
    # An out-of-curriculum verdict overrides intent/action/mode.
    in_curr = data.get("in_curriculum")
    if in_curr is False and _CURRICULUM_CATALOG:
        data["intent"], data["action"], data["mode"] = "out_of_program", "direct_llm", "conversational"
        data["lesson_number"], data["extra_modes"] = None, []
    else:
        in_curr = None if in_curr is None else bool(in_curr)
    raw_mode = data.get("mode", "conversational")
    raw_extra = data.get("extra_modes") or []
    if not isinstance(raw_extra, list):
        raw_extra = []
    if raw_mode == "exercise_answer":
        extra_modes: List[str] = []
    else:
        extra_modes = [m for m in raw_extra if m != "exercise_answer"]

    # Force every summary request to use retrieval.
    # Keep intent/mode unchanged; only override the action.
    if raw_mode == "summary" and in_curr is not False:
        if data.get("action") != "retrieve":
            print(
                f"[router-fix] summary action "
                f"{data.get('action')!r} -> 'retrieve'"
            )
        data["action"] = "retrieve"

    try:
        return RouterOutput(
            intent        = data.get("intent",        "unknown"),
            action        = data.get("action",        "direct_llm"),
            mode          = raw_mode,
            topic         = data.get("topic"),
            lesson_number = data.get("lesson_number"),
            difficulty    = data.get("difficulty"),
            continues_current_lesson = data.get("continues_current_lesson"),
            extra_modes   = extra_modes,
            in_curriculum = in_curr,
        )

    except Exception as e:
        print(f"[router] failed to build RouterOutput from parsed data {data!r}: "
              f"{e} — using fallback")
        traceback.print_exc()
        return _FALLBACK