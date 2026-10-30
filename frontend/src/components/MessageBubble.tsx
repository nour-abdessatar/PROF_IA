// ─── MessageBubble.tsx ────────────────────────────────────────────────────────
//
// Change vs previous version:
//   • When msg.hasWidget is true, the AI bubble skips all segment/widget
//     rendering and only shows the teacher explanation text (or a placeholder).
//     The actual widget is rendered in the left CanvasPanel in Chat.tsx.
//
//   • FIX (char-wrap bug): user bubbles no longer have `direction: rtl` forced
//     on them (that was only meant to affect AI bubbles per UI language).
//     Combined with shrink-to-fit sizing, forced rtl direction on short
//     Latin-script strings (e.g. "oui") caused the browser to miscalculate
//     the box's intrinsic width down to a single character, making the text
//     wrap one letter per line. User bubbles now use `dir="auto"` and
//     `width: max-content` instead of `inline-block`/`w-fit`.
//
//   • FIX (wall-of-text teacher bubble): msg.teacher was passed straight into
//     marked.parse() with no paragraph breaks, so `marked` emitted a single
//     <p> no matter how many logically distinct sentences were in it — the
//     [&>p]:mb-3 spacing rule had nothing to apply to. formatTeacherText()
//     now splits the flat string into markdown paragraphs (~2 sentences
//     each) and pulls the trailing confirmation question ("Tu as compris...?"
//     / "هل فهمت...؟") into its own blockquote so it reads as a distinct
//     prompt rather than a continuation of the explanation.
//
//   • FIX (bubble side flips / direction followed UI language, not content):
//     applyLayout() used to set el.style.direction and el.style.textAlign
//     from the `lang` prop (interface language) for BOTH user and AI
//     bubbles, and did so via an inline style applied after render — which
//     overrode the dir="auto" already set in JSX on the user bubble. As a
//     result, message text direction tracked the UI language instead of
//     each message's own content, and switching the interface language
//     could visually override per-message direction. applyLayout() now only
//     sets the bubble's border-radius (tail shape); direction is handled
//     entirely by dir="auto" in JSX on both user and AI bubbles, based on
//     each message's own content.
//
//   • FIX (Arabic text looking "décalé"/shifted, this version): every AI text
//     container hardcoded Tailwind's `tracking-wide` (letter-spacing: 0.025em)
//     regardless of language. Letter-spacing is a Latin-script styling
//     concept; Arabic is a cursive script where most letters must visually
//     connect to their neighbors depending on position. Adding positive
//     letter-spacing breaks that joining and pushes glyphs (and any
//     embedded Latin/number runs, e.g. "12", "D12") out of their natural
//     position — which is exactly what read as "shifted" text. `tracking-wide`
//     is now applied only when lang !== "ar", in all three places that used
//     it (the hasWidget teacher-text block, the normal AI-bubble teacher-text
//     block, and SegmentView's plain-text renderer — which now also receives
//     `lang` as a prop so it can make the same decision). Nothing else
//     changed: dir="auto", spacing utilities, markdown rendering, and every
//     other class are untouched.
//
//   • FIX (markdown never actually rendered): `marked` and KaTeX's
//     `renderMathInElement` were read off `window.marked` /
//     `window.renderMathInElement`, expecting a CDN <script> tag to have
//     put them there. Neither package was ever installed or loaded that
//     way, so `window.marked` was always `undefined` and every AI message
//     silently fell back to the plain-text/no-markdown path — bold, lists,
//     and headings from the backend were never actually turned into <strong>/
//     <ul>/<h2>, they just showed up as literal "**text**" / "- item" runs.
//     Both are now real npm imports (`marked`, `katex/contrib/auto-render`)
//     instead of implicit globals.
//
//   • FIX (formatTeacherText mangling already-structured backend markdown):
//     formatTeacherText() was written for an old backend that returned one
//     flat, unstructured string, so it re-split the whole text by sentence
//     punctuation and regrouped ~2 sentences per paragraph — ignoring any
//     newlines already in the string. Now that the backend sends real
//     markdown (bullet lists, headings, bold), that resplit step silently
//     destroyed it: "- item1\n- item2" got flattened into a single run-on
//     sentence-paragraph, same for "## heading" lines. formatTeacherText()
//     now detects existing markdown structure (lists / headings / bold)
//     via hasMarkdownStructure() and, when present, passes the text straight
//     through to `marked` untouched (only still pulling out the trailing
//     confirmation question). The old sentence-resplit path is unchanged
//     and still used for legacy flat strings with no markdown markers.
//
//   • NEW FIX (math formulas scrambled by bidi / exercise headers not bold /
//     bubbles felt cramped): three related readability problems, all fixed
//     without touching the backend:
//
//     1. Math scrambled ("A = 8³ × 4⁵ × 2⁷" rendering out of order): the
//        teacher text is Arabic (RTL). A line like "A = 8³ × 4⁵ × 2⁷" is
//        made up of Unicode "weak"/"neutral" characters (digits, ×, =, ()),
//        which the browser's bidi algorithm positions based on the
//        *surrounding* paragraph direction. Sitting inside an RTL paragraph,
//        those characters get visually reordered instead of reading
//        left-to-right like a normal formula. isMathLine()/wrapMathLine()
//        now detect lines that are "math-only" (no Arabic letters, has a
//        digit + an operator, no real multi-letter words) and wrap them in
//        their own dir="ltr" + unicode-bidi:isolate block, centered on its
//        own line — isolating them from the surrounding RTL paragraph so
//        the browser can no longer reorder their characters.
//
//     2. Formula merged into the same paragraph as the preceding sentence:
//        formatTeacherText() used to *always* re-split "flat" teacher text
//        by sentence punctuation and regroup ~2 sentences per paragraph —
//        even when the backend had already sent the text pre-broken into
//        lines (exercise title / statement / formula each on their own
//        line). That resplit ignored the existing "\n" breaks, merging the
//        Arabic instruction and the LTR formula into one paragraph, which
//        made the bidi scrambling worse. formatTeacherText() now checks for
//        existing "\n" breaks first and, if present, treats each line as
//        its own paragraph/heading/math-block (preprocessLinesFull) instead
//        of resplitting by punctuation. The old punctuation-resplit path is
//        kept only as a last-resort fallback for genuinely flat one-line text.
//
//     3. Exercise headers not visually distinct: lines like "التمرين رقم 1"
//        / "Exercice 1" / "Question 2" are now detected (isHeadingLine) and
//        promoted to a markdown "### " heading, so they pick up the same
//        bold/spaced/colored h3 styling already defined below instead of
//        rendering as plain paragraph text.
//
//     Also bumped paragraph/heading/list vertical spacing slightly.
//
//   • FIX (exercise headers not bolded in multi-topic replies): see
//     wrapMathAndHeadingsOnly()'s own comment below for the full trace.
//
//   • FIX (bubble width randomly shrinking / bubbles STILL different widths):
//     see AI_BUBBLE_MIN_WIDTH / AI_BUBBLE_MAX_WIDTH comment below.
//
//   • FIX (superscript exponent-step annotations, e.g. "⁺²", not isolated):
//     see SUPERSCRIPT_RE comment below.
//
//   • FIX (bare parenthetical numbers wrongly promoted to their own bold/
//     centered line, splitting sentences apart): see stripExplicitFormulaBlocks
//     comment below — root cause was server-side (prompt_builder.py), this
//     file only added a defense-in-depth safety net for raw "$$...$$" that
//     might still reach the frontend unprocessed.
//
//   • FIX (invisible Unicode bidi-isolation marks breaking LaTeX inside
//     "$$...$$", e.g. a formula rendering as the literal text
//     "$$⁦17 = 5⁩ \⁦times 3 + 2⁩$$" instead of a rendered equation):
//
//     Root cause is server-side (model output), not a rendering mistake
//     here: when Gemini writes Arabic (RTL) text containing an inline LTR
//     math/LaTeX span, it sometimes inserts invisible Unicode bidi-isolation
//     control characters — U+2066 LRI, U+2067 RLI, U+2068 FSI, U+2069 PDI,
//     and occasionally U+200E LRM / U+200F RLM — directly around the LTR
//     runs, presumably trying to "protect" them from the surrounding RTL
//     paragraph. When one of these lands *inside* a LaTeX command (e.g.
//     between the backslash and the command name: "\⁦times"), the command
//     is no longer well-formed. KaTeX's auto-render then fails to match/
//     parse it, so the raw "$$...$$" text (now also containing invisible
//     junk characters) is shown to the user as literal text instead of a
//     rendered formula.
//
//     Fix: stripBidiControlChars() removes every character in that small,
//     fixed Unicode set from the raw text. It is intentionally applied as
//     the very FIRST step of both stripExplicitFormulaBlocks() call sites
//     (formatTeacherText() and SegmentView's plain-text path), before any
//     other regex/markdown/math logic runs — those characters are invisible
//     and carry no information once we're rendering into a div that already
//     controls direction via CSS (dir="auto" / dir="ltr" on isolated math
//     blocks), so removing them is safe everywhere, not just inside "$$".
//     Nothing else changed: this is a pure text-sanitization pass with no
//     effect on visible characters, spacing, or existing formatting logic.
//
//   • NEW FIX (THIS VERSION — KaTeX-rendered "$$...$$" math still visually
//     "décalé"/shifted, e.g. \widehat{AOB}'s accent misaligned, even after
//     the bidi-control-char strip above):
//
//     Root cause: stripBidiControlChars() removes invisible bidi marks so
//     they can no longer corrupt the LaTeX *source* — but it does nothing
//     about the *rendering context* the formula ends up in. A "$$...$$"
//     block, once promoted onto its own line by stripExplicitFormulaBlocks()
//     and rendered by KaTeX via renderMathInElement(), was still sitting
//     inside an ambient RTL flow: the nearest explicit `dir` attribute is
//     `dir="auto"` on the outer AI-bubble <div>, which resolves to "rtl"
//     for an Arabic-dominant message. KaTeX itself doesn't set its own
//     `direction`/`unicode-bidi` — it assumes the caller isolates it, the
//     same way this file already does for isMathLine()-detected plain-text
//     math lines (wrapMathLine() below has always added
//     `dir="ltr" style="direction:ltr;unicode-bidi:isolate"`). The KaTeX/
//     "$$...$$" path never got the equivalent treatment, so stretchy
//     accents and multi-character constructs (which KaTeX positions using
//     relative widths/centering) could end up measured or laid out in an
//     RTL-influenced context and render visibly shifted, even though the
//     underlying LaTeX source itself was already clean.
//
//     Fix: stripExplicitFormulaBlocks() now wraps each "$$...$$" block in
//     an explicit `dir="ltr"; unicode-bidi:isolate` <div> (mirroring
//     wrapMathLine()'s existing treatment) BEFORE marked/KaTeX ever see it,
//     instead of just re-inserting bare "$$...$$" back into the flow. KaTeX
//     auto-render still finds and renders the "$$...$$" delimiters exactly
//     as before (they're preserved unchanged inside the new wrapper) — the
//     only change is that the div they now live in is isolated from the
//     surrounding RTL paragraph direction, the same guarantee plain math
//     lines already had. Nothing else in this function changed.
//
// ─────────────────────────────────────────────────────────────────────────────

import { useRef, useEffect, memo } from "react";
import { marked } from "marked";
import renderMathInElement from "katex/contrib/auto-render";
import "katex/dist/katex.min.css";

function StreamingDots() {
  return (
    <div className="flex items-center gap-1.5 py-1">
      <span className="w-2 h-2 rounded-full bg-foreground/50 animate-bounce" style={{ animationDelay: "0ms" }} />
      <span className="w-2 h-2 rounded-full bg-foreground/50 animate-bounce" style={{ animationDelay: "150ms" }} />
      <span className="w-2 h-2 rounded-full bg-foreground/50 animate-bounce" style={{ animationDelay: "300ms" }} />
    </div>
  );
}

interface Message {
  id: string;
  role: "user" | "ai";
  content: string;
  teacher?: string;
  structured?: { title: string; explanation: string; example: string; summary: string };
  hasWidget?: boolean;
}

type Segment =
  | { type: "text";            html: string }
  | { type: "svg";             code: string }
  | { type: "interactive_svg"; code: string };

// ── Invisible bidi-isolation control characters ─────────────────────────────
// See top-of-file changelog. These six code points are all invisible,
// zero-width directional-formatting characters. LLMs writing mixed RTL/LTR
// text (Arabic prose with embedded LaTeX/numbers) sometimes sprinkle them in
// to hint at directionality — occasionally landing mid-token (e.g. inside
// "\times") and corrupting it. Stripping them is always safe here: this
// component already controls direction explicitly via dir="auto" (JSX) and
// dir="ltr" + unicode-bidi:isolate (wrapMathLine's own wrapper div, and now
// the "$$...$$" wrapper below too), so these characters add no behavior we
// rely on, only breakage risk.
const BIDI_CONTROL_RE = /[\u200E\u200F\u2066\u2067\u2068\u2069]/g;

function stripBidiControlChars(text: string): string {
  if (!text) return text;
  return text.replace(BIDI_CONTROL_RE, "");
}

// ── SAFETY NET: explicit $$...$$ formula blocks (see top-of-file changelog) ─
// Mirrors prompt_builder.py's _EXPLICIT_FORMULA_RE / normalize_exercise_
// layout() behavior: promote the block onto its own paragraph and isolate it
// from the surrounding RTL flow. Normally the backend already does the
// promotion part before the text reaches the frontend at all — this exists
// so a server-side fail-open (normalize_exercise_layout() raising, caught,
// text shipped unmodified) never surfaces literal "$$" characters to the
// person, AND (as of this version) so KaTeX's rendered output is never left
// sitting in an ambient RTL context regardless of where the "$$" came from.
const EXPLICIT_FORMULA_RE = /\$\$([\s\S]+?)\$\$/g;

function stripExplicitFormulaBlocks(text: string): string {
  if (!text) return text;
  // Bidi control chars are stripped FIRST, unconditionally — they can hide
  // inside the "$$" delimiters themselves or inside the inner LaTeX, and
  // must be gone before we even test for "$$" or hand text to KaTeX/marked.
  const clean = stripBidiControlChars(text);
  if (!clean.includes("$$")) return clean;
  // NEW FIX (THIS VERSION): wrap the block in an explicit LTR-isolated div
  // (same treatment wrapMathLine() already gives plain-text math lines)
  // instead of just re-inserting bare "$$...$$". KaTeX's auto-render still
  // matches the "$$...$$" delimiters fine inside this wrapper — only the
  // ambient direction context around them changes.
  return clean.replace(
    EXPLICIT_FORMULA_RE,
    (_, inner) =>
      `\n\n<div dir="ltr" style="direction:ltr;unicode-bidi:isolate;text-align:center;">$$${inner.trim()}$$</div>\n\n`
  );
}

// ── NEW: math-line isolation + heading detection ────────────────────────────
//
// isMathLine(): a line counts as "math-only" when it has no Arabic letters,
// contains at least one digit, contains at least one math-ish symbol
// (=, ×, +, -, ^, parens, superscript digits, √, %), has no real multi-letter
// word (which would mean it's a sentence, not a formula — single-letter
// variables like "A", "B", "n" are fine), and isn't implausibly long.
//
// NOTE (see top-of-file changelog): this function's job has always only
// been "is this line — which is ALREADY alone on its own line — a formula
// that should be styled as one?", never "should this text be split onto
// its own line in the first place?". That splitting decision now lives
// entirely server-side (explicit "$$" delimiters), so this function is
// unchanged and, as a side effect of the server-side fix, now only ever
// sees genuine standalone-line candidates instead of accidentally-split
// sentence fragments.
const ARABIC_RE = /[\u0600-\u06FF]/;
const WORD_RE   = /[A-Za-z]{3,}/;

// Superscript plus/minus (U+207A/U+207B) — a line made up only of these
// plus superscript digits (e.g. "⁺²") has no ASCII digit, so the plain
// /\d/ test below missed it and it fell through unisolated/unstyled.
const SUPERSCRIPT_RE = /[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻ˣ]/;

// NOTE: no longer requires an explicit math symbol (=, ×, ...) to be
// present — a bare standalone number like "360" (a computed answer on its
// own line, no operator at all) should still be isolated/centered the same
// way a full formula is, otherwise it looks visually inconsistent next to
// the other formulas. "No Arabic + has a digit + no real (3+ letter) word +
// not implausibly long" is distinctive enough on its own: any line that
// clears those three bars is either a formula or a bare numeric answer,
// never an actual sentence.
function isMathLine(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed) return false;
  if (ARABIC_RE.test(trimmed)) return false;
  if (!/\d/.test(trimmed) && !SUPERSCRIPT_RE.test(trimmed)) return false;
  if (WORD_RE.test(trimmed)) return false;
  if (trimmed.length > 60) return false;
  return true;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Wraps a math-only line in its own dir="ltr" + unicode-bidi:isolate block so
// the browser's bidi algorithm can't reorder its digits/operators based on
// the surrounding RTL paragraph. Centered, with its own vertical rhythm so
// it visually reads as a standalone formula rather than a run of text.
function wrapMathLine(line: string): string {
  return `<div dir="ltr" style="direction:ltr;unicode-bidi:isolate;display:block;text-align:center;margin:14px 0;font-size:1.05em;font-weight:600;">${escapeHtml(line.trim())}</div>`;
}

// ── Exercise header color — CHANGE ONLY THIS ONE LINE ───────────────────────
// This is the single source of truth for "التمرين رقم N" / "Exercice N" /
// "السؤال رقم N" header color. It is intentionally NOT tied to the generic
// [&>h3]:text-primary Tailwind rule used elsewhere in this file — exercise
// headers are rendered as their own <div>, not a markdown "### " heading, so
// changing this one hex value recolors ONLY exercise/question headers and
// nothing else (no other heading, no body text, no math line is affected).
const EXERCISE_HEADER_COLOR = "#4122ca"; // e.g. try "#e05c2a" for orange, or any hex

// Renders a detected exercise/question header (e.g. "التمرين رقم 7") as its
// own styled block, completely independent of markdown heading levels. Used
// instead of emitting "### " so this can be recolored/restyled without ever
// touching (or being touched by) any other h2/h3 rule in the file.
function wrapExerciseHeader(header: string): string {
  return `<div class="exercise-header-line" style="color:${EXERCISE_HEADER_COLOR};font-weight:700;font-size:15px;margin-top:20px;margin-bottom:8px;">${escapeHtml(header)}</div>`;
}

// Exercise/question header detection, Arabic + French. The backend is
// inconsistent about whether the header is on its own line ("التمرين رقم 1")
// or glued to the start of the instruction sentence on the same line
// ("التمرين رقم 1 حدد المقسوم..."). The captured second group grabs
// whatever trailing text follows the header (possibly empty) so both shapes
// are handled: the header always becomes its own bold heading, and any
// trailing instruction text becomes its own separate paragraph right after
// it, instead of being silently left un-bolded because the line as a whole
// didn't match a "header only" pattern.
const HEADING_PATTERNS: RegExp[] = [
  /^(التمرين\s*(?:رقم)?\s*\d+)\s*[:\-]?\s*(.*)$/,
  /^(تمرين\s*(?:رقم)?\s*\d+)\s*[:\-]?\s*(.*)$/,
  /^(السؤال\s*(?:رقم)?\s*\d+)\s*[:\-]?\s*(.*)$/,
  /^(Exercice\s*\d+)\s*[:\-]?\s*(.*)$/i,
  /^(Question\s*\d+)\s*[:\-]?\s*(.*)$/i,
];

function matchHeading(line: string): { header: string; rest: string } | null {
  const trimmed = line.trim();
  for (const re of HEADING_PATTERNS) {
    const m = trimmed.match(re);
    if (m) return { header: m[1].trim(), rest: (m[2] || "").trim() };
  }
  return null;
}

// Used for text that already has explicit "\n" breaks: each line becomes its
// own markdown paragraph, heading, or isolated math block — instead of being
// merged back together by the old sentence-punctuation resplit logic.
function preprocessLinesFull(text: string): string {
  const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
  const out: string[] = [];
  for (const line of lines) {
    const heading = matchHeading(line);
    if (heading) {
      out.push(wrapExerciseHeader(heading.header));
      if (heading.rest) {
        out.push(isMathLine(heading.rest) ? wrapMathLine(heading.rest) : heading.rest);
      }
    } else if (isMathLine(line)) {
      out.push(wrapMathLine(line));
    } else {
      out.push(line);
    }
  }
  return out.join("\n\n");
}

// Lighter pass used when markdown structure already exists (lists/headings/
// bold from the backend) or on raw AI text segments: only isolates math
// lines, leaving everything else (including existing markdown syntax) untouched.
// Kept as-is (still exported/used nowhere new) — see wrapMathAndHeadingsOnly()
// below, which is now used instead wherever exercise-header detection must
// also run alongside math isolation.
function wrapMathLinesOnly(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  for (const line of lines) {
    if (isMathLine(line)) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      out.push(wrapMathLine(line));
      out.push("");
    } else {
      out.push(line);
    }
  }
  return out.join("\n");
}

// FIX: used instead of wrapMathLinesOnly() wherever the text may ALREADY
// contain markdown the model wrote itself (e.g. a "## " or "**bold**" topic
// subtitle ahead of a multi-topic exercise group). Doing a line-by-line pass
// here would previously stop at math-isolation only, so a bare
// "التمرين رقم N" line sitting right after a model-written subtitle never
// got promoted to a heading — the subtitle got styled (it was already real
// markdown) but the exercise header next to it silently didn't. This runs
// the exact same matchHeading() promotion preprocessLinesFull() already does
// for the no-markdown-anywhere case, so a model-authored heading elsewhere in
// the message no longer suppresses heading detection for plain
// "التمرين رقم N" / "Exercice N" lines. Any markdown syntax already present
// in a line (##, **bold**, "- item", etc.) is left completely untouched —
// only bare heading-pattern lines and math-only lines get transformed.
function wrapMathAndHeadingsOnly(text: string): string {
  const lines = text.split("\n");
  const out: string[] = [];
  for (const rawLine of lines) {
    const line = rawLine.trim();          // ← add this
    if (!line) { out.push(""); continue; } // ← preserve blank-line separators
    const heading = matchHeading(line);
    if (heading) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      out.push(wrapExerciseHeader(heading.header));
      if (heading.rest) {
        out.push(isMathLine(heading.rest) ? wrapMathLine(heading.rest) : heading.rest);
      }
      out.push("");
    } else if (isMathLine(line)) {
      if (out.length && out[out.length - 1] !== "") out.push("");
      out.push(wrapMathLine(line));
      out.push("");
    } else {
      out.push(line);
    }
  }
  return out.join("\n");
}

// ── teacher-text paragraph formatter ────────────────────────────────────────
// Splits a flat teacher string (no \n\n from the backend) into markdown
// paragraphs so `marked` actually emits separate <p> tags — which is what
// lets the existing [&>p]:mb-3 spacing rule do anything at all. Also pulls
// the trailing confirmation question into its own blockquote line so it's
// visually set apart from the explanation instead of buried at the end of
// a run-on paragraph.
const CONFIRM_RE_FR = /(Tu as compris[^?]*\?[^?]*\?)\s*$/;
const CONFIRM_RE_AR = /(هل فهمت[^؟]*؟[^؟]*؟)\s*$/;

// Detects markdown structure the backend may already have produced
// (bullet/numbered lists, headings, bold spans). When present, the
// sentence-resplit logic below must be skipped entirely — it works purely
// off sentence punctuation and has no concept of "- item" lines or "##"
// headings, so running it on already-structured text merges list items and
// headings into flowing prose and destroys the structure.
function hasMarkdownStructure(text: string): boolean {
  return /\n[ \t]*[-*][ \t]+\S|\n[ \t]*\d+\.[ \t]+\S|(^|\n)#{1,6}[ \t]+\S|\*\*[^*\n]+\*\*/.test(text);
}

function formatTeacherText(raw: string, lang: "ar" | "fr"): string {
  if (!raw) return raw;

  // SAFETY NET (see top-of-file changelog): strip invisible bidi-isolation
  // control characters, then strip/promote any raw "$$...$$" that reached
  // the frontend unprocessed (server-side fail-open case only — normally
  // already handled before this text arrives). Must run FIRST, before
  // anything else below inspects line breaks or markdown structure, since
  // this can introduce new "\n\n" paragraph boundaries.
  let text = stripExplicitFormulaBlocks(raw).trim();

  // 1. Pull out the trailing confirmation question, if present.
  const confirmRe = lang === "ar" ? CONFIRM_RE_AR : CONFIRM_RE_FR;
  let confirmPart = "";
  const confirmMatch = text.match(confirmRe);
  if (confirmMatch) {
    confirmPart = confirmMatch[1].trim();
    text = text.slice(0, confirmMatch.index).trim();
  }

  // Already-structured markdown (lists / headings / bold): pass through to
  // `marked` basically as-is, only isolating any math-only lines AND still
  // promoting any bare "التمرين رقم N" / "Exercice N" lines to headings —
  // see wrapMathAndHeadingsOnly()'s changelog note above for why this can
  // no longer be wrapMathLinesOnly() alone.
  if (hasMarkdownStructure(text)) {
    let md = wrapMathAndHeadingsOnly(text);
    if (confirmPart) md += `\n\n> ${confirmPart}`;
    return md;
  }

  // Text already broken into lines by the backend (exercise title /
  // statement / formula each on their own "\n"): respect those breaks
  // instead of resplitting by sentence punctuation, which used to merge
  // an Arabic sentence and an LTR formula into a single paragraph.
  if (text.includes("\n")) {
    let md = preprocessLinesFull(text);
    if (confirmPart) md += `\n\n> ${confirmPart}`;
    return md;
  }

  // Legacy fallback: genuinely flat single-line prose with no structure
  // and no explicit line breaks at all.
  const sentenceSplit = lang === "ar"
    ? text.split(/(?<=[.؟!])\s+(?=\S)/)
    : text.split(/(?<=[.?!])\s+(?=[A-ZÀ-ÿ])/);

  const rawParagraphs: string[] = [];
  for (let i = 0; i < sentenceSplit.length; i += 2) {
    rawParagraphs.push(sentenceSplit.slice(i, i + 2).join(" "));
  }
  const paragraphs = rawParagraphs.map(p => (isMathLine(p) ? wrapMathLine(p) : p));

  let md = paragraphs.filter(Boolean).join("\n\n");
  if (confirmPart) {
    md += `\n\n> ${confirmPart}`;
  }
  return md;
}

// Always runs formatTeacherText() first, then renders through `marked`.
function renderFormattedHtml(raw: string, lang: "ar" | "fr"): string {
  const formatted = formatTeacherText(raw, lang);
  return marked.parse(formatted) as string;
}

// applyLayout() only controls the bubble's alignment (side) and tail shape,
// both tied to role and unrelated to text direction. Direction is handled
// entirely by dir="auto" in JSX on both user and AI bubbles, based on each
// message's own content.
function applyLayout(el: HTMLElement, role: Message["role"]) {
  if (role === "user") {
    el.style.alignSelf    = "flex-end";
    el.style.borderRadius = "18px 18px 4px 18px";
  } else {
    el.style.alignSelf    = "flex-start";
    el.style.borderRadius = "18px 18px 18px 4px";
  }
}

// Small helper so all three call sites gate `tracking-wide` the same way:
// letter-spacing is a Latin-script concept and visibly breaks Arabic's
// cursive letter-joining, so it must never apply when lang === "ar".
function trackingClass(lang: "ar" | "fr"): string {
  return lang === "ar" ? "" : "tracking-wide";
}

// ── Shared AI bubble width envelope ─────────────────────────────────────────
// FIX (bubbles STILL different widths): both the min-width floor and the
// max-width ceiling are expressed in px via min(px, 100%), never as a bare
// Tailwind percentage class — percentages can't resolve reliably here since
// the bubble's containing block (a wrapper <div> in Chat.tsx) is itself
// shrink-to-fit. A single shared pair of constants is used by BOTH the
// hasWidget bubble and the normal bubble so a widget-teacher-text reply and
// a plain-text reply always land on the exact same width envelope, instead
// of each shrinking independently to their own widest line. See top-of-file
// changelog for the full explanation.
const AI_BUBBLE_MIN_WIDTH = "min(420px, 92vw)";
const AI_BUBBLE_MAX_WIDTH = "min(640px, 92vw)";

function parseSegments(raw: string, isStreaming: boolean): Segment[] {
  const segments: Segment[] = [];
  const OPEN = /```(interactive_svg|svg)\s*/g;
  let cursor = 0;

  while (true) {
    OPEN.lastIndex = cursor;
    const openMatch = OPEN.exec(raw);

    if (!openMatch) {
      const tail = raw.slice(cursor);
      if (isStreaming) {
        const incompleteStart = tail.search(/```/);
        const visibleTail = incompleteStart === -1 ? tail : tail.slice(0, incompleteStart);
        if (visibleTail.trim()) segments.push({ type: "text", html: visibleTail });
      } else {
        if (tail.trim()) segments.push({ type: "text", html: tail });
      }
      break;
    }

    const kind       = openMatch[1] as "svg" | "interactive_svg";
    const fenceStart = openMatch.index;
    const codeStart  = fenceStart + openMatch[0].length;

    const textBefore = raw.slice(cursor, fenceStart);
    if (textBefore.trim()) segments.push({ type: "text", html: textBefore });

    const closeIdx = raw.indexOf("```", codeStart);
      if (closeIdx === -1) {
        // Unterminated fence: no matching closing ```. Don't drop the rest of
        // the message — show everything from the opening fence onward as plain
        // text instead of silently discarding it.
        const rest = raw.slice(fenceStart);
        if (rest.trim()) segments.push({ type: "text", html: rest });
        break;
      }
  }

  return segments;
}

const InteractiveSvgWidget = memo(function InteractiveSvgWidget({
  code,
  stableKey,
}: {
  code: string;
  stableKey: string;
}) {
  const iframeRef  = useRef<HTMLIFrameElement>(null);
  const blobUrlRef = useRef<string | null>(null);

  useEffect(() => {
    const isFullDoc = /^\s*<!DOCTYPE|^\s*<html/i.test(code);

    let html: string;
    if (isFullDoc) {
      html = code;
    } else {
      html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>* { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: sans-serif; background: transparent; overflow: hidden; }</style>
</head>
<body>${code}</body>
</html>`;
    }

    const blob = new Blob([html], { type: "text/html" });
    const url  = URL.createObjectURL(blob);

    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    blobUrlRef.current = url;
    if (iframeRef.current) iframeRef.current.src = url;

    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [code, stableKey]);

  return (
    <div className="mt-3 rounded-xl border border-border bg-background/60 overflow-hidden">
      <iframe
        ref={iframeRef}
        className="w-full"
        style={{ height: "360px", border: "none", display: "block" }}
        sandbox="allow-scripts"
        title="interactive geometry widget"
      />
    </div>
  );
});

function SegmentView({
  seg,
  isStreaming,
  index,
  msgId,
  lang,
}: {
  seg: Segment;
  isStreaming: boolean;
  index: number;
  msgId: string;
  lang: "ar" | "fr";
}) {
  if (seg.type === "interactive_svg") {
    const stableKey = `${msgId}-seg${index}-${seg.code.length}`;
    return (
      <InteractiveSvgWidget
        key={stableKey}
        stableKey={stableKey}
        code={seg.code}
      />
    );
  }

  if (seg.type === "svg") {
    return (
      <div
        className="mt-3 rounded-xl border border-border bg-background/60 p-3 flex justify-center"
        dangerouslySetInnerHTML={{ __html: seg.code }}
      />
    );
  }

  if (isStreaming) {
    return (
      <span style={{ whiteSpace: "pre-wrap" }} className="block opacity-90">
        {seg.html}
      </span>
    );
  }

  // SAFETY NET (see top-of-file changelog): strip invisible bidi control
  // characters + strip/promote any raw "$$...$$" before math/heading
  // isolation, same reasoning as formatTeacherText() above — normally
  // already handled server-side.
  const preprocessed = stripExplicitFormulaBlocks(seg.html);

  // Isolate any math-only lines AND promote any bare exercise-header lines
  // before handing off to marked, so plain (non-widget) AI text segments
  // get the same bidi-safe formula treatment AND the same heading-bolding
  // as teacher text — see wrapMathAndHeadingsOnly()'s changelog note above.
  const renderedHtml = marked.parse(wrapMathAndHeadingsOnly(preprocessed)) as string;

  return (
    <div
      style={{ whiteSpace: "normal" }}
      className={`text-foreground ${trackingClass(lang)}
                 [&>p]:mb-4 [&>p]:last:mb-0 [&>p]:leading-relaxed
                 [&>h2]:text-base [&>h2]:font-bold [&>h2]:mt-6 [&>h2]:mb-3 [&>h2]:text-primary
                 [&>h3]:text-[15px] [&>h3]:font-bold [&>h3]:mt-6 [&>h3]:mb-2 [&>h3]:text-primary
                 [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:space-y-2 [&>ul]:mb-4
                 [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:space-y-2 [&>ol]:mb-4
                 [&>hr]:my-6 [&>hr]:border-border/60
                 [&>blockquote]:border-l-4 [&>blockquote]:border-primary [&>blockquote]:pl-4 [&>blockquote]:my-4 [&>blockquote]:italic [&>blockquote]:text-muted-foreground
                 [&>table]:w-full [&>table]:my-5 [&>table]:border-collapse [&>table]:text-xs
                 [&>table_th]:border [&>table_th]:border-border [&>table_th]:p-2.5 [&>table_th]:bg-background/50 [&>table_th]:font-semibold
                 [&>table_td]:border [&>table_td]:border-border [&>table_td]:p-2.5`}
      dangerouslySetInnerHTML={{ __html: renderedHtml }}
    />
  );
}

export const MessageBubble = memo(function MessageBubble({
  msg,
  lang,
  isStreaming,
}: {
  msg: Message;
  lang: "ar" | "fr";
  isStreaming: boolean;
}) {
  const msgRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = msgRef.current;
    if (!el) return;
    applyLayout(el, msg.role);
  }, [msg.role]);

  useEffect(() => {
    if (isStreaming || msg.role !== "ai" || msg.structured) return;
    if (msgRef.current) {
      renderMathInElement(msgRef.current, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$",  right: "$",  display: false },
        ],
      });
    }
  }, [isStreaming, msg.content, msg.teacher, msg.role, msg.structured, msg.hasWidget]);

  // ── Structured card ──────────────────────────────────────────────────────────
  if (msg.structured) {
    return (
      <div ref={msgRef} className="max-w-[80%] rounded-2xl px-5 py-4 text-sm leading-relaxed bg-muted text-foreground rounded-bl-md shadow-sm">
        <div className="space-y-3">
          <p className="font-bold text-base text-primary">{msg.structured.title}</p>
          <p className="text-muted-foreground">{msg.structured.explanation}</p>
          <p className="text-xs bg-background/50 p-2.5 rounded-lg border border-border/40">📝 {msg.structured.example}</p>
          <p className="font-semibold text-xs text-emerald-600 dark:text-emerald-400">✅ {msg.structured.summary}</p>
        </div>
      </div>
    );
  }

  // ── User bubble ──────────────────────────────────────────────────────────────
  if (msg.role === "user") {
    return (
      <div
        ref={msgRef}
        dir="auto"
        className="max-w-[78%] rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed shadow-sm gradient-primary text-primary-foreground rounded-br-md break-words"
        style={{ width: "max-content" }}
      >
        <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span>
      </div>
    );
  }

  // ── AI bubble ────────────────────────────────────────────────────────────────

  // When the message owns a widget, only show the teacher text.
  // The widget itself is displayed in the left CanvasPanel — no duplicate.
  if (msg.hasWidget) {
    return (
      <div
        ref={msgRef}
        dir="auto"
        className="rounded-2xl px-5 py-4 text-[17px] leading-relaxed shadow-sm bg-muted text-foreground rounded-bl-md"
        style={{
          wordBreak: "break-word",
          minWidth: AI_BUBBLE_MIN_WIDTH,
          maxWidth: AI_BUBBLE_MAX_WIDTH,
        }}
      >
        {isStreaming && <StreamingDots />}
        {msg.teacher && !isStreaming && (
          <div
            style={{ whiteSpace: "normal" }}
            className={`text-foreground ${trackingClass(lang)}
                       [&>p]:mb-4 [&>p]:last:mb-0 [&>p]:leading-relaxed
                       [&>h2]:text-base [&>h2]:font-bold [&>h2]:mt-5 [&>h2]:mb-2 [&>h2]:text-primary
                       [&>h3]:text-[15px] [&>h3]:font-bold [&>h3]:mt-5 [&>h3]:mb-2 [&>h3]:text-primary
                       [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:space-y-1.5 [&>ul]:mb-4
                       [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:space-y-1.5 [&>ol]:mb-4
                       [&>strong]:font-semibold
                       [&>blockquote]:mt-3 [&>blockquote]:pt-3 [&>blockquote]:border-t [&>blockquote]:border-border/50 [&>blockquote]:border-l-0 [&>blockquote]:pl-0 [&>blockquote]:font-medium [&>blockquote]:not-italic [&>blockquote]:text-foreground`}
            dangerouslySetInnerHTML={{
              __html: renderFormattedHtml(msg.teacher, lang),
            }}
          />
        )}
        {/* No teacher text and not streaming: show a subtle indicator */}
        {!msg.teacher && !isStreaming && (
          <span className="text-muted-foreground text-sm italic opacity-60">
            {lang === "ar" ? "↖ شاهد الرسم التفاعلي" : "↖ Voir le schéma interactif"}
          </span>
        )}
      </div>
    );
  }

  // ── Normal AI bubble (no widget) ─────────────────────────────────────────────
  const segments = parseSegments(msg.content, isStreaming);

  return (
    <div
      ref={msgRef}
      dir="auto"
      className="rounded-2xl px-5 py-4 text-[15.5px] leading-relaxed shadow-sm bg-muted text-foreground rounded-bl-md"
      style={{
        wordBreak: "break-word",
        minWidth: AI_BUBBLE_MIN_WIDTH,
        maxWidth: AI_BUBBLE_MAX_WIDTH,
      }}
    >
      {isStreaming && <StreamingDots />}

      {msg.teacher && !isStreaming && (
        <div
          style={{ whiteSpace: "normal" }}
          className={`text-foreground ${trackingClass(lang)} mb-3
                     [&>p]:mb-4 [&>p]:last:mb-0 [&>p]:leading-relaxed
                     [&>h2]:text-[17px] [&>h2]:font-bold [&>h2]:mt-5 [&>h2]:mb-2 [&>h2]:text-primary
                     [&>h3]:text-[16px] [&>h3]:font-bold [&>h3]:mt-5 [&>h3]:mb-2 [&>h3]:text-primary
                     [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:space-y-1.5 [&>ul]:mb-4
                     [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:space-y-1.5 [&>ol]:mb-4
                     [&>strong]:font-semibold
                     [&>blockquote]:mt-3 [&>blockquote]:pt-3 [&>blockquote]:border-t [&>blockquote]:border-border/50 [&>blockquote]:border-l-0 [&>blockquote]:pl-0 [&>blockquote]:font-medium [&>blockquote]:not-italic [&>blockquote]:text-foreground`}
          dangerouslySetInnerHTML={{
            __html: renderFormattedHtml(msg.teacher, lang),
          }}
        />
      )}

      {segments.map((seg, i) => (
        <SegmentView
          key={i}
          seg={seg}
          isStreaming={isStreaming}
          index={i}
          msgId={msg.id}
          lang={lang}
        />
      ))}
    </div>
  );
});

export default MessageBubble;