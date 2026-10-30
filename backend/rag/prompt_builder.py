"""
prompt_builder.py — builds the system prompt sent to the LLM.

The prompt is always written in Arabic (the app currently serves 7ème,
Arabic-medium). It is assembled from:
  - the output-format contract ([WIDGET] / [SPEECH] / [TEACHER] tags), chosen per
    mode (explanation, summary, exercise, ...) and combined for multi-mode turns;
  - the widget menu from registry/ (build_menu): the model picks a
    {"representation", "params"} JSON and registry/ computes the coordinates,
    so the LLM never invents pixel positions;
  - the lesson content, per-lesson terminology rules ("keywords") and the
    coverage checklist for the active point.

Main entry point: build_prompt(). CANVAS_ENGINE_SHELL is the server-owned HTML/JS
used to render widgets; render_canvas_widget() fills it with the steps.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

from registry import build_menu, REPRESENTATIONS, get_variant_carrying_representations

# ─────────────────────────── SVG PALETTE ───────────────────────

SVG_PALETTE = {
    "primary":   os.environ.get("SVG_PRIMARY",   "#6350db"),
    "secondary": os.environ.get("SVG_SECONDARY", "#e05c2a"),
    "accent":    os.environ.get("SVG_ACCENT",    "#2a9e6e"),
    "auxiliary": os.environ.get("SVG_AUXILIARY", "#8b7cf0"),
    "text":      os.environ.get("SVG_TEXT",      "#1a1a2e"),
    "highlight": os.environ.get("SVG_HIGHLIGHT", "#f0c040"),
}

CANVAS_COLOR_NAMES_AR = {
    "primary": "بنفسجي", "secondary": "برتقالي", "accent": "أخضر",
    "aux": "بنفسجي فاتح", "hi": "أصفر ذهبي", "text": "كحلي غامق",
}
CANVAS_COLOR_NAMES_FR = {
    "primary": "violet", "secondary": "orange", "accent": "vert",
    "aux": "violet clair", "hi": "jaune doré", "text": "bleu marine foncé",
}


def _canvas_color_table(lang: str) -> str:
    names = CANVAS_COLOR_NAMES_AR if lang == "ar" else CANVAS_COLOR_NAMES_FR
    return " | ".join(f"{k} = {v}" for k, v in names.items())


# ── BIDI ISOLATION FOR INLINE MATH ──
_LRI = "\u2066"
_PDI = "\u2069"

_BIDI_MATH_RULE_AR = (
    f"🔴 قاعدة إلزامية — اتجاه الكتابة داخل الأعداد والعمليات: أي تعبير رياضي "
    f"(أرقام، عمليات +/-/×/÷، أُسّ مرتفع، أقواس) مكتوب وسط جملة عربية يجب أن "
    f"يُحاط بالكامل بهذين الرمزين غير المرئيين: ابدأ بـ \"{_LRI}\" مباشرة قبل أول "
    f"رقم أو قوس في التعبير، واختم بـ \"{_PDI}\" مباشرة بعد آخر رقم أو قوس فيه. "
    f"هذان الرمزان غير مرئيين للتلميذ ولا يُحسبان جزءاً من نص القاعدة الممنوعة "
    f"(LaTeX/كود) — لا تذكرهما أو تشرحهما للتلميذ إطلاقاً."
)
_FORMULA_DELIMITER_RULE_AR = (
    "🔴 قاعدة صارمة: عند عرض معادلة/صيغة كاملة كسطر مستقل، أحطها بالكامل بـ "
    "\"$$\" من الطرفين، مثل \"$$A = 8³ × 4⁵ × 2⁷$$\". أي رقم يُذكر عابراً داخل "
    "جملة عادية لا يُحاط بـ \"$$\" إطلاقاً. داخل \"$$...$$\" فقط يمكنك استعمال "
    "أوامر LaTeX (\\times، \\text{}، \\frac{}{}، ^{}، _{}) — ممنوع استعمالها "
    "خارج \"$$...$$\" مطلقاً."
)
_ANGLE_NAME_NOTATION_RULE_AR = (
    "🔴 استثناء صريح من قاعدة \"$$\" أعلاه: عند تسمية زاوية بأحرف نقاطها "
    "الثلاث (مثل AOB)، اكتبها دائماً \"$$\\widehat{AOB}$$\" — حتى وسط جملة "
    "عادية وليست سطراً مستقلاً؛ \\widehat{} هنا اسمُ الزاوية نفسه وليس صيغة "
    "كاملة، فهو مسموح مضمّناً داخل الجملة استثناءً. ⛔ ممنوع استعمال رمز "
    "\"∠\" إطلاقاً — الغطاء المرسوم في [WIDGET] فوق حروف اسم الزاوية هو "
    "widehat حقيقي (قوس فوق الحروف)، وليس رمز \"∠\"، فاستعمال \"∠\" في النص "
    "يخلق تناقضاً بصرياً مع الرسم الذي يراه التلميذ."
)
_SUPERSCRIPT_DIGITS = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUPERSCRIPT_OPS = "⁺⁻ˣ"
_MATH_TOKEN = (
    rf"(?:[0-9]+(?:[.,][0-9]+)?[{_SUPERSCRIPT_DIGITS}]*"
    rf"|[{_SUPERSCRIPT_DIGITS}{_SUPERSCRIPT_OPS}]+"
    rf"|[A-Za-z](?:['′])?"
    rf"|[+\-×x÷=\^{_SUPERSCRIPT_OPS}]|[()])"
)
_MATH_RUN_RE = re.compile(rf"{_MATH_TOKEN}(?:[ \t]*{_MATH_TOKEN})*")
_MATH_TOKEN_RE = re.compile(_MATH_TOKEN)
_MATH_NUMBER_RE = re.compile(
    rf"^[0-9]+(?:[.,][0-9]+)?[{_SUPERSCRIPT_DIGITS}]*$|^[{_SUPERSCRIPT_DIGITS}]+$"
)
_MATH_OP_TOKENS = {"+", "-", "×", "x", "÷", "=", "(", ")", "^", "⁺", "⁻"}
_LETTER_TOKEN_RE = re.compile(r"^[A-Z](?:['′])?$")


_DOLLAR_BLOCK_RE = re.compile(r"\$\$.*?\$\$", re.DOTALL)


def isolate_inline_math(text: str) -> str:
    if not text:
        return text
    cleaned = text.replace(_LRI, "").replace(_PDI, "")

    def _wrap(m: "re.Match[str]") -> str:
        run = m.group(0)
        tokens = _MATH_TOKEN_RE.findall(run)
        if len(tokens) < 2:
            return run
        has_digit = any(_MATH_NUMBER_RE.match(t) for t in tokens)
        has_op    = any(t in _MATH_OP_TOKENS for t in tokens)
        # Point/angle/triangle names (AOB, ABC, ...) are pure letter runs: no
        # digit and no operator, so they would otherwise get no bidi isolation.
        # A run of 2+ bare letters (optionally primed, e.g. A') is isolated too.
        is_letter_run = all(_LETTER_TOKEN_RE.match(t) for t in tokens)
        if not (is_letter_run or (has_digit and has_op)):
            return run
        return f"{_LRI}{run}{_PDI}"

    # Skip "$$...$$" blocks: the math renderer lays them out on its own, and the
    # invisible LRI/PDI marks would widen the measured width of the argument
    # (e.g. \widehat{AOB} would render its cap off-centre). Only the surrounding
    # prose gets bidi-isolated.
    parts = _DOLLAR_BLOCK_RE.split(cleaned)
    formulas = _DOLLAR_BLOCK_RE.findall(cleaned)
    out = []
    for i, part in enumerate(parts):
        out.append(_MATH_RUN_RE.sub(_wrap, part))
        if i < len(formulas):
            out.append(formulas[i])
    return "".join(out)


_EXERCISE_HEADER_RE = re.compile(
    r"(?:^|(?<=[.!؟\n]))[ \t]*("
    r"التمرين\s*(?:رقم)?\s*\d+"
    r"|تمرين\s*(?:رقم)?\s*\d+"
    r"|السؤال\s*(?:رقم)?\s*\d+"
    r"|Exercice\s*\d+"
    r"|Question\s*\d+"
    r")[ \t]*:?[ \t]*",
    re.IGNORECASE | re.MULTILINE,
)
_EXPLICIT_FORMULA_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)


def normalize_exercise_layout(text: str) -> str:
    if not text:
        return text
    text = _EXERCISE_HEADER_RE.sub(lambda m: f"\n\n{m.group(1)}\n", text)
    text = _EXPLICIT_FORMULA_RE.sub(lambda m: f"\n\n$${m.group(1).strip()}$$\n\n", text)
    text = re.sub(r"[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip()


# ─────────────────────── LANGUAGE DETECTION ────────────────────

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def detect_language(text: str) -> str:
    arabic_chars = len(_ARABIC_RE.findall(text))
    total_chars  = len(re.findall(r"\S", text))
    if total_chars == 0:
        return "fr"
    return "ar" if arabic_chars / total_chars > 0.3 else "fr"


# ─────── "DIDN'T UNDERSTAND" → FORCE NEW REPRESENTATION ───────

_CONFUSION_PATTERNS = [
    r"ما\s*فهمت", r"لم\s*أفهم", r"مافهمتش", r"ماشي\s*واضح", r"مش\s*واضح",
    r"غير\s*واضح", r"اشرح.*(طريقة|بطريقة)\s*(أخرى|ثانية)", r"ماستوعبتش",
    r"j.?ai\s*pas\s*compris", r"je\s*n.?ai\s*pas\s*compris", r"c.?est\s*pas\s*clair",
    r"pas\s*clair", r"explique.*autrement", r"explique.*différemment",
    r"didn.?t\s*understand", r"don.?t\s*understand", r"not\s*clear",
    r"explain.*differently",
]
_CONFUSION_RE = re.compile("|".join(_CONFUSION_PATTERNS), re.IGNORECASE)


def _is_confusion_followup(question: str) -> bool:
    if not question:
        return False
    return bool(_CONFUSION_RE.search(question))


_FORCE_NEW_REPRESENTATION_NOTE_AR = (
    "\n🔴🔴 التلميذ صرّح أنه لم يفهم الشرح/الرسم السابق — يجب اختيار "
    "\"representation\" مختلفة جوهرياً عن التي استُعملت في الرد السابق (انظر "
    "المحادثة). لا تُعِد استعمال نفس النوع حتى بأرقام مختلفة.\n"
    "🔴🔴 المشكلة على الأرجح في فهم المبدأ نفسه، وليس فقط في شكل الرسم — لذلك "
    "غيّر أيضاً المثال العددي بالكامل: استعمل أرقاماً أصغر وأبسط بكثير من كل "
    "المحاولات السابقة لهذه النقطة (مثلاً مقسوماً أصغر من 10 وقاسماً صغيراً "
    "كـ 2 أو 3)، حتى يسهل على التلميذ متابعة كل خطوة بالعد الذهني. لا تُبقِ "
    "على نفس الأرقام أو أرقاماً بنفس حجمها، حتى لو غيّرت نوع الرسم."
)


# ─────────────── SUB-ITEM COVERAGE CHECKLIST ────────

def _split_top_level_items(text: str) -> List[str]:
    if not text:
        return []
    items: List[str] = []
    buf: List[str] = []
    depth = 0
    for ch in text:
        if ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth = max(0, depth - 1)
            buf.append(ch)
        elif ch in "،,.":
            if depth == 0:
                items.append("".join(buf).strip())
                buf = []
            else:
                buf.append(ch)
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        items.append(tail)
    return [i for i in items if i]


def _build_coverage_checklist(
    lang: str, active_point_text: str, max_items: int | None = None
) -> str:
    items = _split_top_level_items(active_point_text)
    if len(items) <= 1:
        return ""
    # `max_items` caps the checklist to what may be covered this turn, so it does
    # not contradict a per-turn cap set elsewhere (e.g. by the lesson-flow
    # controller). The caller must tell the student the rest comes next turn.
    if max_items is not None and max_items > 0:
        items = items[:max_items]
    numbered = "\n".join(f"  {i+1}. {it}" for i, it in enumerate(items))
    if lang == "ar":
        return (
            "\n\n🔴🔴🔴 [قائمة تغطية إلزامية]\nالنقطة الحالية تحتوي على عدة عناصر:\n"
            f"{numbered}\nيجب شرح كل عنصر، واحداً تلو الآخر، كل عنصر في سطر مستقل "
            "يبدأ بـ \"- \"، ثم اسأل سؤال الفهم مرة واحدة في النهاية.\n"
            "🔴 كل رد يجب أن يحتوي دائماً على الأقسام الثلاثة [WIDGET] و[SPEECH] و[TEACHER] "
            "معاً، ولا يجوز الاكتفاء ببعضها فقط. [WIDGET] مرة واحدة فقط لكامل الرد — كائن "
            "JSON واحد فقط، اختره بعناية ليكون الأنسب لأهم عنصر."
        )
    return (
        "\n\n🔴🔴🔴 [Checklist de couverture obligatoire]\nLe point actuel "
        f"contient plusieurs éléments:\n{numbered}\nExplique chaque élément, "
        "l'un après l'autre, sur sa propre ligne commençant par \"- \", puis "
        "pose la question de compréhension une seule fois à la fin."
    )


# ═════════════════════════════════════════════════════════════════
# CANVAS ENGINE (server-owned HTML/JS that plays the animated SVG steps)
# ═════════════════════════════════════════════════════════════════

_STEPS_JSON_PLACEHOLDER = "@@STEPS_JSON@@"

CANVAS_ENGINE_SHELL = """```interactive_svg
<div style="font-family:sans-serif;padding:10px;background:#fafafa;border-radius:10px">
  <svg id="c" viewBox="0 0 480 300" width="480" height="300"
    style="border:1px solid #e0e0e0;display:block;border-radius:8px;background:#fff"></svg>

  <div style="margin-top:10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <button id="btnPlay"
      style="padding:8px 18px;background:#6350db;color:#fff;border:none;border-radius:8px;cursor:pointer;font-size:14px">
      ▶ تشغيل
    </button>
    <button id="btnPrev"
      style="padding:8px 14px;background:#e0e0e0;border:none;border-radius:8px;cursor:pointer;font-size:14px">
      ◀ السابق
    </button>
    <button id="btnNext"
      style="padding:8px 14px;background:#e0e0e0;border:none;border-radius:8px;cursor:pointer;font-size:14px">
      التالي ▶
    </button>
    <button id="btnReset"
      style="padding:8px 14px;background:#f5f5f5;border:1px solid #ccc;border-radius:8px;cursor:pointer;font-size:13px">
      ↺ إعادة
    </button>
  </div>

  <div id="lbl"
    style="margin-top:10px;padding:8px 12px;background:#f0eeff;border-radius:8px;
           color:#1a1a2e;font-size:14px;min-height:36px;line-height:1.5">
  </div>
</div>

<script>
const SVG_NS = "http://www.w3.org/2000/svg";
const W = 480, H = 300;
const C = { primary:"#6350db", secondary:"#e05c2a", accent:"#2a9e6e",
            aux:"#8b7cf0", text:"#1a1a2e", hi:"#f0c040" };

const svg = document.getElementById('c');
const lbl = document.getElementById('lbl');

function _svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) {
    if (attrs[k] !== undefined && attrs[k] !== null) el.setAttribute(k, attrs[k]);
  }
  return el;
}

const _ARABIC_RUN_RE = /[\u0600-\u06FF]+/g;
const _RLI = "\u2067", _PDI = "\u2069"; // Right-to-Left Isolate / Pop Directional Isolate

// BUGFIX: this used to detect "text contains both Arabic and Latin/digit
// characters" and respond by setting direction:"rtl" on the WHOLE <text>
// node (e.g. for a term like " = {x ∈ ℕ | x يقسم n}"). That's wrong on a
// fixed-coordinate canvas for two reasons: (1) it re-flows the entire
// logical string as one RTL paragraph, reordering the LTR/math sub-runs
// around the Arabic word instead of just shaping that word RTL in place;
// (2) text-anchor="start" is direction-relative, so under direction:rtl
// "start" anchors from the RIGHT edge instead of the left — shifting the
// whole block left, over previously-drawn terms (e.g. "D", "n"), which is
// exactly the overlapping/scrambled render this produced. Canvas text
// should never have its own paragraph direction flipped. Fix: isolate
// only the Arabic run(s) in place with Unicode isolate marks (mirrors
// isolate_inline_math's _LRI/_PDI wrapping in the Python side, just for
// the opposite case — an Arabic run inside an LTR/math string instead of
// an LTR run inside Arabic prose) and leave direction alone entirely.
function _isolateArabicRuns(text) {
  return String(text).replace(_ARABIC_RUN_RE, function(run) {
    return _RLI + run + _PDI;
  });
}

function _clearSvg() {
  while (svg.firstChild) svg.removeChild(svg.firstChild);
}

let _animFrame = null;
function _cancelAnim() {
  if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }
}

function animateLine(x1, y1, x2, y2, style, ms) {
  style = style || {}; ms = ms || 600;
  return new Promise(function(resolve, reject) {
    _cancelAnim();
    const line = _svgEl('line', {
      x1: x1, y1: y1, x2: x1, y2: y1,
      stroke: style.color || C.primary,
      'stroke-width': style.lineWidth || 2.5,
      'stroke-linecap': style.lineCap || 'round',
      'stroke-dasharray': style.dash ? style.dash.join(',') : null
    });
    svg.appendChild(line);
    var start = performance.now();
    function frame(now) {
      try {
        var t  = Math.min((now - start) / ms, 1);
        var ex = x1 + (x2 - x1) * t;
        var ey = y1 + (y2 - y1) * t;
        line.setAttribute('x2', ex);
        line.setAttribute('y2', ey);
        if (t < 1) { _animFrame = requestAnimationFrame(frame); }
        else        { resolve(); }
      } catch (err) { reject(err); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

function _arcPath(cx, cy, r, a0, a1, segments) {
  segments = segments || 48;
  var sweep = a1 - a0;
  var maxSweep = Math.PI * 2;
  if (Math.abs(sweep) > maxSweep) {
    sweep = Math.sign(sweep) * maxSweep;
    a1 = a0 + sweep;
  }
  var d = "";
  for (var i = 0; i <= segments; i++) {
    var t = i / segments;
    var a = a0 + sweep * t;
    var x = cx + r * Math.cos(a);
    var y = cy + r * Math.sin(a);
    d += (i === 0 ? "M " : "L ") + x + " " + y + " ";
  }
  return d.trim();
}

function animateArc(cx, cy, r, startAngle, endAngle, style, ms) {
  style = style || {}; ms = ms || 500; r = Math.max(0, r);
  return new Promise(function(resolve, reject) {
    _cancelAnim();
    const path = _svgEl('path', {
      d: '', fill: 'none', stroke: style.color || C.accent,
      'stroke-width': style.lineWidth || 2, 'stroke-linecap': 'round'
    });
    svg.appendChild(path);
    var start = performance.now();
    var sweep = endAngle - startAngle;
    function frame(now) {
      try {
        var t = Math.min((now - start) / ms, 1);
        if (t > 0) {
          path.setAttribute('d', _arcPath(cx, cy, r, startAngle, startAngle + sweep * t));
        }
        if (t < 1) { _animFrame = requestAnimationFrame(frame); }
        else        { resolve(); }
      } catch (err) { reject(err); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

function animateDot(x, y, style, ms) {
  style = style || {}; ms = ms || 200;
  return new Promise(function(resolve, reject) {
    _cancelAnim();
    var radius = Math.max(0, style.radius || 5);
    const circle = _svgEl('circle', { cx: x, cy: y, r: 0, fill: style.color || C.hi });
    svg.appendChild(circle);
    var start = performance.now();
    function frame(now) {
      try {
        var t  = Math.min((now - start) / ms, 1);
        var cr = Math.max(0, radius * t);
        circle.setAttribute('r', cr);
        if (t < 1) { _animFrame = requestAnimationFrame(frame); }
        else        { resolve(); }
      } catch (err) { reject(err); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

function animateText(text, x, y, style, ms) {
  style = style || {}; ms = ms || 300;
  var fontSize = 14;
  if (style.font) {
    var m = String(style.font).match(/\\d+/);
    if (m) fontSize = m[0];
  }
  return new Promise(function(resolve, reject) {
    _cancelAnim();
    const t = _svgEl('text', {
      x: x, y: y, fill: style.color || C.text, 'font-size': fontSize,
      'font-family': 'sans-serif', 'text-anchor': style.anchor || 'start', opacity: 0
    });
    t.textContent = _isolateArabicRuns(text);
    svg.appendChild(t);
    var start = performance.now();
    function frame(now) {
      try {
        var p = Math.min((now - start) / ms, 1);
        t.setAttribute('opacity', p);
        if (p < 1) { _animFrame = requestAnimationFrame(frame); }
        else        { resolve(); }
      } catch (err) { reject(err); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

const STEPS_DATA = @@STEPS_JSON@@;

function _colorOf(key, fallback) {
  if (key && C[key]) return C[key];
  if (typeof key === 'string' && key) return key;
  return fallback;
}
function _nonNeg(n, fallback) {
  var v = (typeof n === 'number') ? n : fallback;
  return Math.max(0, v);
}

const OP_HANDLERS = {
  line: function(op) {
    return animateLine(op.from[0], op.from[1], op.to[0], op.to[1],
      { color: _colorOf(op.color, C.primary), lineWidth: op.lineWidth || 2.5, dash: op.dash },
      op.ms || 600);
  },
  arc: function(op) {
    return animateArc(op.cx, op.cy, _nonNeg(op.r, 0), op.startAngle, op.endAngle,
      { color: _colorOf(op.color, C.accent), lineWidth: op.lineWidth || 2 },
      op.ms || 500);
  },
  dot: function(op) {
    return animateDot(op.at[0], op.at[1],
      { color: _colorOf(op.color, C.hi), radius: _nonNeg(op.radius, 5) },
      op.ms || 200);
  },
  text: function(op) {
    return animateText(op.text, op.at[0], op.at[1],
      { color: _colorOf(op.color, C.text), font: op.font, anchor: op.anchor }, op.ms || 300);
  }
};

const STEPS = STEPS_DATA.map(function(s) {
  var ops = s.ops || [];
  return {
    label: s.label || "",
    opsCount: ops.length,
    async draw() {
      var ops = s.ops || [];
      for (var i = 0; i < ops.length; i++) {
        var op      = ops[i];
        var handler = OP_HANDLERS[op.type];
        if (!handler) continue;
        try { await handler(op); }
        catch (err) { console.error('[widget] op failed:', op, err); }
      }
    }
  };
});

let current = -1, playing = false, autoTimer = null;
function pauseForStep(step) {
  var n = (step && step.opsCount) ? step.opsCount : 3;
  return Math.min(Math.max(n * 500, 900), 3200);
}
function clearAutoTimer() { if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; } }

async function replayInstant(upTo) {
  _cancelAnim(); _clearSvg();
  for (var i = 0; i <= upTo; i++) {
    try { await STEPS[i].draw(); } catch (err) { console.error('[widget] replay step ' + i + ' failed:', err); }
  }
  lbl.textContent = upTo >= 0 ? STEPS[upTo].label : MSG_START;
}

async function goForward() {
  if (current >= STEPS.length - 1) return;
  current++;
  lbl.textContent = STEPS[current].label;
  try { await STEPS[current].draw(); }
  catch (err) { console.error('[widget] step ' + current + ' failed:', err); }
  if (playing) {
    if (current < STEPS.length - 1) {
      clearAutoTimer();
      autoTimer = setTimeout(function () { if (playing) goForward(); }, pauseForStep(STEPS[current]));
    } else { stopPlay(); }
  }
}

async function goBack() {
  if (current <= 0) {
    current = -1; _cancelAnim(); _clearSvg(); lbl.textContent = MSG_START; return;
  }
  current--;
  await replayInstant(current);
}

function stopPlay() { playing = false; clearAutoTimer(); btnPlay.textContent = "▶ تشغيل"; }

async function startPlay() {
  if (current >= STEPS.length - 1) {
    current = -1; _cancelAnim(); _clearSvg(); lbl.textContent = MSG_START;
  }
  playing = true; btnPlay.textContent = "⏸ إيقاف";
  await goForward();
}

const MSG_START = "اضغط تشغيل أو التالي للبدء";

const btnPlay  = document.getElementById('btnPlay');
const btnNext  = document.getElementById('btnNext');
const btnPrev  = document.getElementById('btnPrev');
const btnReset = document.getElementById('btnReset');

btnPlay.onclick  = function() { playing ? stopPlay() : startPlay(); };
btnNext.onclick  = function() { stopPlay(); goForward(); };
btnPrev.onclick  = function() { stopPlay(); goBack(); };
btnReset.onclick = function() {
  stopPlay(); current = -1; _cancelAnim(); _clearSvg(); lbl.textContent = MSG_START;
};

lbl.textContent = MSG_START;
</script>
```````"""


def render_canvas_widget(steps: list) -> str:
    """
    Fill CANVAS_ENGINE_SHELL with `steps` ([{"label", "ops"}, ...]) produced by
    registry.dispatch.build_widget() (or the freeform_svg fallback). Called by
    llm.py after it has parsed {"representation", "params"} and dispatched to
    the matching layout function.
    """
    import json
    steps_json = json.dumps(steps, ensure_ascii=False)
    return CANVAS_ENGINE_SHELL.replace(_STEPS_JSON_PLACEHOLDER, steps_json)


# Minimal example shown to the LLM ONLY for the rare "freeform_svg" fallback.
# Every other representation gets its coordinates from registry/, so no example
# is needed for them.
CANVAS_STEPS_JSON_EXAMPLE_FREEFORM = """[
  {
    "label": "[AB]",
    "ops": [
      {"type": "line", "from": [100, 150], "to": [380, 150], "color": "primary", "lineWidth": 2.5},
      {"type": "dot", "at": [100, 150], "color": "hi", "radius": 4},
      {"type": "text", "text": "A", "at": [86, 144], "color": "primary"},
      {"type": "dot", "at": [380, 150], "color": "hi", "radius": 4},
      {"type": "text", "text": "B", "at": [388, 144], "color": "primary"},
      {"type": "arc", "cx": 240, "cy": 150, "r": 30, "startAngle": 0, "endAngle": 1.0472, "color": "secondary", "lineWidth": 1.5}
    ]
  }
]"""


# ═════════════════════════════════════════════════════════════════
# [WIDGET] BLOCK (registry-based)
# ═════════════════════════════════════════════════════════════════

def _build_visual_instructions(
    lang: str,
    subject: str = "",
    question: str = "",
    used_representations: list[str] | None = None,
) -> str:
    """
    Build the [WIDGET] instructions: a fixed menu (see registry.build_menu) from
    which the model answers with {"representation", "params"} JSON.

    `used_representations`: representation keys already used in this
    conversation (derived server-side from the history, see
    stream_handler.extract_used_representations()). When non-empty they are
    listed explicitly, except representations that carry a variant
    discriminator, which are designed to be reused.

    `lang` is accepted for call-site compatibility but unused: output is always
    Arabic. The "confusion -> force a new representation" note is merged into
    the already-used note so a turn never gets two near-identical 🔴🔴 blocks.
    """
    # `subject` is forwarded so the menu only offers representations tagged for
    # this lesson's subject (see registry.representations.build_menu()).
    menu = build_menu(lang, subject)
    # The geometry note is gated strictly on subject == "geometry" (not on
    # keywords in the question) so it never suggests representations that
    # build_menu() has filtered out for the current subject. "geometry_generic"
    # is not suggested: it is not a registry key and dispatch.py rejects it.
    geo_note_ar = (
        "🔴 هذا الدرس ذو طابع هندسي — يمكن أن تكون الإجابة الأنسب "
        "compass_construction أو mirror_symmetry أو angle_pair حسب النقطة "
        "المحددة.\n"
        "🔴 إذا كانت النقطة تُطابق أحد أنواع angle_pair (حادة/قائمة/منفرجة/"
        "متتامتان/متكاملتان/متقابلتان بالرأس/متجاورتان)، استعمل \"angle_pair\" "
        "حصراً ولا تستعمل \"freeform_svg\" — حتى حالة عرض ثلاث زوايا متجاورة "
        "معاً في رسم واحد مغطاة الآن بالنوع \"adjacent\"، فاستعمله بدل "
        "freeform_svg. freeform_svg ملاذ أخير فقط لما لا يغطيه angle_pair "
        "إطلاقاً.\n"
        "🔴 إذا كانت النقطة عن المنصف العمودي لقطعة، أو منصف زاوية، أو إنشاء "
        "عمودي بواسطة نقطة (سواء كانت هذه النقطة تنتمي إلى المستقيم أو لا "
        "تنتمي إليه)، استعمل \"compass_construction\" حصراً بالمتغيّر "
        "(\"variant\") المناسب — \"perpendicular_bisector\" أو "
        "\"angle_bisector\" أو \"perpendicular_on_line\" أو "
        "\"perpendicular_external_point\" — ولا تستعمل \"freeform_svg\" "
        "إطلاقاً لأي من هذه الحالات الأربع. إذا كانت النقطة تطلب رسم "
        "العمودي مباشرة بدون خطوات البركار (مجرد مستقيمين متعامدين وعلامة "
        "الزاوية القائمة)، أضف \"method\": \"direct\" إلى \"params\" بدل "
        "التفكير في freeform_svg — كلا الحالتين (بالبركار أو مباشرة) "
        "مغطاتان الآن. freeform_svg ملاذ أخير فقط لما لا يغطيه "
        "compass_construction إطلاقاً.\n"
        "🔴 إذا كانت النقطة تحديداً عن خاصية المنصف العمودي (كل نقطة منه "
        "متساوية البعد عن طرفي القطعة)، وليس عن كيفية إنشائه: استعمل "
        "\"compass_construction\" بالمتغيّر \"perpendicular_bisector\" مع "
        "إضافة \"demo_point_label\" (مثلاً 'P') لإظهار نقطة توضيحية "
        "وقطعتي PA وPB فعلياً — لا تكتفِ بإعادة نفس رسم الإنشاء بدون هذه "
        "الإضافة، فذلك لا يُظهر الخاصية المقصودة من هذه النقطة.\n"
        if subject == "geometry" else ""
    )

    # Representations with a kind/variant discriminator (angle_pair,
    # mirror_symmetry, ...) are meant to be reused once per variant in a lesson,
    # so they are exempt from the "already used" ban. Others are banned once used.
    _VARIANT_CARRYING_REPRESENTATIONS = get_variant_carrying_representations()
    used = [
        r for r in (used_representations or [])
        if r and r not in _VARIANT_CARRYING_REPRESENTATIONS
    ]
    is_confusion = _is_confusion_followup(question)

    if used:
        used_list = ", ".join(dict.fromkeys(used))  # dedupe, preserve order
        already_used_note_ar = (
            f"\n🔴🔴 تنبيه دقيق (وليس تخميناً): تم بالفعل استعمال representation "
            f"التالية في هذه المحادثة: {used_list}. اختر representation مختلفة "
            f"جوهرياً من القائمة أدناه — لا تُعِد استعمال أيّ من القيم أعلاه."
        )
        if is_confusion:
            already_used_note_ar += (
                "\nكما أن التلميذ صرّح أنه لم يفهم المحاولة الأخيرة — تجنّب "
                "تماماً نفس النوع، وليس فقط تفاصيل مختلفة منه.\n"
                "🔴🔴 غيّر أيضاً المثال العددي بالكامل: استعمل أرقاماً أصغر "
                "وأبسط بكثير من كل المحاولات السابقة لهذه النقطة (مثلاً "
                "مقسوماً أصغر من 10 وقاسماً صغيراً كـ 2 أو 3) — المشكلة على "
                "الأرجح في فهم المبدأ نفسه، وتكرار أرقام كبيرة مع رسم مختلف "
                "لن يساعد."
            )
        already_used_note_ar += "\n"
    elif is_confusion:
        already_used_note_ar = _FORCE_NEW_REPRESENTATION_NOTE_AR
    else:
        already_used_note_ar = ""

    return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  تعليمات [WIDGET] — أولوية قصوى                  ║
╚══════════════════════════════════════════════════════╝

🔴 لا تكتب أي إحداثيات (x,y) بنفسك إطلاقاً. اختر فقط "representation" من
   القائمة أدناه المناسبة لهذه النقطة تحديداً، ثم زوّد "params" (أرقام
   وأسماء فقط) — نظام خارجي يتكفل بحساب الرسم الفعلي بدقة.
{already_used_note_ar}
القائمة المتاحة (اختر واحدة فقط، الأنسب لهذه النقطة):
{menu}
{geo_note_ar}
📦 الصيغة الإلزامية لـ [WIDGET] — كائن JSON واحد فقط، لا شيء غيره:
  {{"representation": "<المفتاح>", "params": {{...}}}}

✅ إذا كانت النقطة مفاهيمية بحتة ولا يفيدها أي رسم — اختر "none" مباشرة
   (بلا "params")، هذا خيار صحيح ومتوقّع، وليس فشلاً.

🔴 القماش (canvas) محدود العرض — أي "params" نصية (formula، label، text...)
   يجب أن تبقى مختصرة. عند كتابة معادلة أو علاقة، استعمل رموزاً رياضية
   مختصرة (مثل: المقسوم = القاسم×خارج القسمة+الباقي بصيغتها الرمزية القصيرة
   إن وُجدت، أو استعمل حروفاً مفردة كـ a=b×q+r) بدل كتابة كل كلمة كاملة
   متكررة — النص الطويل جداً يُرفض تلقائياً من النظام الخارجي.

⛔⛔ ممنوع تماماً كتابة أي أمر LaTeX يبدأ بالرمز "\\" (مثل \\times، \\div،
   \\cdot، \\frac{{}}{{}}، \\pm) داخل أي حقل "text" أو "label" أو "formula"
   في [WIDGET]. قاعدة $$...$$ وأوامر LaTeX تخص [TEACHER] فقط ولا علاقة لها
   بـ [WIDGET] إطلاقاً — القماش (canvas) يرسم النص حرفياً كما هو، بدون أي
   محرّك LaTeX، فكتابة "\\times" هناك ستظهر للتلميذ كنص خام "\\times" بدل
   العلامة "×". داخل [WIDGET] استعمل حصراً الرمز اليونيكود الجاهز مباشرة:
   × ÷ · ± ≠ ≤ ≥ ² ³ — لا شيء غير ذلك.

⛔ ممنوع كتابة أي مصفوفة "ops" أو إحداثيات بنفسك — إلا في الحالة النادرة
   جداً حيث تحتاج النقطة فعلاً رسماً ولا يناسبها أي عنصر من القائمة (بما
   فيها "none")، فاستعمل عندها "representation": "freeform_svg" مع
   "params": {{"ops": [...]}}، كملاذ أخير فقط بعد "none":
```json
{CANVAS_STEPS_JSON_EXAMPLE_FREEFORM}
```
🔴 أنواع "type" المسموحة داخل "ops" هي هذه الأربعة فقط: line، arc، dot،
   text. أي نوع آخر (rect، circle، polygon، path، rectangle أو أي شيء
   غيرها) غير موجود في المحرّك وسيُرفض بالكامل. لتمثيل مستطيل أو قطعة
   بطول معيّن استعمل "line" بعرض خط سميك (lineWidth كبير)، وليس "rect".
🔴🔴 كل الإحداثيات داخل "ops" (from، to، at، cx، cy) يجب أن تكون أعداداً
   حرفية نهائية فقط (مثل 265 أو 185.5) — وليست عمليات حسابية غير محسوبة
   مثل "250 + 15" أو "200 - 15". JSON لا يدعم العمليات الحسابية إطلاقاً:
   كتابة "250 + 15" بدل الرقم النهائي 265 تجعل الـ JSON بأكمله غير قابل
   للتحليل (parse) فيُرفض الـ widget كاملاً بدون أي رسالة واضحة للنموذج.
   احسب الناتج النهائي بنفسك قبل كتابته، دائماً.
🔴 لعنصر "arc" استعمل حصراً المفاتيح cx وcy (مركز القوس) وr (نصف القطر) —
   وليس "at" إطلاقاً. كما أن "startAngle" وendAngle" يجب أن تكونا بالراديان
   (radians)، وليس بالدرجات (degrees) — مثال: 1.0472 يعادل 60 درجة.
🆕 كل سؤال جديد = اختيار جديد كلياً، لا تُعِد استعمال نفس params من سؤال سابق.
{color_naming_note_ar(lang)}
"""


def color_naming_note_ar(lang: str) -> str:
    # Kept as a placeholder for call-site compatibility; colour naming in
    # [TEACHER] is handled by _inject_visual_reminder().
    return ""


def _inject_visual_reminder(question: str, lang: str, active_point_text: str = "") -> str:
    """
    Append a short reminder to the user's question: pick one representation from
    the menu, fill params with the real numbers/names, and refer to the visible
    drawing in [TEACHER]. The bidi/formula rules are not repeated here because
    they are already in the system prompt. `lang` is unused (always Arabic).
    """
    reminder = (
        "\n\n[تذكير: اختر representation واحدة من القائمة المتاحة تناسب "
        "هذه النقطة تحديداً، ثم زوّد params بالأرقام/الأسماء الفعلية من "
        "سؤال التلميذ الحالي — بلا إحداثيات. أشِر في [TEACHER] مباشرة "
        "إلى الرسم المرئي ('كما يظهر في الرسم')، وليس بصيغة تخيّل.]"
    )

    if _is_confusion_followup(question):
        reminder += _FORCE_NEW_REPRESENTATION_NOTE_AR

    return question + reminder


# ─────────────────── DUAL OUTPUT INSTRUCTIONS ──────────────────
#
# Each mode's TEACHER-content rules live in a small fragment
# (*_TEACHER_CONTENT_RULES_AR). The full per-mode templates
# (DUAL_OUTPUT_ / SUMMARY_OUTPUT_ / EXERCISE_OUTPUT_INSTRUCTIONS_AR) reference
# those fragments rather than copying them, so _build_dual_output_block() can
# combine any mode combination by appending each present mode's fragment.

# ── explanation / continuation content rules ──
EXPLANATION_TEACHER_CONTENT_RULES_AR = f"""- ⚠️ الرسم في [WIDGET] مرئي للتلميذ في نفس اللحظة التي يقرأ فيها نصّك
- كل الشرح والتعريف يكون هنا فقط، بصوت المعلم — وليس مكتوباً على السبورة
- ✅ يمكن استعمال **نص عريض** وقائمة نقطية "- " فقط. ⛔ ممنوع أي شيء آخر:
  لا عناوين #، لا وسوم HTML، لا كتل كود، لا JSON
- 🔴🔴 بعد آخر عنصر في قائمة نقطية، اترك سطراً فارغاً قبل أي فقرة جديدة
- ⛔ ممنوع "تخيل أنّ لديك..." — الشكل موجود فعلاً ومرسوم
- ✅ أشِر دائماً مباشرة للرسم: "كما يظهر في الرسم أمامك"
- استعمل أسماء النقاط بالضبط كما ستظهر في [WIDGET] (A، B، M...)
{_ANGLE_NAME_NOTATION_RULE_AR}
- ⛔ ممنوع تهجئة الحرف اللاتيني صوتياً بأحرف عربية (اكتب A لا "آي")
- ⛔ ممنوع إضافة الاسم الفرنسي أو أي ترجمة أجنبية للمصطلح بين قوسين
  (مثل "الزاويتان المتجاورتان (Angles adjacents)") — المصطلح بالعربية
  فقط، بدون أي إضافة بلغة أخرى
- اشرح النقطة المحددة فقط، ثم اسأل: "هل فهمت هذه النقطة؟ نكمل للتالية؟" — و"التالية"
  هنا تعني النقطة التالية في الدرس، وليست حالة فرعية ضمن النقطة الحالية نفسها
- 🔴 إذا كانت النقطة الحالية تحتوي عدة حالات/عناصر فرعية، يجب شرحها كلها في هذا
  الرد نفسه قبل طرح سؤال الفهم — ممنوع إنهاء الرد بعد حالة فرعية واحدة فقط
  ثم الانتظار لرد التلميذ لمتابعة الحالات الباقية
{_BIDI_MATH_RULE_AR}
{_FORMULA_DELIMITER_RULE_AR}
- الطول: فقرة قصيرة، ثم قائمة نقطية إن لزم، بدون إطالة غير ضرورية
- 🔴🔴 الإيجاز إلزامي: التلميذ في المرحلة الإعدادية يضيع أمام النص الطويل.
  اكتب أقل ما يكفي لفهم الفكرة.
- 🔴 لا تكرر الفكرة بصيغة أخرى، ولا تلخّص ما قلته للتو، ولا تمهّد بمقدمة طويلة.
  ابدأ مباشرة بالشرح."""

# ── [SPEECH] content rules — a SHORT spoken narration, like a YouTube-
# lesson voiceover playing alongside the on-screen [WIDGET] drawing and
# [TEACHER] text — never a read-out/summary-of-everything in [TEACHER].
_SPEECH_SHARED_RULES_AR = """- هذا النص للنطق الصوتي فقط (TTS) — لن يظهر مكتوباً على الشاشة إطلاقاً
- ⛔ ممنوع أي رمز أو صيغة رياضية بين $$، وممنوع أي Markdown (لا **، لا -،
  لا #) — اكتب كل شيء كجملة عادية تُنطق كما هي حرفياً"""

SPEECH_CONTENT_RULES_EXPLANATION_AR = f"""{_SPEECH_SHARED_RULES_AR}
- 🔴 اكتب 4 إلى 5 جمل قصيرة، بصوت معلّم يتكلم مباشرة (لا يقرأ نصاً) — كأنك
  مُعلِّق صوتي في فيديو تعليمي على يوتيوب: ليست جملة واحدة مقتضبة، وليست
  نسخة كاملة من [TEACHER] — طول متوسط بين الاثنين
- ⛔ ممنوع أن تكون نسخة أو تلخيصاً شبه كامل لنص [TEACHER] — لا تغطِّ كل حالة
  أو كل تفصيل موجود فيه، فقط الأفكار المحورية لهذه النقطة
- 🆕 [WIDGET] و[SPEECH] و[TEACHER] يجب أن يكونا مترابطين، يشرحان نفس الفكرة
  من زوايا مختلفة (الرسم بصرياً، الصوت شفوياً، النص كتابياً) — وليسا نصين
  منفصلين اتفقا بالصدفة على نفس الموضوع
- 🆕 إذا كان [WIDGET] هذا الدور يحتوي رسماً فعلياً (وليس فارغاً)، يجب أن
  يشير [SPEECH] إلى العنصر المحدد الظاهر فيه بالاسم (مثال: "كما ترى في
  الرسم، هذه الزاوية قائمة لأنها تساوي 90 درجة" — وليس مجرد "كما ترى في
  الرسم" بدون تحديد ماذا تحديداً يرى) — لا تفعل هذا إذا كان [WIDGET] فارغاً
  هذا الدور"""

SPEECH_CONTENT_RULES_EXERCISE_AR = f"""{_SPEECH_SHARED_RULES_AR}
- 🔴 جملة واحدة قصيرة فقط: اذكر عدد التمارين التي ستكتبها في [TEACHER] هذا
  الدور (احسبه بنفسك)، وادعُ التلميذ لمحاولة حلها — مثال: "إليك 3 تمارين،
  حاول حلها" أو "إليك تمرين واحد، حاول حلّه"
- ⛔ ممنوع قراءة صياغة أي تمرين أو أي رقم أو معادلة منه هنا — نص التمارين
  الكامل موجود في [TEACHER] فقط، وليس في [SPEECH]"""


def _speech_content_rules_summary_ar(lesson_title: str) -> str:
    lesson_phrase = f"ملخص درس {lesson_title}" if lesson_title else "ملخص هذا الدرس"
    return f"""{_SPEECH_SHARED_RULES_AR}
- 🔴 جملة واحدة قصيرة فقط تقدّم الملخص وفائدته — مثال: "إليك {lesson_phrase}،
  يمكن أن يساعدك على الفهم بشكل أفضل وحل التمارين"
- ⛔ ممنوع قراءة أو تكرار أي نقطة من نقاط الخريطة الذهنية هنا"""

DUAL_OUTPUT_INSTRUCTIONS_AR = f"""
╔══════════════════════════════════════════════════════════════╗
║   📦 تنسيق الإخراج — إلزامي — لا تتجاهله أبداً             ║
╚══════════════════════════════════════════════════════════════╝

يجب أن يكون كل ردّك في ثلاثة أقسام مُعلَّمَة بالضبط، بهذا الترتيب:

[WIDGET]
{{"representation": "...", "params": {{...}}}}
[/WIDGET]

[SPEECH]
...4-5 جمل، بصوت مُعلِّق يشرح نفس فكرة [TEACHER] مع إشارة محددة لما هو مرسوم في [WIDGET] (مثلاً اسم الزاوية أو قيمتها)، وليس ملخصاً كاملاً...
[/SPEECH]

[TEACHER]
...نص المعلم المنطوق...
[/TEACHER]

🔴🔴 كل رد يجب أن يحتوي دائماً على الأقسام الثلاثة معاً، [WIDGET] ثم [SPEECH]
ثم [TEACHER]، بدون استثناء ولا يجوز الاكتفاء ببعضها فقط — حتى لو كان [WIDGET]
فارغاً (اتركه فارغاً بين الوسمين، لا تحذفه)، يجب أن يُختم كل رد بقسم
[TEACHER]...[/TEACHER] فيه كل الشرح المكتوب. لا يجوز أبداً إنهاء الرد مباشرة
بعد [/WIDGET] أو [/SPEECH] بدون القسم التالي كاملاً.

━━━ قواعد [WIDGET] ━━━
- كائن JSON واحد فقط، بالضبط بالصيغة الموضحة في تعليمات [WIDGET] أدناه
- 🆕 كل سؤال جديد = اختيار جديد كلياً خاص بهذا السؤال فقط

━━━ قواعد [SPEECH] — إلزامية تماماً ━━━
{SPEECH_CONTENT_RULES_EXPLANATION_AR}

━━━ قواعد [TEACHER] — إلزامية تماماً ━━━
{EXPLANATION_TEACHER_CONTENT_RULES_AR}
"""

# ── summary content rules ──
SUMMARY_TEACHER_CONTENT_RULES_AR = (
    "- نص قصير يقدّم الملخص (2-3 جمل) فقط، يشير مباشرة إلى الخريطة الذهنية "
    "المرسومة في [WIDGET] (\"كما تلخّصه الخريطة أمامك\")\n"
    "- ⛔ ممنوع تكرار كل نقاط الخريطة نصياً هنا — الخريطة نفسها هي التفصيل"
)

# A function (not a static string) because [SPEECH] names the lesson
# ("إليك ملخص درس <title>...") and the title is only known inside build_prompt().
def _summary_output_instructions_ar(lesson_title: str) -> str:
    return f"""
╔══════════════════════════════════════════════════════════════╗
║   📦 تنسيق الإخراج — تلخيص — إلزامي                        ║
╚══════════════════════════════════════════════════════════════╝

[WIDGET]
...الخريطة الذهنية التفاعلية فقط (```interactive_svg```)...
[/WIDGET]

[SPEECH]
...إليك ملخص هذا الدرس، يمكن أن يساعدك على الفهم بشكل أفضل وحل التمارين...
[/SPEECH]

[TEACHER]
...نص قصير يقدّم الملخص (2-3 جمل)...
[/TEACHER]

🔴🔴 كل رد يجب أن يحتوي دائماً على الأقسام الثلاثة معاً، [WIDGET] ثم [SPEECH]
ثم [TEACHER]، بدون استثناء.

━━━ قواعد [SPEECH] — إلزامية تماماً ━━━
{_speech_content_rules_summary_ar(lesson_title)}

━━━ قواعد [TEACHER] ━━━
{SUMMARY_TEACHER_CONTENT_RULES_AR}
"""

# ── exercise / exercise_answer content rules ──
# NOTE: exercise_answer reuses this fragment even though grading an answer and
# giving a new exercise are different tasks. Fixing it needs a separate grading
# fragment and its own entry in _MODE_TEACHER_CONTENT_AR below.
EXERCISE_TEACHER_CONTENT_RULES_AR = f"""- 🔴 لكل تمرين: سطر "التمرين رقم N" مستقل، ثم الصياغة، ثم معادلة كاملة إن
  وُجدت بين $$، وافصل بين التمارين بسطر فارغ
- وصف هندسي بالكلمات فقط، بدون رسم
{_BIDI_MATH_RULE_AR}
{_FORMULA_DELIMITER_RULE_AR}
- اطلب المحاولة أولاً، لا تعطِ الحل إلا إذا طُلب صراحة"""

EXERCISE_OUTPUT_INSTRUCTIONS_AR = f"""
╔══════════════════════════════════════════════════════════════╗
║   📦 تنسيق الإخراج — تمرين — إلزامي                        ║
╚══════════════════════════════════════════════════════════════╝

[WIDGET]
[/WIDGET]

[SPEECH]
...إليك N تمارين، حاول حلها...
[/SPEECH]

[TEACHER]
...نص التمرين كاملاً...
[/TEACHER]

🔴🔴 كل رد يجب أن يحتوي دائماً على الأقسام الثلاثة معاً، [WIDGET] ثم [SPEECH]
ثم [TEACHER]، حتى لو كان [WIDGET] فارغاً (اتركه فارغاً بين الوسمين، لا تحذفه).

━━━ قواعد [WIDGET] ━━━
- يبقى فارغاً تماماً دائماً — لا JSON، لا representation، لا شيء

━━━ قواعد [SPEECH] — إلزامية تماماً ━━━
{SPEECH_CONTENT_RULES_EXERCISE_AR}

━━━ قواعد [TEACHER] ━━━
{EXERCISE_TEACHER_CONTENT_RULES_AR}
"""

# ── generic combined-mode assembly ──
#
# A combined turn is a primary `mode` plus router-detected `extra_modes`
# (e.g. "summary" + "exercise"). The template chosen by widget_owner only
# described [TEACHER] content for its own mode, so the other modes' content
# rules were dropped and the model sometimes produced untagged output.
#
# Fix: _MODE_TEACHER_CONTENT_AR maps each mode to its content fragment, and
# _build_dual_output_block() starts from widget_owner's template and appends
# the fragment of every other mode in {mode} ∪ extra_modes. A new combination
# needs no new code; a genuinely new mode only needs a new fragment.
_MODE_TEACHER_CONTENT_AR = {
    "explanation":     EXPLANATION_TEACHER_CONTENT_RULES_AR,
    "continuation":    EXPLANATION_TEACHER_CONTENT_RULES_AR,
    "summary":         SUMMARY_TEACHER_CONTENT_RULES_AR,
    "exercise":        EXERCISE_TEACHER_CONTENT_RULES_AR,
    "exercise_answer": EXERCISE_TEACHER_CONTENT_RULES_AR,
}

# Human-readable Arabic labels used only to head each appended section so
# the model can tell the fragments apart when more than one is combined.
_MODE_LABEL_AR = {
    "explanation":     "الشرح",
    "continuation":     "الشرح",
    "summary":         "الملخّص",
    "exercise":        "التمارين",
    "exercise_answer": "تصحيح الإجابة",
}

_WIDGET_OWNER_TEMPLATE_AR = {
    # "summary" is absent on purpose: it needs lesson_title, so it is handled
    # through _summary_output_instructions_ar() instead of a static lookup.
    "exercise":        EXERCISE_OUTPUT_INSTRUCTIONS_AR,
    "exercise_answer": EXERCISE_OUTPUT_INSTRUCTIONS_AR,
    # "explanation"/"continuation"/anything unrecognized fall back to
    # DUAL_OUTPUT_INSTRUCTIONS_AR via .get()'s default below.
}


def _build_dual_output_block(widget_owner: str, mode: str,
                              extra_modes: list[str] | None,
                              lesson_title: str = "") -> str:
    """
    Assemble the [WIDGET]/[TEACHER] format-instruction block from the
    FULL set of modes present this turn ({mode} ∪ extra_modes), not just
    `widget_owner` alone.

    `widget_owner` still decides [WIDGET] itself unchanged (still exactly
    one JSON object / one mindmap / one empty block — see
    resolve_widget_owner()). This function additionally makes sure
    [TEACHER]'s content contract covers EVERY mode present this turn, by
    appending each other mode's content fragment from
    _MODE_TEACHER_CONTENT_AR after widget_owner's own base template.

    On a single-mode turn (extra_modes empty/None, the common case) this
    returns widget_owner's base template completely unchanged — identical
    to the pre-v28.2 single-mode behavior.

    lesson_title: passed through from build_prompt()'s own lesson_title
    local — only actually used when widget_owner == "summary", whose
    [SPEECH] sentence names the lesson (see _summary_output_instructions_ar).
    """
    if widget_owner == "summary":
        base = _summary_output_instructions_ar(lesson_title)
    else:
        base = _WIDGET_OWNER_TEMPLATE_AR.get(widget_owner, DUAL_OUTPUT_INSTRUCTIONS_AR)

    seen = {widget_owner}          # widget_owner's own content is already in `base`
    extras = []
    for m in [mode, *(extra_modes or [])]:
        if m in seen:
            continue
        seen.add(m)
        content = _MODE_TEACHER_CONTENT_AR.get(m)
        if content:                 # unknown mode -> skip rather than crash
            extras.append((m, content))

    if not extras:
        return base

    addendum = (
        "\n\n━━━ إضافة إلزامية — الرسالة تتضمن أكثر من طلب واحد ━━━\n"
        "🔴🔴 هذا الرد يجب أن يغطي كل الطلبات التالية معاً، ضمن نفس وسم "
        "[TEACHER]...[/TEACHER] الواحد، بهذا الترتيب، بدون فتح [TEACHER] "
        "أكثر من مرة ولا كتابة أي نص خارج وسمَي [WIDGET] و[TEACHER]:\n"
    )
    for i, (m, content) in enumerate(extras, start=1):
        label = _MODE_LABEL_AR.get(m, m)
        addendum += f"\n{i}) {label}:\n{content}\n"

    return base + addendum


# ── MINDMAP TEMPLATE ──

def _mindmap_template() -> str:
    return """```interactive_svg
<style>
  .mm-wrap.mirrored svg { transform: scaleX(-1); }
  .mm-wrap.mirrored svg text { transform: scaleX(-1); transform-box: fill-box; transform-origin: center; }
  .mm-topbar { display:flex; justify-content:flex-end; margin-bottom:8px; }
  .mm-topbar button {
    padding:6px 14px; border:1px solid #ddd; border-radius:8px; background:#fff;
    cursor:pointer; font-size:12px; font-family:sans-serif; color:#333;
  }
</style>

<div class="mm-topbar">
  <button id="mmMirrorBtn" type="button">↔ قلب الاتجاه</button>
</div>

<div class="mm-wrap markmap" id="mmWrap"
     style="width:100%;height:440px;border:1px solid #e0e0e0;border-radius:10px;overflow:hidden;background:#fff">
<script type="text/template">
---
markmap:
  colorFreezeLevel: 2
---
# العنوان المركزي
## المحور الأول
- الفكرة الأولى
- الفكرة الثانية
## المحور الثاني
- الفكرة الأولى
- الفكرة الثانية
</script>
</div>

<script src="https://cdn.jsdelivr.net/npm/markmap-autoloader"></script>
<script>
(function () {
  var btn  = document.getElementById('mmMirrorBtn');
  var wrap = document.getElementById('mmWrap');
  if (btn && wrap) {
    btn.addEventListener('click', function () { wrap.classList.toggle('mirrored'); });
  }
})();
</script>
```"""


def _mindmap_instructions(lang: str, lesson_title: str = "") -> str:
    # `lang` is unused (always Arabic); kept for call-site compatibility.
    #
    # The returned block is the LAST thing the model reads before generating, so
    # the literal example below must show the exact output format: the template
    # wrapped in [WIDGET]...[/WIDGET], then [SPEECH], then [TEACHER]. When the
    # example showed a bare ```interactive_svg``` fence, the model copied it
    # without tags and summary turns came back with no [WIDGET]/[TEACHER].
    template = _mindmap_template()
    speech_example = (
        f"إليك ملخص درس {lesson_title}، يمكن أن يساعدك على الفهم بشكل "
        "أفضل وحل التمارين" if lesson_title else
        "إليك ملخص هذا الدرس، يمكن أن يساعدك على الفهم بشكل أفضل وحل التمارين"
    )
    return f"""
تعليمات الخريطة الذهنية (للتلخيص):
- استخدم القالب أدناه — لا قائمة نقاط عادية، ولا SVG يدوي
- عدّل فقط محتوى <script type="text/template">...</script>: "# " للعنوان
  المركزي، "## " لكل محور رئيسي (3-6 محاور)، "- " لكل نقطة فرعية (2-4 لكل محور)
- لا مستوى ثالث (### )، لا جداول، لا روابط
- ضع الكل داخل ```interactive_svg ... ```

╔══════════════════════════════════════════════════════════════╗
║ 🔴🔴🔴 إلزامي — هذا هو الشكل الحرفي الكامل لردّك، من أوله    ║
║ إلى آخره. طابقه تماماً، بما في ذلك وسوم [WIDGET] و[SPEECH]   ║
║ و[TEACHER] أنفسها — هي جزء لا يتجزأ من الرد، وليست مجرد وصف  ║
║ خارجي له.                                                     ║
╚══════════════════════════════════════════════════════════════╝

[WIDGET]
{template}
[/WIDGET]

[SPEECH]
...{speech_example}...
[/SPEECH]

[TEACHER]
...نص قصير (2-3 جمل) يقدّم الملخص، يشير مباشرة إلى الخريطة أعلاه...
[/TEACHER]

⛔⛔ ممنوع منعاً باتاً كتابة القالب (أو أي جزء آخر من الرد) خارج وسوم
[WIDGET]...[/WIDGET] و[SPEECH]...[/SPEECH] و[TEACHER]...[/TEACHER] — وسم
[WIDGET] تحديداً هو ما يجعل الخريطة تُعرض فعلياً للتلميذ؛ إن كُتب القالب
بدونه فلن تظهر أي خريطة على الإطلاق مهما كان محتواها صحيحاً. ابدأ ردّك بـ
[WIDGET] مباشرة، لا بأي مقدمة أو عنوان قبله."""


# ──────────────── COMBINED-MODE WIDGET OWNERSHIP ────────────────
# A turn can combine a primary `mode` with `extra_modes` from router.py (e.g.
# "exercise" + "summary"). Exactly one [WIDGET] is produced per turn; this table
# decides which mode present (mode ∪ extra_modes) owns it. Higher wins.
# "exercise"/"exercise_answer" never win (no widget of their own); they are
# listed so the lookup never raises KeyError.
_WIDGET_PRIORITY = {
    "summary": 3,
    "explanation": 2, "continuation": 2,
    "exercise": 1, "exercise_answer": 1,
}


def resolve_widget_owner(mode: str, extra_modes: list[str] | None) -> str:
    """
    Pick which mode's widget rules apply this turn, from the union of
    {mode} ∪ extra_modes, using _WIDGET_PRIORITY above.

    Defaults to `mode` alone when extra_modes is empty/None, so a
    single-mode turn (the common case) is byte-for-byte unaffected —
    resolve_widget_owner(m, []) == m for every m with an entry above.
    """
    candidates = [mode] + list(extra_modes or [])
    return max(candidates, key=lambda m: _WIDGET_PRIORITY.get(m, 0))


# ─────────────────────── PROMPT BUILDER ────────────────────────

def build_prompt(
    lesson            : dict,
    lang              : str,                      # unused: output is always Arabic (kept for llm.py)
    question          : str = "",
    tc_injection      : str = "",
    mode              : str = "explanation",
    active_point_text : str = "",
    point_idx         : Optional[int] = None,     # unused: kept for llm.py compatibility
    used_representations: list[str] | None = None,  # representation keys already used in this conversation
    max_subpoints_this_turn: int | None = None,   # per-turn cap on sub-items; pass the SAME number
                                                  # used in tc_injection so the checklist matches it
    extra_modes       : list[str] | None = None,  # other modes on a combined turn (router.py);
                                                  # None/[] -> widget_owner == mode
) -> str:
    lesson_title  = (lesson.get("lesson_title")  or "").strip()
    lesson_number = (lesson.get("lesson_number") or "").strip()
    level         = (lesson.get("level")         or "").strip()
    content       = (lesson.get("content")       or "").strip()
    subject       = (lesson.get("subject")       or "").strip().lower()
    # Lesson-specific terminology/phrasing rules, authored in the lesson JSON
    # "keywords" field (see keyword_block below).
    keywords      = lesson.get("keywords") or []

    # [WIDGET] ownership follows widget_owner (the winner of mode ∪ extra_modes);
    # on a single-mode turn widget_owner == mode. Everything else below
    # (is_exercise_like, coverage checklist, lesson_header_ar) keys off `mode`.
    widget_owner = resolve_widget_owner(mode, extra_modes)

    if widget_owner in ("summary", "exercise", "exercise_answer"):
        visual_block = ""
    else:
        # The "confusion -> force new representation" note is merged inside
        # _build_visual_instructions().
        visual_block = _build_visual_instructions(
            lang, subject, question, used_representations=used_representations,
        )

    # widget_owner decides [WIDGET] ownership; extra_modes also extend the
    # [TEACHER] contract so combined asks do not produce untagged output.
    dual_output_block = _build_dual_output_block(
        widget_owner, mode, extra_modes, lesson_title=lesson_title,
    )

    mindmap_block = _mindmap_instructions(lang, lesson_title) if widget_owner == "summary" else ""
    is_exercise_like = mode in ("exercise", "exercise_answer", "summary")
    coverage_checklist = (
        "" if is_exercise_like
        else _build_coverage_checklist(lang, active_point_text, max_subpoints_this_turn)
    )

    lesson_header_ar = (
        "محتوى الدرس المرجعي (لا تشرح منه أي شيء — استعمله فقط كمصدر تكتب منه التمارين):"
        if is_exercise_like else "نقاط الدرس التي سيتعلمها التلميذ:"
    )

    # Language lock: always answer in Arabic. Lesson-specific wording rules (e.g.
    # the "قوة" vs "أس" terminology of the exponents lesson) are not hard-coded
    # here; they live in the lesson's "keywords" field (see keyword_block).
    answer_instructions_ar = """تعليمات الإجابة:
    - أجب بالعربية دائماً حتى لو كان سؤال التلميذ مكتوباً بالفرنسية أو يحتوي كلمات فرنسية
    - اكتب الأُسّ كرقم مرتفع ملتصق (3² لا "3 قوة 2")
    - هذا تعليم إعدادي، ليس ابتدائياً — لا تُطِل تبسيط ما هو واضح لهذا المستوى
    - 🔴 لكل نقطة تشرحها: جملة واحدة للتعريف/الفكرة، ثم مثال واحد فقط إن كان ضرورياً لتوضيحها — لا
    تكرر نفس الفكرة بصياغة ثانية، ولا تضف "بمعنى آخر" أو "بعبارة أخرى" لإعادة قول نفس الشيء
    - 🔴 إذا كانت النقطة تحتوي عدة حالات فرعية (مثل عدة أنواع)، اذكر كل حالة في سطر واحد فقط
    (الاسم + الخاصية الجوهرية)، بدون فقرة منفصلة لكل حالة
    - رتّب التمارين من الأسهل إلى الأصعب إن طُلبت عدة تمارين
    - الحل يُعطى فقط إذا طلبه التلميذ صراحةً"""

    # Per-lesson terminology block; empty when the lesson has no "keywords"
    # (most lessons). Styled like _build_coverage_checklist so it stays visible.
    keyword_block = ""
    if keywords:
        rules = "\n".join(f"🔴 {k}" for k in keywords)
        keyword_block = (
            "\n\n╔══════════════════════════════════════════════════════╗\n"
            "║   🔴🔴🔴 مصطلحات إلزامية خاصة بهذا الدرس تحديداً      ║\n"
            "╚══════════════════════════════════════════════════════╝\n"
            f"{rules}\n"
            "هذه القواعد إلزامية في كل رد متعلق بهذا الدرس، بدون استثناء."
        )

    lesson_info = ""
    if lesson_title:
        lesson_info = f"الدرس {lesson_number}: {lesson_title}" if lesson_number else f"الدرس: {lesson_title}"
        if level:
            lesson_info += f" | المستوى: {level}"
    return f"""أنت مساعد بيداغوجي متخصص في تدريس الرياضيات للمرحلة الإعدادية التونسية.

{dual_output_block}

{visual_block}

{'═' * 50}
{lesson_info}
{'═' * 50}

{lesson_header_ar}
{content}
{keyword_block}

{'═' * 50}
{answer_instructions_ar}

{mindmap_block}{tc_injection}{coverage_checklist}""".strip()