"""
tts_text.py — prepares [SPEECH] / [TEACHER] text for ElevenLabs.

Strips or rewrites what only makes sense visually (bidi isolation marks, $$...$$
blocks, markdown) and spells out math for the voice: operators, degrees,
exponents, and point/angle names letter by letter ("ABC" -> "A B C").
Best-effort: tune it as you listen to real lessons (fractions will need more
rules).
"""

from __future__ import annotations

import re

from prompt_builder import _LRI, _PDI  # same bidi marks isolate_inline_math() uses

_DOLLAR_BLOCK_RE   = re.compile(r"\$\$(.+?)\$\$", re.S)
_LATEX_CMD_RE      = re.compile(r"\\[a-zA-Z]+")
# 1 to 3 bare UPPERCASE letters (point/angle/triangle names: "A", "AB", "ABC").
# Uppercase only, so short lowercase French/Arabic words are left alone.
_LETTER_RUN_RE     = re.compile(r"\b([A-Z])([A-Z])?([A-Z])?\b")
_MD_RESIDUE_RE     = re.compile(r"[*_#`]")
_WS_RE             = re.compile(r"\s+")

# Exponents: "^7", "^{7}", or unicode superscript digits (2², 8³...).
_CARET_EXP_RE      = re.compile(r"\^\{?(-?\d+)\}?")
_SUPERSCRIPT_MAP   = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻", "0123456789-")
_SUPERSCRIPT_RE    = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+")

_POWER_WORD = {"ar": "أس", "fr": "puissance"}

# The app is Arabic-only (see prompt_builder.py), so only "ar" is defined here.
_OPERATOR_WORDS = {
    "ar": {"+": "زائد", "-": "ناقص", "*": "في", "×": "في", "/": "على", "÷": "على", "=": "يساوي"},
}

# Fires only between two digits, or between a "°" and a digit ("90°+45°": the
# "+" follows "°", not a digit).
_OPERATOR_RE = re.compile(r"(?<=[\d°])\s*([+\-*/×÷=])\s*(?=\d)")

# Degree symbol: "90°" -> "90 درجة". Must run AFTER _speak_operators, otherwise
# the "+" in "90°+45°" would follow "ة" and _OPERATOR_RE would stop matching.
_DEGREE_WORD = {"ar": "درجة"}
_DEGREE_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*°")

# Raw LaTeX operator macros that can leak into the text. They are mapped to the
# plain symbol (and so to a spoken word) instead of being deleted like other
# \commands.
_LATEX_OP_MAP = [
    (re.compile(r"\\times"), "×"),
    (re.compile(r"\\cdot"), "×"),
    (re.compile(r"\\div"), "÷"),
]


def _normalize_latex_operators(text: str) -> str:
    for pattern, repl in _LATEX_OP_MAP:
        text = pattern.sub(repl, text)
    return text


def _speak_operators(text: str, lang: str) -> str:
    words = _OPERATOR_WORDS.get(lang, _OPERATOR_WORDS["ar"])
    return _OPERATOR_RE.sub(lambda m: f" {words[m.group(1)]} ", text)


def _speak_degrees(text: str, lang: str) -> str:
    word = _DEGREE_WORD.get(lang, _DEGREE_WORD["ar"])
    return _DEGREE_RE.sub(lambda m: f" {m.group(1)} {word} ", text)


def _speak_exponents(text: str, lang: str) -> str:
    word = _POWER_WORD.get(lang, _POWER_WORD["fr"])
    text = _CARET_EXP_RE.sub(lambda m: f" {word} {m.group(1)} ", text)
    text = _SUPERSCRIPT_RE.sub(
        lambda m: f" {word} {m.group(0).translate(_SUPERSCRIPT_MAP)} ", text
    )
    return text


def _speak_letter_runs(text: str) -> str:
    """Space out point/angle/triangle names ("ABC" -> "A B C") so TTS spells them."""
    return _LETTER_RUN_RE.sub(
        lambda mm: " ".join(g for g in mm.groups() if g), text
    )


def _strip_latex_block(m: "re.Match[str]") -> str:
    """Drop raw LaTeX commands and braces inside a $$...$$ block (e.g. \\widehat,
    \\frac). TODO: a command-aware rewrite ("\\frac{a}{b}" -> "a على b")."""
    inner = m.group(1)
    inner = _LATEX_CMD_RE.sub(" ", inner)
    inner = re.sub(r"[{}\\_]", " ", inner)  # "^" is kept: exponents were already spoken
    return " " + inner + " "


def prepare_for_tts(text: str, lang: str = "fr") -> str:
    """Clean up [SPEECH] / [TEACHER] text before sending it to ElevenLabs.

    lang: "ar" or "fr", picks the spoken word for exponents ("أس" / "puissance").
    Defaults to "fr"; pass the result of detect_language() when possible.
    """
    if not text:
        return ""
    cleaned = text.replace(_LRI, "").replace(_PDI, "")
    # 1. LaTeX operator macros -> plain symbols, before _strip_latex_block would
    #    delete them and before _speak_operators looks for literal symbols.
    cleaned = _normalize_latex_operators(cleaned)
    # 2. Exponents, while "^{7}" is intact (_strip_latex_block removes braces).
    cleaned = _speak_exponents(cleaned, lang)
    cleaned = _DOLLAR_BLOCK_RE.sub(_strip_latex_block, cleaned)
    # 3. Operators between digits -> spoken words (whole string).
    cleaned = _speak_operators(cleaned, lang)
    # 4. Degree symbol -> spoken word (must follow _speak_operators).
    cleaned = _speak_degrees(cleaned, lang)
    # 5. Letter runs, on the whole string (plain prose as well as math blocks).
    cleaned = _speak_letter_runs(cleaned)
    cleaned = _MD_RESIDUE_RE.sub("", cleaned)
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    return cleaned