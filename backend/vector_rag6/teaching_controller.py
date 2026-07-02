"""
teaching_controller.py — Pure Python session state machine.
No LLM call. Reads RouterOutput + session dict.
Returns prompt_injection: a string that gets appended to build_prompt().

Tracks progress through a lesson's points and generates the teaching
instruction injected into the prompt (explain next point, continue,
give exercise, summarize, or congratulate on completion).

Note: the old "drawn_summary" mechanism was removed — it caused the
canvas to accumulate all prior points instead of staying clean per turn.
build_drawn_summary() is kept as a no-op for backward compatibility only.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from langsmith import traceable


@dataclass
class LessonSession:
    lesson_id       : Optional[str]  = None
    lesson_title    : Optional[str]  = None
    lesson_points   : List[str]      = field(default_factory=list)
    current_point   : int            = 0
    confirmed_points: List[int]      = field(default_factory=list)
    mode            : str            = "explanation"
    topic           : Optional[str]  = None


def parse_points(content: str) -> List[str]:
    lines = [l.strip().lstrip("-•·").strip() for l in content.splitlines()]
    return [l for l in lines if l]


@traceable(name="tc_advance", run_type="chain")
def advance(session: LessonSession) -> LessonSession:
    """Mark current point confirmed, move to next (if any remain)."""
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
    """
    DEPRECATED as of v6 — no longer used anywhere in this module.
    Each turn now gets its own isolated, empty canvas instead of the model
    being told to reconstruct everything confirmed so far. Kept only so
    callers importing this symbol don't break on upgrade.
    """
    return ""


@traceable(name="tc_get_injection", run_type="chain")
def get_prompt_injection(session: LessonSession, mode: str, lang: str) -> str:
    """
    Returns a short instruction string appended at the end of build_prompt().
    Decorated with @traceable so input/output appear in LangSmith nested
    under chat_turn -> generate_answer.
    """
    points = session.lesson_points
    idx    = session.current_point
    total  = len(points)

    if not points:
        return ""

    current_label = points[idx] if idx < total else points[-1]
    done_count    = len(session.confirmed_points)

    if is_lesson_complete(session):
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"✅ التلميذ أنهى جميع نقاط الدرس ({total}/{total} نقطة مؤكَّدة).\n"
                f"لا تشرح أي نقطة جديدة ولا تكرر ما سبق.\n"
                f"بدلاً من ذلك:\n"
                f"  1. هنّئه بإتمام الدرس بجملة مشجِّعة\n"
                f"  2. اسأله مباشرةً: «هل تريد تمريناً على الدرس، أم تريد ملخصاً شاملاً؟»"
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"✅ L'élève a terminé tous les points du cours ({total}/{total} points confirmés).\n"
                f"N'explique aucun nouveau point et ne répète rien.\n"
                f"À la place:\n"
                f"  1. Félicite-le avec une phrase encourageante\n"
                f"  2. Demande-lui directement: «Tu veux un exercice sur le cours, ou un résumé complet?»"
            )

    if mode == "explanation":
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"النقطة الحالية للشرح: النقطة {idx+1} من {total}: «{current_label}»\n"
                f"اشرح هذه النقطة فقط، ثم اسأل: «هل فهمت هذه النقطة؟ نكمل للتالية؟»"
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"Point actuel à expliquer: point {idx+1}/{total}: «{current_label}»\n"
                f"Explique uniquement ce point, puis demande: «Tu as compris ? On passe au suivant ?»"
            )

    if mode == "continuation":
        prev_idx   = idx - 1 if idx > 0 else 0
        prev_label = points[prev_idx]
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"التلميذ أكد فهمه للنقطة {prev_idx+1}: «{prev_label}»\n"
                f"الآن اشرح النقطة {idx+1} من {total}: «{current_label}»\n"
                f"ثم اسأل: «هل فهمت هذه النقطة؟ نكمل للتالية؟»"
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"L'élève a confirmé avoir compris le point {prev_idx+1}: «{prev_label}»\n"
                f"Explique maintenant le point {idx+1}/{total}: «{current_label}»\n"
                f"Puis demande: «Tu as compris ? On passe au suivant ?»"
            )

    if mode == "exercise":
        if session.confirmed_points:
            last_confirmed = max(session.confirmed_points)
            base = points[last_confirmed] if last_confirmed < total else current_label
        else:
            base = current_label
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"التلميذ يريد تمريناً على: «{base}»\n"
                f"أعطِه تمريناً واحداً فقط على هذه النقطة — لا تعطِ الحل."
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"L'élève veut un exercice sur: «{base}»\n"
                f"Donne UN seul exercice sur ce point — ne donne pas la solution."
            )

    if mode == "summary":
        covered = [points[i] for i in sorted(session.confirmed_points) if i < total]
        covered_str = " • ".join(covered) if covered else "toutes les notions"
        if lang == "ar":
            return (
                f"\n\n[تعليمات التحكم في التدريس]\n"
                f"التلميذ أنهى {done_count} من {total} نقطة.\n"
                f"النقاط المُغطَّاة: {covered_str}\n"
                f"قدّم ملخصاً شاملاً كخريطة ذهنية SVG في [WIDGET]."
            )
        else:
            return (
                f"\n\n[Instructions pédagogiques]\n"
                f"L'élève a terminé {done_count}/{total} points.\n"
                f"Points couverts: {covered_str}\n"
                f"Génère un résumé sous forme de carte mentale SVG dans [WIDGET]."
            )

    return ""