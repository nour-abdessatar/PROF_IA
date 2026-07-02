"""
prompt_builder.py — Construction des prompts 
==================================================================
Contient toute la logique pure de construction de prompt : détection de
langue, détection des labels de points géométriques, détection du contexte
géométrique, sélection du type de widget visuel, les templates HTML/JS/SVG
(canvas, interactive_svg, mindmap), les instructions de format de sortie
(AR/FR), et l'assemblage final du system prompt (build_prompt) ainsi que
l'injection du rappel visuel sur la question (_inject_visual_reminder).


"""

from __future__ import annotations

import os
import re
from typing import List

# ─────────────────────────── SVG PALETTE ───────────────────────

SVG_PALETTE = {
    "primary":   os.environ.get("SVG_PRIMARY",   "#6350db"),
    "secondary": os.environ.get("SVG_SECONDARY", "#e05c2a"),
    "accent":    os.environ.get("SVG_ACCENT",    "#2a9e6e"),
    "auxiliary": os.environ.get("SVG_AUXILIARY", "#8b7cf0"),
    "text":      os.environ.get("SVG_TEXT",      "#1a1a2e"),
    "highlight": os.environ.get("SVG_HIGHLIGHT", "#f0c040"),
}


# ─────────────────────── LANGUAGE DETECTION ────────────────────

_ARABIC_RE = re.compile(r"[\u0600-\u06FF]")


def detect_language(text: str) -> str:
    arabic_chars = len(_ARABIC_RE.findall(text))
    total_chars  = len(re.findall(r"\S", text))
    if total_chars == 0:
        return "fr"
    return "ar" if arabic_chars / total_chars > 0.3 else "fr"


# ─────────────────────── POINT LABEL DETECTION ─────────────────
# Geometry point names are conventionally single uppercase Latin letters,
# optionally primed (A, B, M, O, A', B' ...). We scan both the live question
# and the lesson content so labels mentioned anywhere (e.g. "القطعة AB" or
# "النقطة M") get hard-enforced in the visual reminder, not just labels
# typed verbatim in the student's message.

_POINT_LABEL_RE = re.compile(r"\b([A-Z](?:'|′)?)\b")


def _extract_point_labels(*texts: str) -> List[str]:
    found: List[str] = []
    for t in texts:
        if not t:
            continue
        for m in _POINT_LABEL_RE.finditer(t):
            label = m.group(1)
            if label not in found:
                found.append(label)
    return found


# ─────────────────────── GEOMETRY DETECTION ────────────────────

_GEOMETRY_KEYWORDS = {
    "droite", "segment", "triangle", "rectangle", "carré", "cercle", "polygone",
    "angle", "parallèle", "perpendiculaire", "médiane", "hauteur", "bissectrice",
    "diagonale", "périmètre", "aire", "losange", "trapèze", "milieu", "symétrie",
    "axe", "point", "figure", "géométrie", "tangente", "distance", "vecteur",
    "مستقيم", "قطعة", "مثلث", "مستطيل", "مربع", "دائرة", "مضلع", "زاوية",
    "متوازي", "عمودي", "وسيط", "ارتفاع", "منصف", "قطر", "محيط", "مساحة",
    "معين", "شبه منحرف", "منتصف", "تماثل", "محور", "نقطة", "شكل", "هندسة",
    "مماس", "بعد", "متعامد", "موازٍ", "تناظر", "انعكاس",
}


def _is_geometry_context(subject: str, question: str = "") -> bool:
    if "geometry" in subject.lower() or "هندسة" in subject:
        return True
    q = question.lower()
    return any(kw in q for kw in _GEOMETRY_KEYWORDS)


# ─────────────────────── WIDGET TYPE ───────────────────────────

INTERACTIVE_SVG_LESSONS  = set()
CANVAS_ANIMATION_LESSONS = {"6", "7", "8"}


def _get_visual_type(lesson: dict) -> str:
    num = str(lesson.get("lesson_number", "")).strip()
    if num in INTERACTIVE_SVG_LESSONS:
        return "interactive_svg"
    if num in CANVAS_ANIMATION_LESSONS:
        return "canvas"
    return "svg"


# ─────────────────── DUAL OUTPUT INSTRUCTIONS ──────────────────

DUAL_OUTPUT_INSTRUCTIONS_AR = """
╔══════════════════════════════════════════════════════════════╗
║   📦 تنسيق الإخراج — إلزامي — لا تتجاهله أبداً             ║
╚══════════════════════════════════════════════════════════════╝

يجب أن يكون كل ردّك في قسمين مُعلَّمَين بالضبط، بهذا الترتيب:

[WIDGET]
...كود HTML/JS للـ canvas التفاعلي كاملاً...
[/WIDGET]

[TEACHER]
...نص المعلم المنطوق...
[/TEACHER]

━━━ قواعد [WIDGET] ━━━
• يحتوي على كود الـ canvas التفاعلي كاملاً داخل ```interactive_svg```
• كل step يجب أن يحتوي على ثلاثة حقول: label و voice و draw
• label = النص المعروض أسفل الرسم (يمكن أن يحتوي رموزاً)
• voice = نص قصير وطبيعي للنطق، بدون رموز ولا LaTeX
• draw  = async function تستخدم المساعدات المتحركة فقط — لا تستدعي ctx.stroke() أبداً
• ⚠️ لكل عنصر هندسي (خط/نقطة) تُضيفه بـ animateLine/animateDot، يجب استدعاء
  drawText() لاسمه (A أو B أو M ...) مباشرةً بعد اكتمال الرسم — بدون استثناء.
  أي خط أو نقطة بدون اسم ظاهر = رسم غير مكتمل ومرفوض.
• 🆕 كل سؤال جديد = رسم جديد فارغ تماماً خاص بهذا السؤال فقط. لا تُعِد رسم أو
  تُشِر إلى أي عنصر رُسم في إجابة سابقة، حتى لو وردت إشارة إليه في
  [تعليمات التحكم في التدريس]. تجاهل تماماً أي فقرة "ما هو مرسوم بالفعل" إن
  وُجدت — كل STEPS تبدأ من لوحة فارغة.

━━━ قواعد [TEACHER] — إلزامية تماماً ━━━
• ⚠️ الرسم في [WIDGET] مرئي للتلميذ في نفس اللحظة التي يقرأ فيها نصّك — فهو ليس
  تخيلاً نظرياً بل عنصر حقيقي ظاهر أمامه على الشاشة
• ⛔ ممنوع منعاً باتاً استخدام عبارات مثل "تخيل أنّ لديك..." أو "لنفترض أنّ لديك..."
  أو أي صيغة تطلب من التلميذ تخيّل شكل غير موجود — لأن الشكل موجود فعلاً ومرسوم
• ✅ بدلاً من ذلك، أشِر دائماً إلى الرسم مباشرة بعبارات مثل:
  "كما يظهر في الرسم أمامك"، "لاحظ القطعة AB في الرسم"، "انظر إلى النقطة التي رسمناها"،
  "كما هو موضّح على الشاشة"
• استعمل أسماء النقاط والعناصر بالضبط كما ستظهر في [WIDGET] (مثلاً A و B وM) حتى
  يربط التلميذ بسهولة بين كلامك وما يراه
• اشرح النقطة المحددة في [تعليمات التحكم في التدريس] فقط — نقطة واحدة لا أكثر
• بعد الشرح مباشرةً، اسأل: "هل فهمت هذه النقطة؟ نكمل للتالية؟"
• لا تشرح أي نقطة أخرى في نفس الرد — انتظر موافقة التلميذ
• أسلوب دافئ وطبيعي — كمعلم حقيقي أمام السبورة يشير إلى ما رسمه للتو
• لا رموز رياضية، لا LaTeX، لا "الخطوة 1"، لا "انظر إلى الكود"
• الطول: 3-5 جمل فقط، تنتهي دائماً بـ "هل فهمت هذه النقطة؟ نكمل للتالية؟"
"""

DUAL_OUTPUT_INSTRUCTIONS_FR = """
╔══════════════════════════════════════════════════════════════╗
║   📦 Format de sortie — obligatoire                         ║
╚══════════════════════════════════════════════════════════════╝

Chaque réponse doit contenir exactement deux sections dans cet ordre:

[WIDGET]
...code HTML/JS du canvas interactif complet...
[/WIDGET]

[TEACHER]
...texte parlé du professeur...
[/TEACHER]

━━━ Règles [WIDGET] ━━━
• Code canvas complet dans ```interactive_svg```
• Chaque step doit avoir: label + voice + draw
• label = texte affiché sous le canvas (peut contenir des symboles)
• voice = texte parlé court et naturel, SANS symboles ni LaTeX
• draw  = async function utilisant UNIQUEMENT les helpers animés — jamais ctx.stroke()
• ⚠️ Pour CHAQUE élément géométrique (ligne/point) ajouté via animateLine/animateDot,
  tu DOIS appeler drawText() pour son nom (A, B, M, ...) juste après — sans exception.
  Une ligne ou un point sans nom visible = dessin incomplet et rejeté.
• 🆕 Chaque nouvelle question = un dessin entièrement nouveau et vide, propre à
  cette question uniquement. Ne redessine et ne fais référence à AUCUN élément
  d'une réponse précédente, même si les [Instructions pédagogiques] en parlent.
  Ignore complètement tout paragraphe "déjà tracé" s'il apparaît — chaque STEPS
  démarre d'un tableau vide.

━━━ Règles [TEACHER] — strictement obligatoires ━━━
• ⚠️ Le dessin dans [WIDGET] est visible par l'élève AU MÊME MOMENT qu'il lit ton texte —
  ce n'est pas une figure imaginaire, c'est un élément réel affiché à l'écran
• ⛔ Interdiction stricte d'utiliser des formulations comme "Imagine que tu as..."
  ou "Suppose que tu as..." ou toute phrase demandant à l'élève d'imaginer une figure
  qui n'existe pas — car la figure existe déjà et est dessinée sous ses yeux
• ✅ Réfère-toi toujours directement au dessin avec des phrases comme:
  "Comme tu peux le voir sur le dessin", "Regarde le segment AB à l'écran",
  "Observe le point que nous venons de tracer", "Comme montré ci-contre"
• Utilise les noms exacts des points/éléments tels qu'ils apparaîtront dans [WIDGET]
  (par exemple A, B, M) pour que l'élève relie facilement ton discours à ce qu'il voit
• Explique UNIQUEMENT le point indiqué dans [Instructions pédagogiques] — un seul point
• Après l'explication, demande TOUJOURS: "Tu as compris ce point ? On passe au suivant ?"
• N'explique JAMAIS les points suivants dans la même réponse — attends la confirmation
• Style naturel et chaleureux, comme un professeur devant un tableau qui montre du doigt
  ce qu'il vient de dessiner
• Aucun symbole ni LaTeX — texte oral uniquement, pas de "Étape 1"
• 3-5 phrases maximum, terminer TOUJOURS par "Tu as compris ce point ? On passe au suivant ?"
"""


# ─────────────────────── CANVAS TEMPLATE ───────────────────────
# Worked example swapped to a segment + midpoint + perpendicular case
# (closer to "perpendicular bisector of a segment" lessons) so the model
# transfers the labeling pattern correctly instead of adapting an
# unrelated angle example.

CANVAS_TEMPLATE = """```interactive_svg
<div style="font-family:sans-serif;padding:10px;background:#fafafa;border-radius:10px">
  <canvas id="c" width="480" height="300"
    style="border:1px solid #e0e0e0;display:block;border-radius:8px;background:#fff"></canvas>

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
const W = 480, H = 300;
const C = { primary:"#6350db", secondary:"#e05c2a", accent:"#2a9e6e",
            aux:"#8b7cf0", text:"#1a1a2e", hi:"#f0c040" };

const canvas = document.getElementById('c');
const ctx    = canvas.getContext('2d');
const lbl    = document.getElementById('lbl');

// ── ANIMATION HELPERS ─────────────────────────────────────────
// All draw() functions must use these — never call ctx.stroke() directly.
// Each helper returns a Promise that resolves when the animation completes.

let _animFrame = null;

function _cancelAnim() {
  if (_animFrame) { cancelAnimationFrame(_animFrame); _animFrame = null; }
}

function animateLine(x1, y1, x2, y2, style, ms) {
  style = style || {};
  ms    = ms    || 600;
  return new Promise(function(resolve) {
    _cancelAnim();
    var start = performance.now();
    ctx.save();
    ctx.strokeStyle = style.color     || C.primary;
    ctx.lineWidth   = style.lineWidth || 2.5;
    ctx.lineCap     = style.lineCap   || 'round';
    if (style.dash) ctx.setLineDash(style.dash);

    function frame(now) {
      var t  = Math.min((now - start) / ms, 1);
      var ex = x1 + (x2 - x1) * t;
      var ey = y1 + (y2 - y1) * t;
      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      if (t < 1) { _animFrame = requestAnimationFrame(frame); }
      else        { ctx.restore(); resolve(); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

function animateArc(cx, cy, r, startAngle, endAngle, style, ms) {
  style = style || {};
  ms    = ms    || 500;
  return new Promise(function(resolve) {
    _cancelAnim();
    var start = performance.now();
    var sweep = endAngle - startAngle;
    ctx.save();
    ctx.strokeStyle = style.color     || C.accent;
    ctx.lineWidth   = style.lineWidth || 2;
    ctx.lineCap     = 'round';

    function frame(now) {
      var t = Math.min((now - start) / ms, 1);
      ctx.beginPath();
      ctx.arc(cx, cy, r, startAngle, startAngle + sweep * t);
      ctx.stroke();
      if (t < 1) { _animFrame = requestAnimationFrame(frame); }
      else        { ctx.restore(); resolve(); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

function animateDot(x, y, style, ms) {
  style = style || {};
  ms    = ms    || 200;
  return new Promise(function(resolve) {
    _cancelAnim();
    var start  = performance.now();
    var radius = style.radius || 5;
    ctx.save();
    ctx.fillStyle = style.color || C.hi;

    function frame(now) {
      var t  = Math.min((now - start) / ms, 1);
      var cr = radius * t;
      ctx.beginPath();
      ctx.arc(x, y, cr, 0, Math.PI * 2);
      ctx.fill();
      if (t < 1) { _animFrame = requestAnimationFrame(frame); }
      else        { ctx.restore(); resolve(); }
    }
    _animFrame = requestAnimationFrame(frame);
  });
}

function drawText(text, x, y, style) {
  style = style || {};
  ctx.save();
  ctx.fillStyle = style.color || C.text;
  ctx.font      = style.font  || '14px sans-serif';
  ctx.fillText(text, x, y);
  ctx.restore();
}

// ── STEPS ─────────────────────────────────────────────────────
// LLM RULES for draw():
//   • MUST be async function
//   • Use: await animateLine(x1,y1,x2,y2, {color,lineWidth}, ms)
//   • Use: await animateArc(cx,cy,r, startAngle,endAngle, {color,lineWidth}, ms)
//   • Use: await animateDot(x,y, {color,radius}, ms)
//   • Use: drawText(text,x,y, {color,font})   ← no await, instant
//   • NEVER call ctx.beginPath() / ctx.stroke() / ctx.fill() directly
//   • EVERY line or dot you draw MUST be followed by a drawText() call
//     naming it (A, B, M, ...) — no unlabeled elements, ever.
//   • Default durations: lines=600ms, arcs=500ms, dots=200ms
//   • Adjust ms proportionally to element size (short line → 300ms, long → 900ms)
//   • Every question starts a brand-new, empty canvas. Never reconstruct
//     or reference elements from a previous question/turn — STEPS always
//     begins from a blank board for the current question only.
//
// Worked example below: segment AB with its midpoint M and the
// perpendicular bisector (d) — every point gets an explicit drawText().

const STEPS = [
  {
    label: "الخطوة 1: نرسم القطعة المستقيمة [AB]",
    voice: "نرسم القطعة المستقيمة من A إلى B",
    async draw() {
      await animateLine(100, 150, 380, 150, { color: C.primary, lineWidth: 2.5 });
      await animateDot(100, 150, { color: C.hi, radius: 4 });
      drawText("A", 86, 144, { color: C.primary });
      await animateDot(380, 150, { color: C.hi, radius: 4 });
      drawText("B", 388, 144, { color: C.primary });
    }
  },
  {
    label: "الخطوة 2: نضع منتصف القطعة، النقطة M",
    voice: "نضع نقطة M في منتصف القطعة AB",
    async draw() {
      await animateDot(240, 150, { color: C.secondary, radius: 5 });
      drawText("M", 228, 168, { color: C.secondary });
    }
  },
  {
    label: "الخطوة 3: نرسم المستقيم العمودي (d) المار بـ M",
    voice: "نرسم المستقيم العمودي الذي يمر من النقطة M",
    async draw() {
      await animateLine(240, 60, 240, 240, { color: C.accent, lineWidth: 2.5 });
      drawText("(d)", 248, 58, { color: C.accent });
    }
  },
  {
    label: "✅ (d) هو المنصف العمودي للقطعة [AB]",
    voice: "المستقيم d هو المنصف العمودي للقطعة A B لأنه يمر من منتصفها ويكون عموديا عليها",
    async draw() {}
  }
];

// ── ENGINE ────────────────────────────────────────────────────
let current      = -1;
let playing      = false;
let autoTimer    = null;

function pauseForStep(step) {
  var len = (step && step.voice ? step.voice.length : 40);
  return Math.min(Math.max(len * 45, 900), 3200);
}

function clearAutoTimer() {
  if (autoTimer) { clearTimeout(autoTimer); autoTimer = null; }
}

async function replayInstant(upTo) {
  _cancelAnim();
  ctx.clearRect(0, 0, W, H);
  for (var i = 0; i <= upTo; i++) {
    await STEPS[i].draw();
  }
  lbl.textContent = upTo >= 0 ? STEPS[upTo].label : MSG_START;
}

async function goForward() {
  if (current >= STEPS.length - 1) return;
  current++;
  lbl.textContent = STEPS[current].label;
  await STEPS[current].draw();

  if (playing) {
    if (current < STEPS.length - 1) {
      clearAutoTimer();
      autoTimer = setTimeout(function () {
        if (playing) goForward();
      }, pauseForStep(STEPS[current]));
    } else {
      stopPlay();
    }
  }
}

async function goBack() {
  if (current <= 0) {
    current = -1;
    _cancelAnim();
    ctx.clearRect(0, 0, W, H);
    lbl.textContent = MSG_START;
    return;
  }
  current--;
  await replayInstant(current);
}

function stopPlay() {
  playing = false;
  clearAutoTimer();
  btnPlay.textContent = "▶ تشغيل";
}

async function startPlay() {
  if (current >= STEPS.length - 1) {
    current = -1;
    _cancelAnim();
    ctx.clearRect(0, 0, W, H);
    lbl.textContent = MSG_START;
  }
  playing = true;
  btnPlay.textContent = "⏸ إيقاف";
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
  stopPlay();
  current = -1;
  _cancelAnim();
  ctx.clearRect(0, 0, W, H);
  lbl.textContent = MSG_START;
};

lbl.textContent = MSG_START;
</script>
```"""


# ── INTERACTIVE SVG TEMPLATE ────────────────────────────────────

INTERACTIVE_SVG_TEMPLATE = """```interactive_svg
<style>
  .wrap { font-family:sans-serif; padding:10px; background:#fafafa; border-radius:10px }
  .tabs { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px }
  .tabs button {
    padding:7px 13px; border:none; border-radius:8px; cursor:pointer;
    background:#e8e8e8; color:#333; font-size:13px; transition:.2s
  }
  .tabs button.active { background:#6350db; color:#fff; font-weight:bold }
  canvas { width:100%; border-radius:8px; background:#fff; border:1px solid #e0e0e0; display:block }
  .desc {
    margin-top:10px; padding:8px 12px; background:#f0eeff; border-radius:8px;
    color:#1a1a2e; font-size:14px; min-height:36px
  }
</style>

<div class="wrap">
  <div class="tabs" id="tabs"></div>
  <canvas id="c" width="480" height="260"></canvas>
  <div class="desc" id="desc"></div>
</div>

<script>
const C = { primary:"#6350db", secondary:"#e05c2a", accent:"#2a9e6e",
            aux:"#8b7cf0", text:"#1a1a2e", hi:"#f0c040" };
const W = 480, H = 260;

const ANGLES = [
  {
    label: "حادة",
    desc: "الزاوية الحادة: قياسها بين 0° و 90°",
    audioIdx: 0,
    steps: [
      ctx => {
        ctx.beginPath(); ctx.arc(100,160,5,0,Math.PI*2);
        ctx.fillStyle=C.hi; ctx.fill();
        ctx.fillStyle=C.text; ctx.font="14px sans-serif"; ctx.fillText("O",86,156);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(100,160); ctx.lineTo(340,160);
        ctx.strokeStyle=C.primary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.primary; ctx.fillText("A",348,164);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(100,160); ctx.lineTo(240,70);
        ctx.strokeStyle=C.secondary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.secondary; ctx.fillText("B",243,64);
      },
      ctx => {
        ctx.beginPath(); ctx.arc(100,160,40,-Math.PI*0.52,0);
        ctx.strokeStyle=C.accent; ctx.lineWidth=2; ctx.stroke();
        ctx.fillStyle=C.accent; ctx.font="12px sans-serif"; ctx.fillText("45°",148,148);
      }
    ]
  },
  {
    label: "قائمة",
    desc: "الزاوية القائمة: قياسها = 90° تماماً — نرمز لها بمربع صغير عند الرأس",
    audioIdx: 1,
    steps: [
      ctx => {
        ctx.beginPath(); ctx.arc(100,200,5,0,Math.PI*2);
        ctx.fillStyle=C.hi; ctx.fill();
        ctx.fillStyle=C.text; ctx.font="14px sans-serif"; ctx.fillText("O",86,196);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(100,200); ctx.lineTo(340,200);
        ctx.strokeStyle=C.primary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.primary; ctx.fillText("A",348,204);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(100,200); ctx.lineTo(100,60);
        ctx.strokeStyle=C.secondary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.secondary; ctx.fillText("B",86,55);
      },
      ctx => {
        ctx.strokeStyle=C.accent; ctx.lineWidth=2;
        ctx.strokeRect(100,180,20,20);
        ctx.fillStyle=C.accent; ctx.font="12px sans-serif"; ctx.fillText("90°",126,192);
      }
    ]
  },
  {
    label: "منفرجة",
    desc: "الزاوية المنفرجة: قياسها بين 90° و 180°",
    audioIdx: 2,
    steps: [
      ctx => {
        ctx.beginPath(); ctx.arc(240,180,5,0,Math.PI*2);
        ctx.fillStyle=C.hi; ctx.fill();
        ctx.fillStyle=C.text; ctx.font="14px sans-serif"; ctx.fillText("O",226,176);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(240,180); ctx.lineTo(420,180);
        ctx.strokeStyle=C.primary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.primary; ctx.fillText("A",428,184);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(240,180); ctx.lineTo(80,90);
        ctx.strokeStyle=C.secondary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.secondary; ctx.fillText("B",64,85);
      },
      ctx => {
        ctx.beginPath(); ctx.arc(240,180,40,Math.PI,Math.PI+Math.PI*0.72);
        ctx.strokeStyle=C.accent; ctx.lineWidth=2; ctx.stroke();
        ctx.fillStyle=C.accent; ctx.font="12px sans-serif"; ctx.fillText("120°",188,152);
      }
    ]
  },
  {
    label: "متتامتان",
    desc: "زاويتان متتامتان: مجموعهما = 90° — كل منهما تُكمل الأخرى لتُشكّلا زاوية قائمة",
    audioIdx: 3,
    steps: [
      ctx => {
        ctx.beginPath(); ctx.arc(100,200,5,0,Math.PI*2);
        ctx.fillStyle=C.hi; ctx.fill();
        ctx.fillStyle=C.text; ctx.font="14px sans-serif"; ctx.fillText("O",86,196);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(100,200); ctx.lineTo(340,200);
        ctx.strokeStyle=C.primary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.primary; ctx.fillText("A",348,204);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(100,200); ctx.lineTo(100,60);
        ctx.strokeStyle=C.secondary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.secondary; ctx.fillText("B",86,55);
      },
      ctx => {
        ctx.save(); ctx.setLineDash([6,3]);
        ctx.beginPath(); ctx.moveTo(100,200); ctx.lineTo(220,90);
        ctx.strokeStyle=C.aux; ctx.lineWidth=2; ctx.stroke();
        ctx.restore();
        ctx.fillStyle=C.aux; ctx.fillText("C",224,84);
      },
      ctx => {
        ctx.beginPath(); ctx.arc(100,200,35,-Math.PI/2,-Math.PI*0.52);
        ctx.strokeStyle=C.accent; ctx.lineWidth=2; ctx.stroke();
        ctx.beginPath(); ctx.arc(100,200,35,-Math.PI*0.52,0);
        ctx.strokeStyle=C.hi; ctx.lineWidth=2; ctx.stroke();
        ctx.strokeStyle=C.accent; ctx.strokeRect(100,180,20,20);
        ctx.fillStyle=C.accent; ctx.font="11px sans-serif"; ctx.fillText("α",138,196);
        ctx.fillStyle=C.hi; ctx.fillText("β",96,162);
        ctx.fillStyle=C.text; ctx.font="13px sans-serif";
        ctx.textAlign="center"; ctx.fillText("α + β = 90°",200,240); ctx.textAlign="left";
      }
    ]
  },
  {
    label: "متكاملتان",
    desc: "زاويتان متكاملتان: مجموعهما = 180° — تُشكّلان معاً زاوية مستقيمة",
    audioIdx: 4,
    steps: [
      ctx => {
        ctx.beginPath(); ctx.arc(240,160,5,0,Math.PI*2);
        ctx.fillStyle=C.hi; ctx.fill();
        ctx.fillStyle=C.text; ctx.font="14px sans-serif"; ctx.fillText("O",226,156);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(60,160); ctx.lineTo(420,160);
        ctx.strokeStyle=C.primary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.primary; ctx.fillText("A",428,164); ctx.fillText("A'",42,164);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(240,160); ctx.lineTo(340,70);
        ctx.strokeStyle=C.secondary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.secondary; ctx.fillText("B",344,64);
      },
      ctx => {
        ctx.beginPath(); ctx.arc(240,160,35,Math.PI,Math.PI+Math.PI*0.62);
        ctx.strokeStyle=C.accent; ctx.lineWidth=2; ctx.stroke();
        ctx.beginPath(); ctx.arc(240,160,35,-Math.PI*0.62,0);
        ctx.strokeStyle=C.hi; ctx.lineWidth=2; ctx.stroke();
        ctx.fillStyle=C.accent; ctx.font="11px sans-serif"; ctx.fillText("α",194,148);
        ctx.fillStyle=C.hi; ctx.fillText("β",278,146);
        ctx.fillStyle=C.text; ctx.font="13px sans-serif";
        ctx.textAlign="center"; ctx.fillText("α + β = 180°",240,230); ctx.textAlign="left";
      }
    ]
  },
  {
    label: "متقابلتان",
    desc: "الزاويتان المتقابلتان بالرأس: تتشكّلان عند تقاطع مستقيمين — هما متساويتان دائماً",
    audioIdx: 5,
    steps: [
      ctx => {
        ctx.beginPath(); ctx.arc(240,150,5,0,Math.PI*2);
        ctx.fillStyle=C.hi; ctx.fill();
        ctx.fillStyle=C.text; ctx.font="14px sans-serif"; ctx.fillText("O",226,145);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(60,230); ctx.lineTo(420,70);
        ctx.strokeStyle=C.primary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.primary; ctx.fillText("B",428,64); ctx.fillText("B'",42,240);
      },
      ctx => {
        ctx.beginPath(); ctx.moveTo(60,70); ctx.lineTo(420,230);
        ctx.strokeStyle=C.secondary; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.secondary; ctx.fillText("A",428,238); ctx.fillText("A'",42,68);
      },
      ctx => {
        ctx.beginPath(); ctx.arc(240,150,28,-0.6,0.6);
        ctx.strokeStyle=C.accent; ctx.lineWidth=2.5; ctx.stroke();
        ctx.beginPath(); ctx.arc(240,150,28,Math.PI-0.6,Math.PI+0.6);
        ctx.strokeStyle=C.accent; ctx.lineWidth=2.5; ctx.stroke();
        ctx.fillStyle=C.accent; ctx.font="11px sans-serif";
        ctx.fillText("α",272,148); ctx.fillText("α",200,155);
        ctx.fillStyle=C.text; ctx.font="13px sans-serif";
        ctx.textAlign="center"; ctx.fillText("α = α'  (متقابلتان بالرأس)",240,240); ctx.textAlign="left";
      }
    ]
  }
];

const canvas  = document.getElementById('c');
const ctx     = canvas.getContext('2d');
const tabsEl  = document.getElementById('tabs');
const descEl  = document.getElementById('desc');

function clearCanvas() { ctx.clearRect(0, 0, W, H); }

function drawUpTo(angleIdx, stepIdx) {
  clearCanvas();
  var steps = ANGLES[angleIdx].steps;
  for (var i = 0; i <= stepIdx; i++) steps[i](ctx);
}

function animateAngle(idx) {
  if (animTimer) { clearInterval(animTimer); animTimer = null; }
  currentAngle = idx;
  var steps = ANGLES[idx].steps;
  var step  = -1;
  clearCanvas();
  animTimer = setInterval(function() {
    step++;
    if (step >= steps.length) { clearInterval(animTimer); animTimer = null; return; }
    drawUpTo(idx, step);
  }, 500);
}

function show(i) {
  tabsEl.querySelectorAll('button').forEach(function(b, j) { b.classList.toggle('active', j === i); });
  descEl.textContent = ANGLES[i].desc;
  animateAngle(i);
}

ANGLES.forEach(function(a, i) {
  var b = document.createElement('button');
  b.textContent = a.label;
  b.onclick = function() { show(i); };
  tabsEl.appendChild(b);
});

show(0);
</script>
```"""


# ── MINDMAP TEMPLATE (markmap-based) ─────────────────────────────
# v13.0 — Swapped the hand-rolled SVG mindmap engine for markmap
# (https://markmap.js.org, MIT license, loaded from CDN, no backend).
# The LLM no longer writes any coordinates, colors, or animation code —
# it only writes a markdown outline (# root / ## branch / - point).
# Markmap's own engine handles layout, zoom, pan, and collapse/expand
# for free. A mirror toggle is included so Arabic users can flip the
# tree to grow rightward if they prefer that over the LTR default.

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
- الفكرة الثالثة
## المحور الثاني
- الفكرة الأولى
- الفكرة الثانية
## المحور الثالث
- الفكرة الأولى
- الفكرة الثانية
- الفكرة الثالثة
## المحور الرابع
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
    btn.addEventListener('click', function () {
      wrap.classList.toggle('mirrored');
    });
  }
})();
</script>
```"""


def _build_visual_instructions(visual_type: str, lang: str) -> str:
    p = SVG_PALETTE

    if visual_type == "canvas":
        if lang == "ar":
            return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  تعليمات الرسم المتحرك — أولوية قصوى            ║
╚══════════════════════════════════════════════════════╝

🔴 قاعدة صارمة: في قسم [WIDGET]، يجب أن تُدرج رسماً تفاعلياً متحركاً.
   لا يُقبل شرح بدون رسم — حتى لو كان السؤال بسيطاً.

الرسم المطلوب: Canvas تفاعلي بخطوات تُرسم بتدرج كمعلم على السبورة
ضع الكود داخل ```interactive_svg ... ``` (هذا الاسم إلزامي للـ fence)

🔵 نظام الخطوات التراكمية (إلزامي):
  • كل سؤال جديد يبدأ برسم فارغ تماماً خاص به فقط — لا علاقة له بأي سؤال سابق
  • كل ضغطة على "التالي" تُضيف عنصراً جديداً مع انيميشن، ضمن نفس السؤال الحالي
  • زر "تشغيل تلقائي" يقوم بكل الخطوات تلقائياً
  • زر "إعادة" يمسح كل شيء ويعود للبداية

🎨 قواعد الرسم المتحرك — إلزامية تماماً:
  • كل draw() يجب أن تكون async function
  • لرسم خط مستقيم:  await animateLine(x1, y1, x2, y2, {{color, lineWidth}}, ms)
  • لرسم قوس/دائرة: await animateArc(cx, cy, r, startAngle, endAngle, {{color, lineWidth}}, ms)
  • لرسم نقطة:       await animateDot(x, y, {{color, radius}}, ms)
  • للنصوص:          drawText(text, x, y, {{color, font}})   ← فوري بدون await
  • ⛔ لا تستدعي ctx.beginPath() أو ctx.stroke() أو ctx.fill() مباشرةً أبداً
  • 🔴 كل خط أو نقطة يجب أن تُتبَع مباشرةً بـ drawText() لاسمها — بدون استثناء
  • 🆕 لا تُعِد رسم أو تشِر إلى أي عنصر من سؤال سابق، حتى لو ورد ذكره في
    [تعليمات التحكم في التدريس] — تجاهل أي إشارة من هذا النوع تماماً
  • المدد الافتراضية: خطوط=600ms | أقواس=500ms | نقاط=200ms
  • اضبط ms حسب طول العنصر: خط قصير→300ms | خط طويل→900ms

🎙️ حقل voice إلزامي في كل خطوة:
  label : النص المعروض أسفل الرسم (يمكن أن يحتوي رموزاً)
  voice : النص المنطوق — جملة طبيعية قصيرة بدون رموز ولا LaTeX، ويجب أن تشير
          إلى ما يحدث على الرسم في تلك اللحظة بالذات (مثال: "نضع الآن نقطة A هنا")
  draw  : async function تستخدم المساعدات المتحركة فقط

ألوان إلزامية (من كائن C في المحرك):
  • الشكل الرئيسي:    color: C.primary    ← "{p['primary']}"
  • العنصر الثاني:   color: C.secondary  ← "{p['secondary']}"
  • العنصر الثالث:   color: C.accent     ← "{p['accent']}"
  • الخطوط المساعدة:  color: C.aux        ← "{p['auxiliary']}"
  • النقاط:           color: C.hi         ← "{p['highlight']}"
  • النصوص:           color: C.text       ← "{p['text']}"

النموذج الكامل الذي يجب اتباعه (ADAPT الـ STEPS فقط، لا تغيّر المحرك أبداً):
{CANVAS_TEMPLATE}
"""
        else:
            return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  Instructions visuelles animées — Priorité absolue ║
╚══════════════════════════════════════════════════════╝

🔴 Règle stricte: dans [WIDGET], tu DOIS inclure un dessin interactif animé.

Visuel requis: Canvas interactif, dessin étape par étape comme au tableau
Place le code dans ```interactive_svg ... ``` (nom de fence obligatoire)

🔵 Système d'étapes cumulatives (obligatoire):
  • Chaque nouvelle question démarre avec un dessin totalement vide qui lui
    est propre — sans lien avec une question précédente
  • Chaque clic sur "Suivant" ajoute UN élément avec animation, au sein de
    la question en cours
  • "Lecture auto" enchaîne toutes les étapes automatiquement
  • "Réinitialiser" efface tout et revient au début

🎨 Règles de dessin animé — strictement obligatoires:
  • Chaque draw() doit être une async function
  • Tracer une ligne:   await animateLine(x1, y1, x2, y2, {{color, lineWidth}}, ms)
  • Tracer un arc:      await animateArc(cx, cy, r, startAngle, endAngle, {{color, lineWidth}}, ms)
  • Placer un point:    await animateDot(x, y, {{color, radius}}, ms)
  • Texte (instantané): drawText(text, x, y, {{color, font}})   ← sans await
  • ⛔ Ne jamais appeler ctx.beginPath() / ctx.stroke() / ctx.fill() directement
  • 🔴 Chaque ligne ou point doit être immédiatement suivi d'un drawText() pour
    son nom — sans exception
  • 🆕 Ne redessine et ne fais référence à AUCUN élément d'une question
    précédente, même si les [Instructions pédagogiques] en parlent — ignore
    complètement toute mention de ce type
  • Durées par défaut: lignes=600ms | arcs=500ms | points=200ms
  • Ajuste ms selon la taille: ligne courte→300ms | ligne longue→900ms

🎙️ Champ voice obligatoire dans chaque step:
  label : texte affiché sous le canvas (peut contenir des symboles)
  voice : texte parlé — phrase courte et naturelle, SANS symboles ni LaTeX, et qui doit
          se référer à ce qui se passe sur le dessin à ce moment précis
          (exemple: "On place maintenant le point A ici")
  draw  : async function utilisant uniquement les helpers animés

Couleurs obligatoires (objet C du moteur):
  • Forme principale:   color: C.primary    ← "{p['primary']}"
  • Deuxième élément:  color: C.secondary  ← "{p['secondary']}"
  • Troisième élément: color: C.accent     ← "{p['accent']}"
  • Lignes auxiliaires: color: C.aux        ← "{p['auxiliary']}"
  • Points:            color: C.hi         ← "{p['highlight']}"
  • Textes:            color: C.text       ← "{p['text']}"

Modèle complet à suivre (adapte UNIQUEMENT le tableau STEPS, ne touche jamais au moteur):
{CANVAS_TEMPLATE}
"""

    if visual_type == "interactive_svg":
        if lang == "ar":
            return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  تعليمات الرسم — أولوية قصوى — غير قابلة للتجاهل  ║
╚══════════════════════════════════════════════════════╝

🔴 قاعدة صارمة: في قسم [WIDGET]، يجب أن تُدرج SVG تفاعلي بأزرار تبويب.

النموذج الكامل الذي يجب اتباعه (ADAPT الـ ANGLES فقط حسب الدرس):
{INTERACTIVE_SVG_TEMPLATE}
"""
        else:
            return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  Instructions visuelles — Priorité absolue      ║
╚══════════════════════════════════════════════════════╝

🔴 Règle stricte: dans [WIDGET], tu DOIS inclure un SVG interactif avec onglets.

Modèle complet à suivre (adapte uniquement le tableau ANGLES selon le cours):
{INTERACTIVE_SVG_TEMPLATE}
"""

    # static SVG
    if lang == "ar":
        if _is_geometry_context("geometry"):
            return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  تعليمات الرسم — أولوية قصوى                   ║
╚══════════════════════════════════════════════════════╝

🔴 قاعدة: مع كل نقطة هندسية تشرحها في [WIDGET]، أضف رسم SVG مباشرة بعدها.
ضع كود SVG داخل ```svg ... ```

ألوان إلزامية:
  • stroke="{p['primary']}" | stroke="{p['secondary']}" | stroke="{p['accent']}"
  • stroke="{p['auxiliary']}" stroke-dasharray="6,3"
  • fill="{p['highlight']}" | fill="{p['text']}"
  • viewBox="0 0 300 300" | font-size="13" font-family="sans-serif"
"""
        return f"""
تعليمات الرسم (SVG):
- عند إعطاء تمرين يتضمن أشكالاً هندسية في [WIDGET]: أضف رسم SVG توضيحي
- ضع كود SVG داخل ```svg ... ```
- ألوان إلزامية: stroke="{p['primary']}" | stroke="{p['secondary']}" | stroke="{p['accent']}"
"""
    else:
        if _is_geometry_context("geometry"):
            return f"""
╔══════════════════════════════════════════════════════╗
║   ⚠️  Instructions visuelles — Priorité absolue      ║
╚══════════════════════════════════════════════════════╝

🔴 Règle: après chaque point géométrique dans [WIDGET], place immédiatement un SVG.
Place le code SVG dans ```svg ... ```

Couleurs: stroke="{p['primary']}" | stroke="{p['secondary']}" | stroke="{p['accent']}"
viewBox="0 0 300 300" | font-size="13" font-family="sans-serif"
"""
        return f"""
Instructions dessin (SVG):
- Si l'exercice dans [WIDGET] contient des figures géométriques: ajoute un SVG
- Place le code SVG dans ```svg ... ```
- Couleurs: stroke="{p['primary']}" | stroke="{p['secondary']}" | stroke="{p['accent']}"
"""


def _inject_visual_reminder(
    question      : str,
    visual_type   : str,
    lang          : str,
    lesson_content: str = "",
) -> str:
    labels = _extract_point_labels(question, lesson_content)

    if visual_type == "canvas":
        visual_reminder = (
            "\n\n[تذكير إلزامي: هذا سؤال جديد → ابدأ من رسم فارغ تماماً، لا تُعِد رسم أو تُشِر إلى أي "
            "عنصر من سؤال سابق. ضع رسماً Canvas متحركاً كاملاً داخل ```interactive_svg``` في [WIDGET] — "
            "كل draw() يجب أن تكون async وتستخدم animateLine/animateArc/animateDot فقط — لا ctx.stroke() أبداً — "
            "و[TEACHER] يجب أن يشير مباشرة إلى الرسم المرئي ('كما يظهر في الرسم'، 'لاحظ القطعة AB') "
            "وليس بصيغة تخيّل ('تخيل أن لديك...') لأن التلميذ يرى الرسم فعلياً أمامه، "
            "ويحتوي على النقطة المحددة في التعليمات فقط ثم سؤال 'هل فهمت؟ نكمل للتالية؟']"
            if lang == "ar" else
            "\n\n[Rappel obligatoire: nouvelle question → repars d'un dessin totalement vide, ne redessine "
            "et ne fais référence à aucun élément d'une question précédente. Inclure une animation Canvas "
            "dans ```interactive_svg``` dans [WIDGET] — chaque draw() doit être async et utiliser "
            "animateLine/animateArc/animateDot uniquement — jamais ctx.stroke() — "
            "[TEACHER] doit se référer directement au dessin visible ('comme tu peux le voir sur le dessin', "
            "'regarde le segment AB') et non en mode hypothétique ('imagine que tu as...') car l'élève voit "
            "réellement le dessin, puis expliquer UNIQUEMENT le point indiqué puis demander "
            "'Tu as compris ? On passe au suivant ?']"
        )
    elif visual_type == "interactive_svg":
        visual_reminder = (
            "\n\n[تذكير إلزامي: ضع SVG تفاعلي بأزرار تبويب في [WIDGET] — "
            "وفي [TEACHER] أشِر مباشرة إلى الرسم المرئي وليس بصيغة تخيّل، اشرح النقطة المحددة فقط "
            "ثم اسأل 'هل فهمت؟ نكمل للتالية؟']"
            if lang == "ar" else
            "\n\n[Rappel: SVG interactif avec onglets dans [WIDGET] — "
            "[TEACHER] doit se référer directement au dessin visible (pas en mode hypothétique), "
            "point indiqué uniquement puis 'Tu as compris ? On passe au suivant ?']"
        )
    else:
        visual_reminder = (
            "\n\n[تذكير: أي رسم SVG داخل [WIDGET] — وفي [TEACHER] أشِر مباشرة إلى الرسم المرئي وليس بصيغة "
            "تخيّل، النقطة المحددة في التعليمات فقط ثم 'هل فهمت؟ نكمل؟']"
            if lang == "ar" else
            "\n\n[Rappel: SVG dans [WIDGET] — [TEACHER] doit se référer directement au dessin visible "
            "(pas en mode hypothétique), point indiqué uniquement puis 'Tu as compris ?']"
        )

    # Hard-list the exact point names that must be labeled, if any were found.
    # This is the direct fix for "canvas missing label A, B, M".
    if labels:
        label_list_ar = "، ".join(labels)
        label_list_fr = ", ".join(labels)
        label_clause = (
            f"\n[إلزامي صارم: هذا الرسم يخص نقاطاً اسمها {label_list_ar} — يجب أن يحتوي الرسم على "
            f"drawText() واحدة على الأقل لكل نقطة من هذه القائمة، مباشرة بعد رسمها. أي رسم لخط أو نقطة "
            f"من هذه القائمة بدون استدعاء drawText() لاسمها = رسم مرفوض وغير مكتمل.]"
            if lang == "ar" else
            f"\n[Obligatoire et strict: ce dessin concerne les points {label_list_fr} — le dessin DOIT "
            f"contenir au moins un drawText() pour chacun de ces points, juste après son tracé. Toute "
            f"ligne ou point de cette liste sans drawText() de son nom = dessin rejeté et incomplet.]"
        )
        visual_reminder += label_clause

    return question + visual_reminder


# ─────────────────────── PROMPT BUILDER ────────────────────────

def build_prompt(
    lesson      : dict,
    lang        : str,
    question    : str = "",
    tc_injection: str = "",
) -> str:
    lesson_title  = (lesson.get("lesson_title")  or "").strip()
    lesson_number = (lesson.get("lesson_number") or "").strip()
    level         = (lesson.get("level")         or "").strip()
    content       = (lesson.get("content")       or "").strip()
    subject       = (lesson.get("subject")       or "").strip().lower()

    visual_type       = _get_visual_type(lesson)
    visual_block      = _build_visual_instructions(visual_type, lang)
    dual_output_block = (
        DUAL_OUTPUT_INSTRUCTIONS_AR if lang == "ar"
        else DUAL_OUTPUT_INSTRUCTIONS_FR
    )

    mindmap_template = _mindmap_template()

    mindmap_instruction_ar = f"""
تعليمات الخريطة الذهنية (للتلخيص):
- عند طلب تلخيص أو ملخص للدرس: استخدم قالب الخريطة الذهنية أدناه — لا تعطِ قائمة
  نقاط عادية أبداً، ولا ترسم خريطة ذهنية SVG يدوياً بنفسك أبداً
- 🔴 قاعدة صارمة جداً: هذا القالب لا يُبنى بإحداثيات أو ألوان أو كود — بل بنص Markdown
  فقط. المحرك (مكتبة خارجية جاهزة) يتكفل تلقائياً بكل الرسم والتحريك والتكبير والطي
- عدّل فقط المحتوى الموجود داخل <script type="text/template"> ... </script> — لا شيء
  آخر في القالب (لا الأنماط CSS ولا الأزرار ولا وسم <script> الخاص بـ markmap-autoloader)
- التزم بنفس بنية الـ Markdown تماماً:
    • السطر الأول بعد --- markmap: ... --- يبدأ بـ "# " ثم عنوان الدرس أو الموضوع
      (جملة قصيرة واحدة) — هذا هو المركز
    • كل محور رئيسي يبدأ بـ "## " (من 3 إلى 6 محاور) — عنوان قصير من كلمتين إلى
      ست كلمات
    • تحت كل محور، ضع من نقطتين إلى أربع نقاط فرعية، كل نقطة تبدأ بـ "- " وتكون
      من ثلاث إلى اثنتي عشرة كلمة — هذه هي التفاصيل الفعلية للمحور
    • لا تستعمل أي مستوى ثالث (### ) ولا جداول ولا روابط
- حافظ تماماً على سطر "---\\nmarkmap:\\n  colorFreezeLevel: 2\\n---" كما هو في أول
  الملف، وعلى وسمي <script type="text/template"> و </script> كما هما بالضبط
- ضع الكل داخل ```interactive_svg ... ``` (وليس ```svg``` ولا ```markdown```)

القالب الكامل الذي يجب اتباعه:
{mindmap_template}"""

    mindmap_instruction_fr = f"""
Instructions carte mentale (pour les résumés):
- Quand l'élève demande un résumé: utilise le modèle de carte mentale ci-dessous —
  ne donne jamais une simple liste à puces, et ne dessine jamais un SVG de carte
  mentale toi-même à la main
- 🔴 Règle stricte: ce modèle ne se construit pas avec des coordonnées, des couleurs
  ou du code — uniquement avec du texte Markdown. Le moteur (une bibliothèque externe
  déjà prête) gère automatiquement tout le rendu, l'animation, le zoom et le pliage
- Modifie UNIQUEMENT le contenu à l'intérieur de <script type="text/template"> ... 
  </script> — rien d'autre dans le modèle (ni le CSS, ni les boutons, ni la balise
  <script> de markmap-autoloader)
- Respecte exactement cette structure Markdown:
    • La première ligne après --- markmap: ... --- commence par "# " suivi du titre
      de la leçon ou du sujet (une courte phrase) — c'est le centre
    • Chaque branche principale commence par "## " (3 à 6 branches) — titre court
      de 2 à 6 mots
    • Sous chaque branche, place 2 à 4 sous-points, chacun commençant par "- ",
      de 3 à 12 mots — ce sont les vrais détails de cette branche
    • N'utilise aucun troisième niveau (### ), aucun tableau, aucun lien
- Conserve exactement le bloc "---\\nmarkmap:\\n  colorFreezeLevel: 2\\n---" en tout
  début, ainsi que les balises <script type="text/template"> et </script> telles
  quelles
- Place le tout dans ```interactive_svg ... ``` (pas ```svg``` ni ```markdown```)

Modèle complet à suivre:
{mindmap_template}"""

    if lang == "ar":
        lesson_info = ""
        if lesson_title:
            lesson_info = f"الدرس {lesson_number}: {lesson_title}" if lesson_number else f"الدرس: {lesson_title}"
            if level:
                lesson_info += f" | المستوى: {level}"

        return f"""أنت مساعد بيداغوجي متخصص في تدريس الرياضيات للمرحلة الإعدادية التونسية.
مهمتك: مساعدة التلميذ على فهم الدرس بطريقة واضحة ومتدرجة، مع احترام المنهج التونسي.

{dual_output_block}

{visual_block}

{'═' * 50}
{lesson_info}
{'═' * 50}

نقاط الدرس التي سيتعلمها التلميذ:
{content}

{'═' * 50}
تعليمات الإجابة:
- أجب بالعربية دائماً في كل رسائلك
- اكتب المصطلحات الرياضية بالعربية فقط
- اشرح بأسلوب بسيط ومتدرج يناسب مستوى التلميذ
- كن موجزاً: أجب بالقدر الكافي فقط
- إذا طلب التلميذ عدة تمارين: رتّبها من الأسهل إلى الأصعب
- عند إعطاء تمرين: اعرض السؤال والرسم التوضيحي فقط، ثم اطلب من التلميذ المحاولة أولاً
- الحل يُعطى فقط إذا طلبه التلميذ صراحةً
- إذا كان السؤال خارج نطاق الدرس: أخبر التلميذ بلطف

{mindmap_instruction_ar}{tc_injection}""".strip()

    else:
        lesson_info = ""
        if lesson_title:
            lesson_info = f"Leçon {lesson_number}: {lesson_title}" if lesson_number else f"Leçon: {lesson_title}"
            if level:
                lesson_info += f" | Niveau: {level}"

        return f"""Tu es un assistant pédagogique spécialisé dans l'enseignement des mathématiques
au collège tunisien. Ta mission: aider l'élève à comprendre le cours de façon claire
et progressive, en respectant le programme tunisien.

{dual_output_block}

{visual_block}

{'═' * 50}
{lesson_info}
{'═' * 50}

Les notions abordées dans cette leçon:
{content}

{'═' * 50}
Instructions:
- Réponds toujours en français
- Explique de façon simple et progressive
- Sois concis
- Pour les exercices: présente uniquement l'énoncé dans [WIDGET], puis invite l'élève à essayer
- La solution n'est fournie que si l'élève la demande explicitement
- Si la question est hors sujet: informe l'élève poliment

{mindmap_instruction_fr}{tc_injection}""".strip()