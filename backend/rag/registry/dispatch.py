"""
registry/dispatch.py
=====================
Point d'entrée unique appelé par llm.py après réception de la réponse du
modèle. Route representation -> fonction layout, valide les erreurs
d'appel (paramètres manquants/mauvais type) en erreurs explicites que
llm.py peut attraper pour déclencher un fallback ou un retry ciblé.
"""
from __future__ import annotations
from typing import Any

from .representations import (
    layout_number_line, layout_dot_grouping, layout_symbolic_formula,
    layout_venn_diagram, layout_euclidean_division, layout_factor_tree,
    layout_mirror_symmetry, layout_compass_construction, layout_angle_pair,
    layout_triangle_angle_sum, layout_quadrilateral_angle_sum,
    layout_circle_line_position,
)


class UnknownRepresentation(Exception):
    """representation absente du registre (ex: 'geometry_generic',
    'freeform_svg', ou une clé inventée par le LLM) -> le caller doit
    retomber sur l'ancien chemin freeform_svg."""


class InvalidParams(Exception):
    """la representation est connue mais les params reçus ne matchent
    pas la signature de la fonction (clé manquante, mauvais type...)."""


# ─────────────────── ALIAS DE PARAMÈTRES (v3.3) ───────────────────
# Le LLM invente parfois un nom de clé plausible mais faux (camelCase,
# synonyme évident) au lieu du nom exact du params_schema — ex:
# "groupSize"/"total_dots" au lieu de "group_size"/"total". C'est un
# renommage prévisible, pas une erreur de raisonnement: pas besoin d'un
# repair LLM pour ça, un dictionnaire d'alias suffit et coûte 0ms/0 token.
#
# Chaque entrée ici correspond à un cas RÉELLEMENT observé dans les logs
# de repair (voir stream_handler.py's exhausted-fallback log) — n'ajoute
# pas d'alias spéculatif, ajoute-en un dès qu'un nouveau typo récurrent
# apparaît dans les logs.
_PARAM_ALIASES: dict[str, dict[str, str]] = {
    "dot_grouping": {
        "groupSize": "group_size",
        "group_count": "group_size",
        "total_dots": "total",
        "totalDots": "total",
        "dots_total": "total",
        "dividendLabel": "dividend_label",
        "divisorLabel": "divisor_label",
        "quotientLabel": "quotient_label",
        "remainderLabel": "remainder_label",
    },
    "euclidean_division": {
        "dividendLabel": "dividend_label",
        "divisorLabel": "divisor_label",
        "quotientLabel": "quotient_label",
        "remainderLabel": "remainder_label",
    },
}


def _normalize_params(representation: str, params: dict[str, Any]) -> dict[str, Any]:
    aliases = _PARAM_ALIASES.get(representation)
    if not aliases:
        return params
    return {aliases.get(k, k): v for k, v in params.items()}


# ─────────────── FIXUP DE FORME: symbolic_formula subscript_text (v3.5) ───────────────
# Contrairement aux entrées de _PARAM_ALIASES ci-dessus (un simple renommage
# de clé), ce cas-ci est un changement de FORME: le modèle combine parfois
# base + indice dans un seul terme —
#   {"text": "D", "subscript": true, "subscript_text": "n"}
# — au lieu des deux termes séparés qu'exige params_schema (voir
# REPRESENTATIONS["symbolic_formula"] et layout_symbolic_formula's
# _KNOWN_TERM_KEYS guard, qui rejette "subscript_text" comme clé inconnue).
# Un dict alias ne peut pas exprimer "un terme devient deux", d'où une
# fonction dédiée plutôt qu'une entrée dans _PARAM_ALIASES.
#
# Même politique que _PARAM_ALIASES: n'ajoute un fixup de forme ici qu'après
# un cas RÉELLEMENT récurrent observé dans les logs de repair (voir
# stream_handler.py's exhausted-fallback log) — pas spéculatif.
def _split_combined_subscript_terms(params: dict[str, Any]) -> dict[str, Any]:
    formulas = params.get("formulas")
    if not isinstance(formulas, list):
        return params

    changed = False
    new_formulas = []
    for formula in formulas:
        if not isinstance(formula, list):
            new_formulas.append(formula)
            continue
        new_formula = []
        for term in formula:
            if isinstance(term, dict) and "subscript_text" in term:
                changed = True
                base = {k: v for k, v in term.items() if k != "subscript_text"}
                base.pop("subscript", None)  # subscript applies to the split-off term, not the base
                new_formula.append(base)
                new_formula.append({
                    "text": term["subscript_text"],
                    "subscript": True,
                })
            else:
                new_formula.append(term)
        new_formulas.append(new_formula)

    if not changed:
        return params
    return {**params, "formulas": new_formulas}


_SHAPE_FIXUPS = {
    "symbolic_formula": _split_combined_subscript_terms,
}


def _apply_shape_fixups(representation: str, params: dict[str, Any]) -> dict[str, Any]:
    fixup = _SHAPE_FIXUPS.get(representation)
    if not fixup:
        return params
    return fixup(params)


# ─────────────────── SANITISATION DES VALEURS TEXTE (v3.4) ───────────────────
# Le canvas dessine les chaînes "text"/"label"/"formula" caractère par
# caractère — il n'y a aucun moteur LaTeX côté rendu. Le prompt (v27.x)
# interdit désormais explicitement au LLM d'écrire des commandes LaTeX
# (\times, \frac{}{}...) dans [WIDGET], mais un modèle peut toujours en
# glisser une malgré la règle (observé en prod: "a = 1 \times a + 0" rendu
# tel quel, backslash inclus, au lieu de "a = 1 × a + 0"). Cette table est
# le filet de sécurité: elle s'applique à TOUTES les representations, pas
# seulement symbolic_formula, car dot_grouping/euclidean_division exposent
# les mêmes champs texte (dividend_label, divisor_label...) au même risque.
# N'ajoute une entrée ici qu'après un cas réellement observé dans les logs
# de repair — même politique que _PARAM_ALIASES ci-dessus.
_LATEX_TO_UNICODE: dict[str, str] = {
    "\\times": "×",
    "\\cdot":  "·",
    "\\div":   "÷",
    "\\pm":    "±",
    "\\mp":    "∓",
    "\\neq":   "≠",
    "\\leq":   "≤",
    "\\geq":   "≥",
    "\\approx": "≈",
    "\\infty": "∞",
    "\\sqrt":  "√",
}


def _sanitize_text(value: Any) -> Any:
    """Remplace récursivement les commandes LaTeX résiduelles par leur
    équivalent unicode dans toute valeur str/dict/list — appliqué juste
    avant l'appel à la fonction layout, donc après _normalize_params()
    (qui ne touche qu'aux clés) et avant que le texte n'atteigne le canvas.
    Ne lève jamais d'exception: une valeur non reconnue passe inchangée,
    au pire on retombe sur l'ancien comportement (texte brut affiché).
    """
    if isinstance(value, str):
        for latex, symbol in _LATEX_TO_UNICODE.items():
            if latex in value:
                value = value.replace(latex, symbol)
        return value
    if isinstance(value, dict):
        return {k: _sanitize_text(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_text(v) for v in value]
    return value


LAYOUT_REGISTRY = {
    "number_line":              layout_number_line,
    "dot_grouping":             layout_dot_grouping,
    "symbolic_formula":         layout_symbolic_formula,
    "venn_diagram":             layout_venn_diagram,
    "euclidean_division":       layout_euclidean_division,
    "factor_tree":              layout_factor_tree,
    "mirror_symmetry":          layout_mirror_symmetry,
    "compass_construction":     layout_compass_construction,
    "angle_pair":               layout_angle_pair,
    "triangle_angle_sum":       layout_triangle_angle_sum,
    "quadrilateral_angle_sum":  layout_quadrilateral_angle_sum,
    "circle_line_position":     layout_circle_line_position,
}

# représentations valides dans le catalogue mais sans fonction dédiée —
# routées explicitement vers le fallback, pas juste "clé absente"
_NO_FUNCTION_REPRESENTATIONS = {"geometry_generic", "freeform_svg"}


def build_widget(representation: str, params: dict[str, Any]) -> list[dict]:
    if representation in _NO_FUNCTION_REPRESENTATIONS:
        raise UnknownRepresentation(representation)

    fn = LAYOUT_REGISTRY.get(representation)
    if fn is None:
        raise UnknownRepresentation(representation)

    params = _normalize_params(representation, params)  # v3.3: alias fix-up
    params = _apply_shape_fixups(representation, params)  # v3.5: split combined subscript terms
    params = _sanitize_text(params)  # v3.4: strip residual LaTeX from text values

    try:
        return fn(**params)
    except TypeError as e:
        raise InvalidParams(f"{representation}: paramètres invalides — {e}") from e
    except ValueError as e:
        # les layouts lèvent ValueError pour les cas géométriquement
        # impossibles ou hors-canvas (ex: group_size trop grand) —
        # on les remonte tel quel, distinct d'un bug d'appel (TypeError)
        raise InvalidParams(f"{representation}: {e}") from e