"""
teaching_controller.py — pure-Python lesson session state machine (no LLM call).

Reads the router's mode and a LessonSession, and returns the instruction string
appended to build_prompt() for this turn.

Main entry points:
  get_prompt_injection(session, mode, lang, question, difficulty)
      -> (injection_str, active_point_text, point_idx)
      Modes: explanation, continuation, exercise, exercise_answer, summary.
  get_extra_mode_addendum(session, mode, lang)
      Short, widget-free addendum for each extra mode of a combined turn; it
      never mutates the session.
  advance(session)  marks the current point confirmed and moves to the next.

Design notes:
  - Exercise modes keep no server-side state: the model reads the conversation
    history to find the exercise it gave, count the student's attempts and avoid
    repeating itself. Exercise targeting is "last confirmed point, else the
    whole lesson"; the model decides the scope from the question, because a
    word-overlap heuristic kept failing on Arabic morphology (broken plurals,
    attached "و"/"ب"/"ل" prefixes).
  - exercise / exercise_answer are text-only and must not re-explain the lesson.
    exercise_answer handles three cases: a direct request for the correction
    (give final answers concisely), a correct answer (one line of praise), and a
    wrong answer (one hint on the first attempt, the full solution afterwards).
  - Explanation and continuation end by asking whether the student understood
    and name the next point when there is one.
  - _infer_starting_point() picks the first point of a brand-new session from
    the question. _infer_matching_points() is currently unused.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Set
from langsmith import traceable


@dataclass
class LessonSession:
    lesson_id            : Optional[str] = None
    lesson_title         : Optional[str] = None
    lesson_points        : List[str]     = field(default_factory=list)
    current_point        : int           = 0
    confirmed_points     : List[int]     = field(default_factory=list)
    mode                 : str           = "explanation"
    topic                 : Optional[str] = None
    # One lesson point is always explained in full in a single turn, and exercise
    # state is not tracked here (the model reads the conversation history).


def parse_points(content: str) -> List[str]:
    lines = [l.strip().lstrip("-•·").strip() for l in content.splitlines()]
    return [l for l in lines if l]


@traceable(name="tc_advance", run_type="chain")
def advance(session: LessonSession) -> LessonSession:
    """Confirm the current point and move to the next one (one whole point per confirmation)."""
    if session.current_point not in session.confirmed_points:
        session.confirmed_points.append(session.current_point)
    if session.current_point < len(session.lesson_points) - 1:
        session.current_point += 1
    return session


def is_lesson_complete(session: LessonSession) -> bool:
    total = len(session.lesson_points)
    if total == 0:
        return False
    return all(i in range(total) and i in session.confirmed_points for i in range(total))


def build_drawn_summary(session: LessonSession) -> str:
    """Deprecated and unused: kept only so existing imports do not break."""
    return ""


# ─────────────────── STARTING-POINT INFERENCE ───────────────────
# Best-effort, dependency-free Arabic/French word-overlap matcher. Used by
# explanation mode to choose the starting point of a brand-new session, and never
# on a lesson already in progress. Exercise mode does not use it any more.
_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Small, conservative stopword list: generic phrasing words (and boilerplate math
# words such as "أعداد"/"طبيعية" that recur in most lesson points) that must not
# count as a match against any lesson point.
_STOPWORDS = {
    "في", "من", "الى", "إلى", "على", "عن", "و", "أو", "او", "ما", "هي", "هو",
    "ماهي", "ماهو", "هذا", "هذه", "التي", "الذي", "كيف", "متى", "لماذا",
    "لي", "لنا", "درس", "الدرس", "دروس", "فسرلي", "فسر", "اشرح", "شرح",
    "علمني", "فهمني",
    "أعداد", "عدد", "الأعداد", "طبيعية", "الطبيعية", "مجموعة",
    "le", "la", "les", "de", "du", "des", "un", "une", "et", "ou", "que",
    "qui", "est", "c'est", "leçon", "lecon", "cours", "explique", "moi",
}

# Arabic is inflection-heavy, so a plain word comparison misses near-matches like
# "القوة" vs "القوى". Strip a few common trailing inflections so both sides
# collapse to roughly the same root. Not a real stemmer: short singular/plural
# pairs such as "قوة"/"قوى" are still not reconciled (the length guard below), so
# a missed match just falls back to point 0.
_SUFFIXES = ("تين", "ين", "ات", "ة", "ى", "ه")

# Attached prefixes, longest first: conjunctions/prepositions ("و", "ف", "ب",
# "ك", "ل") attach directly to the next word and can stack before the article
# "ال" (e.g. "والقوة" = "و" + "القوة"). Longest-first so "وال" is stripped whole.
_PREFIXES = ("وال", "فال", "بال", "كال", "لل", "ال", "و", "ف", "ب", "ك", "ل")


def _normalize_ar_word(word: str) -> str:
    w = word
    for pre in _PREFIXES:
        if w.startswith(pre) and len(w) > len(pre) + 2:
            w = w[len(pre):]
            break
    for suf in _SUFFIXES:
        if w.endswith(suf) and len(w) > len(suf) + 2:
            w = w[: -len(suf)]
            break
    return w


def _significant_words(text: str) -> Set[str]:
    words = _TOKEN_RE.findall(text.lower())
    out: Set[str] = set()
    for w in words:
        if w in _STOPWORDS or len(w) < 3:
            continue
        out.add(_normalize_ar_word(w))
    return out


def _loose_key(word: str) -> str:
    """Very loose comparison key (قوة / قوى / القوة all -> 'قو') used ONLY to
    recognise that a question word is just the LESSON's own topic word."""
    w = _normalize_ar_word(word)
    return w[:-1] if len(w) > 2 and w[-1] in "ةىهي" else w


def _infer_starting_point(lesson_points: List[str], question: str,
                          lesson_title: str = "") -> int:
    """
    Return the index of the lesson point the question most specifically names, or
    0 if no point stands out.

    Each point's title (text before the first ":") counts double, plus the full
    point text. Words that merely name the lesson itself (e.g. "القوة" for the
    lesson "القوى في الأعداد الطبيعية") are removed first: they mean "teach me this
    lesson", not "this one point". A generic request ("فسرلي الدرس 2") scores 0
    everywhere and falls back to 0.

    Strict argmax: it returns exactly one index even if several points score > 0.
    """
    if not question or not lesson_points:
        return 0

    q_words = _significant_words(question)
    # Drop words that only name the lesson itself (see docstring); only the
    # remaining words can point at a specific lesson point.
    if lesson_title:
        topic_keys = {_loose_key(w) for w in _significant_words(lesson_title)}
        q_words = {w for w in q_words if _loose_key(w) not in topic_keys}
    if not q_words:
        return 0

    best_idx, best_score = 0, 0
    for i, point in enumerate(lesson_points):
        title = point.split(":", 1)[0].split("：", 1)[0]
        title_words = _significant_words(title)
        full_words  = _significant_words(point)
        score = 2 * len(q_words & title_words) + len(q_words & full_words)
        if score > best_score:
            best_score, best_idx = score, i

    return best_idx if best_score > 0 else 0


def _infer_matching_points(lesson_points: List[str], question: str) -> List[int]:
    """
    Like _infer_starting_point() but returns every point scoring > 0, best first
    (same scoring formula). Currently unused: exercise mode now lets the model
    decide the scope. Check for other callers before deleting.
    """
    if not question or not lesson_points:
        return []

    q_words = _significant_words(question)
    if not q_words:
        return []

    scored: List[Tuple[int, int]] = []
    for i, point in enumerate(lesson_points):
        title = point.split(":", 1)[0].split("：", 1)[0]
        title_words = _significant_words(title)
        full_words  = _significant_words(point)
        score = 2 * len(q_words & title_words) + len(q_words & full_words)
        if score > 0:
            scored.append((i, score))

    scored.sort(key=lambda pair: pair[1], reverse=True)
    return [i for i, _ in scored]


# Short title of a lesson point (text before the first ":" or "："), used to name
# the next point in the closing question of explanation / continuation.
def _point_title(point: str) -> str:
    """Return the title part of a lesson point ("العنوان: التفاصيل" -> "العنوان")."""
    return point.split(":", 1)[0].split("：", 1)[0].strip()


@traceable(name="tc_get_injection", run_type="chain")
def get_prompt_injection(
    session   : LessonSession,
    mode      : str,
    lang      : str,
    question  : str = "",
    difficulty: Optional[str] = None,
) -> Tuple[str, str, Optional[int]]:
    """
    Return (injection_str, active_point_text, point_idx):
      - injection_str:     instruction string appended at the end of build_prompt().
      - active_point_text: the point (or, in exercise modes, the target text) in
                           focus; "" when no single point is (summary, lesson
                           complete, no points).
      - point_idx:         index of the point in focus for explanation,
                           continuation and exercise_answer; None otherwise. The
                           prompt builder no longer uses it; callers must still
                           handle None.

    `question` is used only to infer the starting point on the first explanation
    turn of a new session. `difficulty` (easy / medium / hard, from the router)
    is used only in exercise mode and defaults to "medium".
    """
    points = session.lesson_points
    idx    = session.current_point
    total  = len(points)

    if not points:
        return "", "", None

    # First explanation turn of a brand-new session only (nothing confirmed, still
    # at index 0): start at the point the question names. Never touches a lesson
    # already in progress.
    if mode == "explanation" and not session.confirmed_points and idx == 0 and question:
        inferred_idx = _infer_starting_point(
            points, question, session.lesson_title or ""
        )
        if inferred_idx != 0:
            session.current_point = inferred_idx
            idx = inferred_idx

    current_label = points[idx] if idx < total else points[-1]
    done_count    = len(session.confirmed_points)

    # Look one point ahead so the closing question can name the next point; there
    # is none on the last point.
    next_idx   = idx + 1
    has_next   = next_idx < total
    next_title = _point_title(points[next_idx]) if has_next else ""



    # exercise / exercise_answer still work after the lesson is fully confirmed
    # (the congratulations message offers "an exercise or a summary?"), so they
    # skip the lesson-complete short-circuit.
    if is_lesson_complete(session) and mode not in ("exercise", "exercise_answer"):
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"✅ التلميذ أنهى جميع نقاط الدرس ({total}/{total} نقطة مؤكَّدة).\n"
                f"لا تشرح أي نقطة جديدة ولا تكرر ما سبق.\n"
                f"بدلاً من ذلك:\n"
                f"  1. هنّئه بإتمام الدرس بجملة مشجِّعة\n"
                f"  2. اسأله مباشرةً: «هل تريد تمريناً على الدرس، أم تريد ملخصاً شاملاً؟»",
                "",
                None
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"✅ L'élève a terminé tous les points du cours ({total}/{total} points confirmés).\n"
                f"N'explique aucun nouveau point et ne répète rien.\n"
                f"À la place:\n"
                f"  1. Félicite-le avec une phrase encourageante\n"
                f"  2. Demande-lui directement: «Tu veux un exercice sur le cours, ou un résumé complet?»",
                "",
                None
            )

    if mode == "explanation":
        if lang == "ar":
            # Name the next point in the closing question when there is one.
            closing_ar = (
                f"هل فهمت هذا الجزء؟ ننتقل إلى «{next_title}»؟" if has_next
                else "هل فهمت هذا الجزء؟"
            )
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"النقطة الحالية للشرح: النقطة {idx+1} من {total}: «{current_label}»\n"
                f"اشرح هذا الجزء فقط ، ثم اسأل: «{closing_ar}»",
                current_label,
                idx
            )
        else:
            # Name the next point in the closing question when there is one.
            closing_fr = (
                f"Tu as compris cette partie ? On passe à « {next_title} » ?" if has_next
                else "Tu as compris cette partie ?"
            )
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"Point actuel à expliquer: point {idx+1}/{total}: «{current_label}»\n"
                f"Explique uniquement cette partie, puis demande: «{closing_fr}»",
                current_label,
                idx
            )

    if mode == "continuation":
        prev_idx   = idx - 1 if idx > 0 else 0
        prev_label = points[prev_idx]
        if lang == "ar":
            # Name the next point in the closing question when there is one.
            closing_ar = (
                f"هل فهمت هذا الجزء؟ ننتقل إلى «{next_title}»؟" if has_next
                else "هل فهمت هذا الجزء؟"
            )
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"التلميذ أكد فهمه للجزء السابق (من النقطة {prev_idx+1}: «{prev_label}»)\n"
                f"الآن اشرح هذا الجزء فقط من النقطة {idx+1} من {total}: «{current_label}»\n"
                f"ثم اسأل: «{closing_ar}»",
                current_label,
                idx
            )
        else:
            # Name the next point in the closing question when there is one.
            closing_fr = (
                f"Tu as compris cette partie ? On passe à « {next_title} » ?" if has_next
                else "Tu as compris cette partie ?"
            )
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"L'élève a confirmé avoir compris la partie précédente (point {prev_idx+1}: «{prev_label}»)\n"
                f"Explique maintenant uniquement cette partie du point {idx+1}/{total}: «{current_label}»\n"
                f"Puis demande: «{closing_fr}»",
                current_label,
                idx
            )

    if mode == "exercise":
        # Target: the last confirmed point (real session state), otherwise the
        # whole lesson. The model decides the exact scope from the question.
        if session.confirmed_points:
            last_confirmed = max(session.confirmed_points)
            base = points[last_confirmed] if last_confirmed < total else current_label
        else:
            base = "\n".join(points)

        diff = difficulty or "medium"

        if lang == "ar":
            diff_ar = {"easy": "سهل", "medium": "متوسط", "hard": "صعب"}.get(diff, diff)
            scope_rule_ar = (
                f"🔴🔴🔴 قاعدة قصوى — تحديد النطاق ونطاق الصدق: المحتوى المرجعي الكامل "
                f"لهذا الدرس معروض لك أعلاه في هذه الرسالة. اقرأ سؤال التلميذ الآن وحدد "
                f"بنفسك أي جزء منه يقصده:\n"
                f"  • إن ذكر نقطة محددة من الدرس (مثل «تعريف القوة» أو «خصائص القوى») "
                f"→ اكتب تمارين على تلك النقطة فقط.\n"
                f"  • إن ذكر أكثر من نقطة/موضوع محدد → اكتب تمارين على كل موضوع ذكره على حدة، "
                f"تحت عنوان فرعي واضح لكل موضوع (انظر قواعد العدد أدناه).\n"
                f"  • إن كان سؤالاً عاماً لا يسمّي نقطة بعينها (مثل «أعطني تمريناً على الدرس») "
                f"→ اكتب تمارين تغطي نقاط الدرس كاملة (يمكنك توزيعها على أكثر من نقطة).\n"
                f"  • إن ذكر التلميذ موضوعاً لا يظهر إطلاقاً في المحتوى المرجعي المعروض أعلاه "
                f"(درساً آخر تماماً، مثل «الأعداد الكسرية» إن لم تكن ضمن هذا الدرس) → لا تختلق "
                f"أي معلومة عنه ولا تكتب له أي تمرين، ولا تدمجه رياضياً مع محتوى هذا الدرس حتى لو "
                f"بدا الدمج ممكناً نظرياً؛ فقط أخبر التلميذ بلطف أنك لا تملك حالياً درساً حول ذلك "
                f"الموضوع تحديداً في هذا النظام، واكتفِ بتمارين على الموضوع/المواضيع المتوفرة فعلاً.\n"
                f"الصدق بشأن ما هو متوفر لديك فعلاً أولوية مطلقة على محاولة تغطية كل ما يطلبه "
                f"التلميذ."
            )
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"{scope_rule_ar}\n"
                f"مستوى الصعوبة: {diff_ar} — لا تختبر أي محتوى خارج نطاق الدرس المرجعي المعروض "
                f"أعلاه.\n"
                f"🔴 عدد التمارين — حالة موضوع واحد: إذا استعمل التلميذ صيغة الجمع (مثل «تمارين» "
                f"بدل «تمرين») أو طلب صراحة أكثر من تمرين واحد على نفس الموضوع، أعطِ 3 أو 5 تمارين "
                f"متنوعة (تختلف في الصياغة أو درجة الصعوبة الدقيقة) على نفس ذلك الموضوع بدلاً من "
                f"تمرين واحد فقط. إذا لم يطلب صراحة أكثر من تمرين ولم يستعمل صيغة الجمع، اكتفِ "
                f"بتمرين واحد فقط.\n"
                f"🔴 عدد التمارين — حالة عدة مواضيع مختلفة مذكورة صراحة في رسالة التلميذ وموجودة "
                f"فعلياً في المحتوى المرجعي أعلاه: أعطِ 3 تمارين للموضوع الأول تحت عنوان فرعي واضح "
                f"(مثل «تمارين حول [الموضوع الأول]»)، ثم 3 تمارين للموضوع الثاني تحت عنوان فرعي "
                f"منفصل، وهكذا لكل موضوع مذكور فعلاً موجود في المحتوى. ثم — فقط إن كان الجمع بين "
                f"المفاهيم ممكناً منطقياً في تمرين واحد — أضف 3 تمارين إضافية تحت عنوان «تمارين "
                f"تجمع بين المواضيع». لا تحكم على الدمج بمدى شيوعه، بل فقط بإمكانية بنائه رياضياً. "
                f"فقط إن كان الدمج مستحيلاً رياضياً فعلاً (لا يوجد أي معطى مشترك ممكن بين "
                f"الموضوعين)، اكتفِ بالمجموعات المنفصلة وقل للتلميذ بلطف أن الدمج غير ممكن هنا.\n"
                f"🔴 راجع الرسائل السابقة في هذه المحادثة بنفسك قبل الصياغة: لا تكرر نفس صياغة أو "
                f"نفس مثال تمرين سبق أن أعطيته لهذا التلميذ.\n"
                f"🔴🔴 هذه خطوة تمرين فقط: لا تشرح أي درس أو مفهوم أو قاعدة قبل أو بعد التمارين، "
                f"إلا إذا طلب التلميذ ذلك صراحة في رسالته الحالية. اكتب التمارين مباشرة، بدون "
                f"مقدمة شرحية.\n"
                f"🔴🔴 لا تستعمل [WIDGET] أو أي رسم/خريطة تفاعلية في هذه الخطوة إطلاقاً — التمارين "
                f"نص فقط.\n"
                f"لا تعطِ أي حل أو تلميح لأي تمرين في هذه الخطوة.",
                base,
                None
            )
        else:
            diff_fr = {"easy": "facile", "medium": "moyen", "hard": "difficile"}.get(diff, diff)
            scope_rule_fr = (
                f"🔴🔴🔴 Règle absolue — détermination du périmètre et honnêteté sur le contenu "
                f"disponible: le contenu de référence complet de cette leçon est affiché "
                f"ci-dessus dans ce message. Lis la question de l'élève et détermine toi-même "
                f"quelle partie il vise:\n"
                f"  • S'il nomme un point précis de la leçon (par ex. « les propriétés des "
                f"puissances ») → rédige des exercices uniquement sur ce point.\n"
                f"  • S'il nomme plusieurs points/sujets précis → rédige des exercices pour "
                f"chaque sujet mentionné séparément, sous un sous-titre clair par sujet (voir "
                f"règles de nombre ci-dessous).\n"
                f"  • Si la demande est générique et ne nomme aucun point précis (par ex. "
                f"« donne-moi un exercice sur le cours ») → rédige des exercices couvrant "
                f"l'ensemble des points de la leçon (tu peux les répartir sur plusieurs points).\n"
                f"  • Si l'élève mentionne un sujet qui n'apparaît nulle part dans le contenu de "
                f"référence affiché ci-dessus (une tout autre leçon) → n'invente rien à son sujet "
                f"et ne rédige aucun exercice le concernant, et ne le combine pas mathématiquement "
                f"avec le contenu de cette leçon même si cela semble possible en théorie; dis "
                f"simplement et gentiment à l'élève que tu n'as pas actuellement de cours sur ce "
                f"sujet précis dans ce système, et limite-toi à des exercices sur le(s) sujet(s) "
                f"réellement disponible(s).\n"
                f"L'honnêteté sur ce dont tu disposes réellement prime sur la volonté de couvrir "
                f"tout ce que demande l'élève."
            )
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"{scope_rule_fr}\n"
                f"Niveau de difficulté: {diff_fr} — ne teste rien en dehors de la leçon de "
                f"référence affichée ci-dessus.\n"
                f"🔴 Nombre d'exercices — un seul sujet: si l'élève utilise le pluriel (« des "
                f"exercices » au lieu de « un exercice ») ou demande explicitement plus d'un "
                f"exercice sur le même sujet, donne 3 ou 5 exercices variés (formulation ou "
                f"difficulté légèrement différentes) sur ce même sujet plutôt qu'un seul. Sinon, "
                f"donne un seul exercice.\n"
                f"🔴 Nombre d'exercices — plusieurs sujets distincts explicitement mentionnés par "
                f"l'élève et réellement présents dans le contenu de référence ci-dessus: donne 3 "
                f"exercices pour le premier sujet sous un sous-titre clair (par ex. « Exercices "
                f"sur [premier sujet] »), puis 3 pour le second sous un sous-titre séparé, etc. "
                f"pour chaque sujet réellement présent. Puis — uniquement si combiner les notions "
                f"est logiquement possible — ajoute 3 exercices sous « Exercices combinant les "
                f"sujets ». Si la combinaison n'est pas possible, limite-toi aux groupes séparés et "
                f"dis-le gentiment à l'élève.\n"
                f"🔴 Relis toi-même les messages précédents avant de rédiger: ne répète pas la "
                f"même formulation ni le même exemple qu'un exercice déjà donné à cet élève.\n"
                f"🔴🔴 Ceci est une étape d'exercices uniquement: n'explique aucun cours, concept "
                f"ou règle avant ou après les exercices, sauf si l'élève le demande explicitement "
                f"dans son message actuel. Donne directement les exercices, sans préambule "
                f"explicatif.\n"
                f"🔴🔴 N'utilise [WIDGET] ni aucun schéma/carte interactive à cette étape — les "
                f"exercices sont du texte uniquement.\n"
                f"Ne donne ni solution ni indice pour aucun exercice à cette étape.",
                base,
                None
            )

    if mode == "exercise_answer":
        # No session state: the model finds the exercise in the history and counts
        # the student's attempts itself. The bullets are ordered on purpose: a
        # direct request for the correction (nothing to grade) is checked first,
        # then a correct answer, then a wrong one.
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"التلميذ يجيب الآن على تمرين (أو أكثر) أعطيته سابقاً في هذه المحادثة.\n"
                f"🔴 ابحث بنفسك في الرسائل السابقة عن نص التمرين (أو التمارين) الذي يجيب عنه "
                f"التلميذ الآن، وصحّح فقط ما أجاب عنه فعلاً في رسالته — لا تفترض إجابات لتمارين لم "
                f"يذكرها أو يشِر إليها.\n"
                f"🔴🔴🔴 إن كانت رسالة التلميذ الحالية طلباً مباشراً للإصلاح أو الحل (مثل «اعطني "
                f"إصلاح تمرين 2»، «صحح لي التمرين»، «أعطني الحل») بدون أي محاولة إجابة فعلية من "
                f"جانبه في هذه الرسالة (أي لم يكتب أرقاماً أو حسابات أو نتائج يمكن تصحيحها): أعطِ "
                f"الإجابة النهائية مباشرة وبإيجاز شديد لكل عبارة/سؤال — الخطوة الحاسمة والنتيجة "
                f"النهائية فقط في سطر أو سطرين لكل عنصر، بدون إعادة شرح القاعدة أو سبب تطبيقها كما "
                f"لو كنت تُدرّس الدرس من جديد. لا تشرح القاعدة بالتفصيل في هذه الحالة إلا إذا صرّح "
                f"التلميذ أنه لم يفهم أو طلب توضيحاً إضافياً صراحة — في تلك الحالة فقط، ذكّر "
                f"بالقاعدة بإيجاز (مثل «كما رأينا في الدرس») ثم اعطِ الحل.\n"
                f"🔴🔴 إن كانت إجابته صحيحة بالكامل (أي قدّم فعلاً إجابة يمكن تصحيحها): قل له ذلك "
                f"في جملة تشجيع واحدة أو اثنتين فقط، ثم اسأله إن كان يريد تمريناً آخر أو المتابعة "
                f"للنقطة التالية. ممنوع تماماً في هذه الحالة إعادة شرح المفهوم أو الدرس أو أي جزء "
                f"منه بالتفصيل — هذا ليس وضع شرح، والتلميذ أثبت أنه فهم.\n"
                f"🔴 إن لم تكن صحيحة بالكامل (وقدّم فعلاً محاولة إجابة): عدّ بنفسك من المحادثة كم "
                f"محاولة سابقة قدّمها التلميذ لهذا التمرين تحديداً. إذا كانت هذه محاولته الثانية أو "
                f"أكثر وما زالت إجابته خاطئة أو ناقصة، لا تعطِ تلميحاً آخر — اكتب سطراً أو سطرين "
                f"فقط يذكّران بهدف التمرين (ملخص، وليس شرحاً مفصلاً من جديد)، ثم أعطِ الحل كاملاً. "
                f"وإلا (المحاولة الأولى): أعطِ تلميحاً واحداً موجهاً فقط، بدون حلّه كاملاً وبدون "
                f"إعادة شرح الدرس.\n"
                f"🔴🔴 لا تشرح أي درس أو مفهوم جديد لم يُطلب صراحة في رسالة التلميذ الحالية.\n"
                f"🔴🔴 لا تستعمل [WIDGET] أو أي رسم/خريطة تفاعلية في هذه الخطوة إطلاقاً.",
                current_label,
                idx
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"L'élève répond maintenant à un (ou plusieurs) exercice(s) que tu lui as donné plus "
                f"tôt dans cette conversation.\n"
                f"🔴 Retrouve toi-même dans les messages précédents l'énoncé de l'exercice (ou des "
                f"exercices) auquel l'élève répond maintenant, et ne corrige que ce qu'il a "
                f"réellement répondu dans son message — ne suppose aucune réponse pour un exercice "
                f"qu'il n'a pas mentionné ou traité.\n"
                f"🔴🔴🔴 Si le message actuel de l'élève est une demande directe de correction ou de "
                f"solution (par ex. « donne-moi la correction de l'exercice 2 », « corrige "
                f"l'exercice », « donne-moi la solution ») sans aucune tentative de réponse réelle "
                f"dans ce message (c'est-à-dire sans chiffres, calculs ou résultats à corriger): "
                f"donne directement la réponse finale, de façon très concise, pour chaque "
                f"expression/question — uniquement l'étape décisive et le résultat final, en une ou "
                f"deux lignes par élément, sans réexpliquer la règle ni pourquoi elle s'applique "
                f"comme si tu enseignais la notion pour la première fois. N'explique la règle en "
                f"détail dans ce cas que si l'élève dit explicitement qu'il n'a pas compris ou "
                f"demande une clarification — dans ce cas seulement, rappelle brièvement la règle "
                f"(par ex. « comme on l'a vu dans le cours ») puis donne la solution.\n"
                f"🔴🔴 Si sa réponse est entièrement correcte (c'est-à-dire qu'il a réellement fourni "
                f"une réponse à corriger): dis-le lui en une ou deux phrases d'encouragement "
                f"seulement, puis demande-lui s'il veut un autre exercice ou continuer au point "
                f"suivant. Il est interdit dans ce cas de réexpliquer le concept ou la leçon, même "
                f"partiellement — ce n'est pas un mode explication, et l'élève a prouvé qu'il a "
                f"compris.\n"
                f"🔴 Si sa réponse n'est pas entièrement correcte (et qu'il a réellement fourni une "
                f"tentative de réponse): compte toi-même, à partir de la conversation, combien de "
                f"tentatives précédentes l'élève a déjà faites pour cet exercice précis. Si c'est sa "
                f"2e tentative ou plus et que la réponse reste fausse/incomplète, ne redonne pas "
                f"d'indice — écris une ou deux lignes seulement rappelant l'objectif de l'exercice "
                f"(un résumé, pas une nouvelle explication détaillée), puis donne la solution "
                f"complète. Sinon (1ère tentative): donne un seul indice ciblé, sans donner la "
                f"solution complète et sans réexpliquer la leçon.\n"
                f"🔴🔴 N'explique aucun cours ou concept non demandé explicitement dans le message "
                f"actuel de l'élève.\n"
                f"🔴🔴 N'utilise [WIDGET] ni aucun schéma/carte interactive à cette étape.",
                current_label,
                idx
            )

    if mode == "summary":
        covered = [points[i] for i in sorted(session.confirmed_points) if i < total]
        if lang == "ar":
            covered_str = " • ".join(covered) if covered else "جميع النقاط"
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"التلميذ أنهى {done_count} من {total} نقطة.\n"
                f"النقاط المُغطَّاة: {covered_str}\n"
                f"قدّم ملخصاً شاملاً كخريطة ذهنية تفاعلية في [WIDGET].",
                "",
                None
            )
        else:
            covered_str = " • ".join(covered) if covered else "toutes les notions"
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"L'élève a terminé {done_count}/{total} points.\n"
                f"Points couverts: {covered_str}\n"
                f"Génère un résumé sous forme de carte mentale interactive dans [WIDGET].",
                "",
                None
            )

    return "", "", None


# ─────────── COMBINED-MODE ADDENDA ───────────
# Called once per entry of route.extra_modes, in addition to (never instead of)
# the single get_prompt_injection() call for the turn's primary mode.

@traceable(name="tc_get_extra_mode_addendum", run_type="chain")
def get_extra_mode_addendum(
    session: LessonSession,
    mode   : str,
    lang   : str,
) -> str:
    """
    Return a short, widget-free instruction for one extra mode of a combined
    turn, appended to the tc_injection already built for the primary mode. "" if
    there is nothing to add (no lesson points, or mode == "exercise_answer",
    which the router never combines with anything).

    Never mutates `session` (advancing stays exclusive to the primary mode's
    get_prompt_injection() call) and never asks for a [WIDGET]: only the turn's
    widget owner gets one.
    """
    points = session.lesson_points
    idx    = session.current_point
    total  = len(points)

    if not points or mode == "exercise_answer":
        return ""

    current_label = points[idx] if idx < total else points[-1]

    if mode == "summary":
        covered = [points[i] for i in sorted(session.confirmed_points) if i < total]
        if lang == "ar":
            covered_str = " • ".join(covered) if covered else "جميع النقاط"
            return (
                f"\n\n[تعليمات إضافية — ملخص]\n"
                f"بالإضافة لما سبق، أضف في نهاية [TEACHER] فقرة ملخّص قصيرة (سطران أو ثلاثة "
                f"أسطر، نص عادي فقط) تذكّر بالنقاط المُغطَّاة حتى الآن: {covered_str}.\n"
                f"🔴 لا تُنشئ [WIDGET] أو خريطة ذهنية منفصلة لهذا الملخص — فقرة نصية فقط ضمن "
                f"نفس [TEACHER]، بعد المحتوى الأساسي أعلاه."
            )
        else:
            covered_str = " • ".join(covered) if covered else "tous les points"
            return (
                f"\n\n[Instructions supplémentaires — résumé]\n"
                f"En plus de ce qui précède, ajoute à la fin de [TEACHER] un court paragraphe "
                f"récapitulatif (2-3 lignes, texte normal uniquement) rappelant les points "
                f"couverts jusqu'ici: {covered_str}.\n"
                f"🔴 Ne génère aucun [WIDGET] ni carte mentale séparée pour ce résumé — un "
                f"simple paragraphe de texte dans le même [TEACHER], après le contenu "
                f"principal ci-dessus."
            )

    if mode == "exercise":
        # Same target rule as the exercise branch of get_prompt_injection().
        if session.confirmed_points:
            last_confirmed = max(session.confirmed_points)
            base = points[last_confirmed] if last_confirmed < total else current_label
        else:
            base = "\n".join(points)
        if lang == "ar":
            return (
                f"\n\n[تعليمات إضافية — تمرين]\n"
                f"بالإضافة لما سبق، أضف تمريناً واحداً (نص فقط) على: {base}\n"
                f"🔴 لا تُنشئ [WIDGET] لهذا التمرين — أضِفه فقط كنص ضمن نفس [TEACHER]، بعد "
                f"المحتوى الأساسي أعلاه.\n"
                f"لا تعطِ حلاً أو تلميحاً لهذا التمرين في هذه الخطوة."
            )
        else:
            return (
                f"\n\n[Instructions supplémentaires — exercice]\n"
                f"En plus de ce qui précède, ajoute UN exercice (texte uniquement) sur: {base}\n"
                f"🔴 Ne génère aucun [WIDGET] pour cet exercice — ajoute-le simplement en texte "
                f"dans le même [TEACHER], après le contenu principal ci-dessus.\n"
                f"Ne donne ni solution ni indice pour cet exercice à cette étape."
            )

    if mode in ("explanation", "continuation"):
        if lang == "ar":
            return (
                f"\n\n[تعليمات إضافية — شرح]\n"
                f"بالإضافة لما سبق، اشرح أيضاً بإيجاز (سطر أو سطران فقط) هذه النقطة: "
                f"«{current_label}»\n"
                f"🔴 لا تُنشئ [WIDGET] أو رسماً منفصلاً لهذا الشرح الإضافي — نص فقط ضمن نفس "
                f"[TEACHER]، بعد المحتوى الأساسي أعلاه."
            )
        else:
            return (
                f"\n\n[Instructions supplémentaires — explication]\n"
                f"En plus de ce qui précède, explique aussi brièvement (1-2 lignes) ce point: "
                f"« {current_label} »\n"
                f"🔴 Ne génère aucun [WIDGET] ni schéma séparé pour cette explication "
                f"supplémentaire — texte uniquement dans le même [TEACHER], après le contenu "
                f"principal ci-dessus."
            )

    return ""