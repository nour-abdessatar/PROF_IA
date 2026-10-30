import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Mic, User, X, Plus, MessageSquare, Trash2, PanelRight, LogOut } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { MessageBubble } from "@/components/MessageBubble";
import AvatarPanel from "@/components/AvatarPanel";
import { t, useLang } from "@/lib/i18n";
import { askStreamSse, apiPost, type AudioChunkPayload } from "@/lib/api";
import { useAuth } from "@/contexts/AuthContext";
import { getUserPref } from "@/lib/userPrefs";

const st = (key: string, lang: "ar" | "fr"): string => {
  const strings: Record<string, Record<"ar" | "fr", string>> = {
    history:           { fr: "Historique",              ar: "السجل" },
    new_conversation:  { fr: "Nouvelle conversation",   ar: "محادثة جديدة" },
    no_conversations:  { fr: "Aucune conversation pour l'instant.", ar: "لا توجد محادثات بعد." },
    today:             { fr: "Aujourd'hui",             ar: "اليوم" },
    yesterday:         { fr: "Hier",                    ar: "أمس" },
    last_7_days:       { fr: "7 derniers jours",        ar: "آخر 7 أيام" },
    older:             { fr: "Plus ancien",             ar: "أقدم" },
    delete:            { fr: "Supprimer",               ar: "حذف" },
    logout:            { fr: "Se déconnecter",          ar: "تسجيل الخروج" },
    // FIX: "إغلاب" isn't a real Arabic word — was a typo for "إغلاق" (close).
    // Used as the sidebar close button's aria-label.
    close:             { fr: "Fermer",                  ar: "إغلاق" },
    default_title:     { fr: "Nouvelle conversation",   ar: "محادثة جديدة" },
    empty_prompt:      { fr: "Pose ta première question pour commencer !", ar: "اطرح سؤالك الأول للبدء!" },
    limit_reached:     { fr: "Limite de conversation atteinte.",           ar: "تم بلوغ حد المحادثة." },
    start_new:         { fr: "Nouvelle conversation",                      ar: "محادثة جديدة" },
    click_to_replay:   { fr: "Cliquer pour rejouer",    ar: "انقر للإعادة" },
    view_drawing:      { fr: "Cliquer pour voir le dessin", ar: "انقر لعرض الرسم" },
  };
  return strings[key]?.[lang] ?? key;
};

export interface Message {
  id: string;
  role: "user" | "ai";
  content: string;
  teacher?: string;
  structured?: { title: string; explanation: string; example: string; summary: string };
  hasWidget?: boolean;
  representation?: string;
}

interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

const STORAGE_KEY = "profia-conversations";
const ACTIVE_KEY  = "profia-active-conversation";

function loadConversationsFromStorage(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const convs: Conversation[] = JSON.parse(raw);
    const lang = (localStorage.getItem("profia-lang") as "ar" | "fr") || "fr";
    const frDefault = "Nouvelle conversation";
    const arDefault = "محادثة جديدة";
    return convs.map(c =>
      c.title === frDefault && lang === "ar"
        ? { ...c, title: arDefault }
        : c.title === arDefault && lang === "fr"
        ? { ...c, title: frDefault }
        : c
    );
  } catch {
    return [];
  }
}

function saveConversations(convs: Conversation[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(convs));
}

function createNewConversation(lang: "ar" | "fr" = "fr"): Conversation {
  return {
    id: crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now(),
    title: st("default_title", lang),
    messages: [],
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
}

function deriveTitleFromMessage(text: string): string {
  const clean = text.trim().replace(/\s+/g, " ");
  return clean.length > 42 ? clean.slice(0, 42) + "..." : clean;
}

function rowToConversation(row: any): Conversation {
  return {
    id:        row.id,
    title:     row.title,
    messages:  Array.isArray(row.messages) ? row.messages : [],
    createdAt: new Date(row.created_at).getTime(),
    updatedAt: new Date(row.updated_at).getTime(),
  };
}

const db = supabase as any;

async function loadConversationsFromSupabase(userId: string): Promise<Conversation[]> {
  const { data, error } = await db
    .from("conversations")
    .select("*")
    .eq("user_id", userId)
    .order("updated_at", { ascending: false });
  if (error) throw error;
  return ((data ?? []) as any[]).map(rowToConversation);
}

// FIX (silent save failures): this used to ignore the result of the upsert,
// so any Supabase error (RLS, network, payload) was swallowed with no trace.
// It now throws on error so callers' .catch() actually receives it.
async function upsertConversation(conv: Conversation, userId: string): Promise<void> {
  const { error } = await db.from("conversations").upsert({
    id:         conv.id,
    user_id:    userId,
    title:      conv.title,
    messages:   conv.messages,
    updated_at: new Date(conv.updatedAt).toISOString(),
  });
  if (error) throw error;
}

async function deleteConversationFromSupabase(id: string): Promise<void> {
  await db.from("conversations").delete().eq("id", id);
}

// FIX (widget cut off / frozen on step 1 — "for now" frontend hardening):
// The backend (stream_handler.py) tries to repair non-canonical canvas
// widgets server-side, but its repair can fail (LLM ignores the retry
// instruction too) and it fails OPEN — it ships the original, non-canonical
// widget to the client rather than blocking. Previously this host CSS/JS
// assumed every canvas widget would exactly match CANVAS_TEMPLATE's ids
// (btnPlay/btnPrev/btnNext/btnReset/lbl) and hid + ignored every other
// button, and always set pointerEvents:none on non-mindmap iframes, routing
// 100% of interaction through the outer div's single "replay" click handler.
// When a widget uses different ids (e.g. Gemini's own prevBtn/nextBtn/
// stepLabel), that click handler's postMessage({type:'REPLAY'}) is a no-op
// (autoPlay() finds null for btnReset/btnPlay), and the buttons required to
// advance are hidden AND unclickable — the widget looks "stuck"/"cut".
//
// This patch does two things, independent of anything the backend does:
//   1. Only hide the specific known autoplay-control ids, not every button
//      on the page (`button:not(.mm-topbar button)` was a blanket net that
//      caught bespoke templates' own nav buttons too).
//   2. Detect when a widget doesn't match the canonical id contract and, in
//      that case only, let pointer events reach the iframe (like mindmaps
//      already do) so the widget's own buttons are clickable, instead of
//      relying on our replay-only click forwarding.
//
// FIX (canvas → animated SVG engine migration): the server-owned animated
// widget engine (prompt_builder.py's CANVAS_ENGINE_SHELL) now renders an
// <svg id="c"> instead of a <canvas id="c">, same ids, same flex-column
// wrapper, same iframe body{overflow:hidden}. The old `canvas { ... }` rule
// below carried `flex: 1 1 auto` + `min-height: 0` specifically to stop a
// flex child from overflowing its allotted space and being silently
// clipped by that overflow:hidden (this is the exact "canvas cut off" bug
// documented below). The `svg { ... }` rule never needed that before,
// because it was only ever used by the mindmap widget, which isn't inside
// this same clipped flex layout in the same way. Now that the animated
// engine's drawing surface is an <svg>, it is exposed to the identical
// clipping risk the canvas rule was written to prevent — so the svg rule
// now carries the same flex/min-height/reset properties as canvas (minus
// object-fit, which doesn't apply to inline <svg>; its own viewBox +
// default preserveAspectRatio="xMidYMid meet" already gives equivalent
// contain-like scaling). Safe for the mindmap widget too: flex/min-height
// only matter if the <svg> is itself a flex item, and the border resets
// are harmless no-ops there.
const WIDGET_OVERRIDE_CSS = `
<style id="__host_override__">
  *, *::before, *::after { box-sizing: border-box; }
  html, body {
    margin: 0 !important;
    padding: 0 !important;
    width: 100% !important;
    height: 100% !important;
    overflow: hidden !important;
    background: transparent !important;
    display: flex !important;
    flex-direction: column !important;
  }
  /* Only the canonical autoplay-engine control ids are hidden — never a
     blanket "every other button" rule. Non-canonical templates (different
     ids) keep their own buttons visible. */
  #btnPlay, #btnPrev, #btnNext, #btnReset { display: none !important; }
  #lbl { display: none !important; }

  /* canvas / interactive_svg templates: single top-level .wrap container */
  .wrap, div[style*="font-family"] {
    display: flex !important;
    flex-direction: column !important;
    width: 100% !important;
    height: 100% !important;
    padding: 0 !important;
    margin: 0 !important;
    border-radius: 0 !important;
    background: transparent !important;
    gap: 0 !important;
  }

  /* mindmap template: two sibling top-level divs (.mm-topbar + .mm-wrap) —
     give each its own sizing instead of letting the .wrap rule above
     (or a generic body > div rule) force both to height:100%, which was
     pushing .mm-wrap (markmap's actual render target) off-screen. */
  .mm-topbar {
    flex: 0 0 auto !important;
    width: 100% !important;
    height: auto !important;
  }
  .mm-wrap {
    flex: 1 1 auto !important;
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
  }

  /* FIX (canvas cut off / clipped): width:100% + height:auto scales a
     <canvas> (a replaced element) purely off the container's width, then
     derives height from its intrinsic ratio (e.g. 400x300 or 480x300).
     When the container is much wider than the canvas's native size, the
     computed height can exceed the vertical space actually allotted to
     this flex child, and since body has overflow:hidden !important above,
     the overflow is silently clipped — this is what "cut off" looked like.
     object-fit:contain + height:100% keeps the canvas fully inside its
     box and preserves aspect ratio instead of overflowing it. */
  canvas {
    flex: 1 1 auto !important;
    width: 100% !important;
    height: 100% !important;
    object-fit: contain !important;
    min-height: 0 !important;
    display: block !important;
    border: none !important;
    border-radius: 0 !important;
    background: transparent !important;
  }
  /* FIX (svg widget cut off — same root cause as the canvas fix above):
     the animated widget engine now draws into <svg id="c"> instead of
     <canvas id="c">, inside the exact same flex-column wrapper with
     body{overflow:hidden}. Without flex:1 1 auto + min-height:0 here too,
     this element is just as vulnerable to being sized past its allotted
     flex space and silently clipped. Kept as one shared rule with the
     mindmap's <svg> (markmap) — flex/min-height only take effect when the
     svg is itself a flex item, and the border/background resets are
     harmless no-ops for markmap's own svg. */
  svg {
    flex: 1 1 auto !important;
    width: 100% !important;
    height: 100% !important;
    min-height: 0 !important;
    display: block !important;
    border: none !important;
    border-radius: 0 !important;
    background: transparent !important;
  }
  .tabs {
    display: flex !important;
    flex: 0 0 auto !important;
    flex-wrap: wrap !important;
    gap: 6px !important;
    padding: 8px 10px 0 !important;
    background: transparent !important;
  }
  .tabs button {
    display: inline-flex !important;
    background: rgba(255,255,255,0.10) !important;
    color: rgba(255,255,255,0.75) !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    border-radius: 8px !important;
    padding: 4px 11px !important;
    font-size: 12px !important;
    cursor: pointer !important;
  }
  .tabs button.active {
    background: rgba(99,80,219,0.65) !important;
    color: #fff !important;
    border-color: rgba(99,80,219,0.85) !important;
  }
  .desc {
    flex: 0 0 auto !important;
    font-size: 12px !important;
    padding: 5px 10px !important;
    color: rgba(255,255,255,0.55) !important;
    background: transparent !important;
  }

  /* mindmap top-bar button: kept visible (unlike other widget buttons above)
     and styled to match, since the generic button:not(.mm-topbar button)
     rule above intentionally excludes it. */
  .mm-topbar button {
    padding: 6px 14px !important;
    border: 1px solid rgba(255,255,255,0.18) !important;
    border-radius: 8px !important;
    background: rgba(255,255,255,0.10) !important;
    color: rgba(255,255,255,0.85) !important;
    cursor: pointer !important;
    font-size: 12px !important;
  }

  /* Non-canonical widget templates (custom ids, e.g. prevBtn/nextBtn) keep
     their own buttons visible and usable — give them a light default style
     so they don't look like unstyled browser buttons, without touching any
     inline styles the widget itself already set. */
  .__non_canonical_widget__ button {
    font-family: sans-serif !important;
  }

  /* NOTE: text-align override attempted here previously caused text to
     disappear entirely (likely because .markmap-foreign > div's width
     is set to a large var(--markmap-max-width) that doesn't match the
     foreignObject's actual visible width, so right-aligning pushed the
     text outside the visible area). Reverted pending more info. */
</style>`;

// FIX: the control script used to only ever try to click #btnReset /
// #btnPlay (the canonical engine's ids) on load and on REPLAY messages.
// For a non-canonical widget (missing those ids) this was always a silent
// no-op, so nothing ever advanced past the widget's own initial render.
// autoPlay() now falls back to clicking whatever "next"-like button the
// widget actually has, so a REPLAY ping (or the initial load) at least
// nudges a non-canonical widget forward instead of doing nothing.
const WIDGET_SCRIPT = `
<script id="__host_ctrl__">
(function () {
  function findFallbackNextButton() {
    // Best-effort: look for common "next" button ids/text used by
    // hand-rolled (non-canonical) widgets so REPLAY isn't a total no-op.
    var byId = document.getElementById('nextBtn') || document.getElementById('btnNext');
    if (byId) return byId;
    var candidates = document.querySelectorAll('button');
    for (var i = 0; i < candidates.length; i++) {
      var txt = (candidates[i].textContent || '').trim();
      if (/التالي|next|suivant|▶/i.test(txt)) return candidates[i];
    }
    return null;
  }
  function autoPlay() {
    var reset = document.getElementById('btnReset');
    var play  = document.getElementById('btnPlay');
    if (reset || play) {
      if (reset) reset.click();
      setTimeout(function () { if (play) play.click(); }, 80);
      return;
    }
    // Non-canonical widget: no reset/play controls exist. Don't silently
    // no-op — at least try to advance one step so the click-to-replay
    // affordance does something visible.
    var next = findFallbackNextButton();
    if (next) next.click();
  }
  function hideMirrorButton() {
    function isMirror(el) {
      var t = (el.textContent || '').trim();
      return t.indexOf('قلب الاتجاه') !== -1 || t.indexOf('\u2194') !== -1;
    }
    function hideIn(topbar) {
      var candidates = topbar.querySelectorAll('button, a, [role="button"]');
      for (var i = 0; i < candidates.length; i++) {
        if (isMirror(candidates[i])) {
          candidates[i].style.setProperty('display', 'none', 'important');
        }
      }
    }
    function watchTopbar(topbar) {
      hideIn(topbar);
      new MutationObserver(function () { hideIn(topbar); })
        .observe(topbar, { childList: true, subtree: true, characterData: true });
    }
    var existing = document.querySelector('.mm-topbar');
    if (existing) {
      watchTopbar(existing);
      return;
    }
    // Topbar not in the DOM yet (renders slightly after load) — watch for it.
    var bodyObserver = new MutationObserver(function () {
      var tb = document.querySelector('.mm-topbar');
      if (tb) {
        watchTopbar(tb);
        bodyObserver.disconnect();
      }
    });
    bodyObserver.observe(document.documentElement, { childList: true, subtree: true });
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTimeout(autoPlay, 300);
      watchLabel();
      hideMirrorButton();
    });
  } else {
    setTimeout(autoPlay, 300);
    watchLabel();
    hideMirrorButton();
  }
  window.addEventListener('message', function (e) {
    if (e.data && e.data.type === 'REPLAY') autoPlay();
  });
  function watchLabel() {
    var lbl = document.getElementById('lbl');
    if (!lbl) return;
    postLabel(lbl.textContent || '');
    var observer = new MutationObserver(function () {
      postLabel(lbl.textContent || '');
    });
    observer.observe(lbl, { childList: true, subtree: true, characterData: true });
  }
  function postLabel(text) {
    var clean = text.trim();
    if (!clean) return;
    window.parent.postMessage({ type: 'CANVAS_LABEL', text: clean }, '*');
  }
})();
<\/script>`;

// ── CanvasPanel ───────────────────────────────────────────────────────────────
// ── CanvasPanel (with zoom/pan) ────────────────────────────────────────────
// Drop this in place of your existing CanvasPanel function.
// Adds: scroll-wheel zoom, pinch-to-zoom (touch), drag-to-pan when zoomed,
// and +/-/reset buttons. Click-to-replay still works when not panning.

// A widget is a mindmap if its HTML contains the mindmap-specific
// scaffolding classes injected by the backend (.mm-topbar / .mm-wrap),
// which only exist in the markmap template — never in canvas/interactive_svg.
function isMindmapContent(content: string): boolean {
  return /mm-wrap|mm-topbar/i.test(content);
}

// FIX ("stuck on step 1" / widget unusable when the LLM ignores the id
// contract): the backend (stream_handler.py) tries to repair canvas
// widgets that don't match CANVAS_TEMPLATE's exact ids
// (btnPlay/btnPrev/btnNext/btnReset/lbl), but that repair can itself fail
// and, by design, fails OPEN — it ships the original non-canonical widget
// to the client rather than blocking the response. Before this fix, the
// frontend always assumed the canonical ids were present: it hid every
// button and force-set pointerEvents:none on the iframe, routing all
// interaction through a single outer "replay" click handler that only
// knew how to click #btnReset/#btnPlay. A widget using different ids
// (e.g. #prevBtn/#nextBtn/#stepLabel) was then both invisible-button AND
// unclickable — frozen on its first render, which is exactly what showed
// up as a "cut off" canvas.
//
// This detects that mismatch so the panel can fall back to letting the
// widget's own (real, visible) buttons receive clicks directly, the same
// way mindmap widgets already do.
//
// NOTE (canvas → animated SVG engine migration): this check still looks
// for a literal `<canvas` tag. The server-owned animated engine no longer
// emits one (it now renders <svg id="c">), so for that widget this
// correctly returns false — it's always canonical (ids are server-owned,
// never LLM-authored), so it should never be routed through the
// non-canonical/native-interaction path. The one remaining template that
// still uses a real <canvas> tag is INTERACTIVE_SVG_TEMPLATE (the
// angle-tabs widget), which was never touched by the migration and still
// needs this check exactly as before.
function isNonCanonicalCanvasContent(content: string): boolean {
  if (!content) return false;
  if (isMindmapContent(content)) return false;
  if (!/<canvas/i.test(content)) return false;
  const hasCanonicalIds =
    /id=["']btnPlay["']/i.test(content) &&
    /id=["']btnPrev["']/i.test(content) &&
    /id=["']btnNext["']/i.test(content) &&
    /id=["']btnReset["']/i.test(content);
  return !hasCanonicalIds;
}

// FIX (literal ```label / ``` text visible in the canvas panel): this used
// to be a single inline regex — /```(?:interactive_svg|svg)\s*([\s\S]*?)```/
// — that only recognized "interactive_svg" or "svg" as fence labels, and
// whose fallback on no match was `raw` itself: the ENTIRE message content,
// backticks and label included, injected straight into the iframe's <body>
// as visible text. Real model output isn't always that narrow shape —
// Gemini has been observed using a third label ("html"), gluing the label
// to a bare fence with no language tag, or skipping the fence entirely.
// This mirrors the same tolerant shape-matching already applied on the
// backend (stream_handler.py's _unwrap_fence) so a label/fence mismatch
// here can't dump raw markdown fence syntax into the widget iframe again:
//   1. ```label\n...\n```            (glued fence, label = interactive_svg/svg/html)
//   2. label\n\n```\n...\n```         (label line, then a bare/unlabeled fence)
//   3. ```\n...\n```                  (bare fence, no label at all)
//   4. label\n\n<raw html/js>         (label line, no fence at all)
// If truly none of these match, a last-resort safety net strips any
// leading/trailing fence or label line it can find rather than shipping
// the raw text (with backticks) straight into the widget iframe.
const FENCE_RE                = /```(?:interactive_svg|svg|html)?\s*\n?([\s\S]*?)```/;
const LABEL_THEN_BARE_FENCE_RE = /(?:interactive_svg|svg|html)\s*\n+```\s*\n?([\s\S]*?)```/;
const BARE_FENCE_RE            = /```\s*\n?([\s\S]*?)```/;
const LABEL_NO_FENCE_RE        = /^\s*(?:interactive_svg|svg|html)\s*\n+([\s\S]+)$/;

function extractWidgetContent(raw: string): string {
  if (!raw) return raw;

  let m = raw.match(FENCE_RE);
  if (m) return m[1].trim();

  m = raw.match(LABEL_THEN_BARE_FENCE_RE);
  if (m) return m[1].trim();

  m = raw.match(BARE_FENCE_RE);
  if (m) return m[1].trim();

  m = raw.trim().match(LABEL_NO_FENCE_RE);
  if (m) return m[1].trim();

  // Safety net: none of the known shapes matched. Strip a leading
  // fence/label line and a trailing bare fence line if present, instead
  // of returning the untouched raw string with backticks still attached.
  return raw
    .replace(/^\s*```[a-zA-Z_]*\s*\n?/, "")
    .replace(/\n?```\s*$/, "")
    .replace(/^\s*(?:interactive_svg|svg|html)\s*\n+/, "")
    .trim();
}

function CanvasPanel({
  content,
  isSpeaking,
  lang,
}: {
  content: string;
  isSpeaking: boolean;
  lang: "ar" | "fr";
}) {
  const iframeRef  = useRef<HTMLIFrameElement>(null);
  const blobUrlRef = useRef<string | null>(null);
  const [showHint, setShowHint]   = useState(false);
  const [isHovered, setIsHovered] = useState(false);
  const hintTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Detect mindmap so we can flip its interaction model (Option B):
  // mindmaps get real pointer events (native markmap click-to-fold),
  // while canvas/interactive_svg widgets keep their existing
  // pointer-events:none + single-click-anywhere-to-replay behavior.
  const isMindmap = content ? isMindmapContent(content) : false;

  // FIX: widgets that don't match the canonical btnPlay/btnPrev/btnNext/
  // btnReset id contract (backend repair failed open) get the same
  // treatment as mindmaps — real pointer events so their own buttons are
  // clickable — instead of being silently frozen behind pointerEvents:none.
  const isNonCanonical = content ? isNonCanonicalCanvasContent(content) : false;
  const usesNativeInteraction = isMindmap || isNonCanonical;

  // ── zoom / pan state ──────────────────────────────────────────────
  const [zoom, setZoom] = useState(1);
  const [pan, setPan]   = useState({ x: 0, y: 0 });
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 3;

  // ── fullscreen modal state (mindmap only) ──────────────────────────
  const [isFullscreen, setIsFullscreen] = useState(false);

  const dragState = useRef<{ dragging: boolean; moved: boolean; startX: number; startY: number; startPan: { x: number; y: number } }>({
    dragging: false, moved: false, startX: 0, startY: 0, startPan: { x: 0, y: 0 },
  });
  const pinchState = useRef<{ startDist: number; startZoom: number } | null>(null);

  const clampPan = (nx: number, ny: number, z: number) => {
    // simple clamp so you can't drag the content fully out of view
    const maxOffset = 400 * (z - 1); // generous bound, adjust to taste
    return {
      x: Math.max(-maxOffset, Math.min(maxOffset, nx)),
      y: Math.max(-maxOffset, Math.min(maxOffset, ny)),
    };
  };

  const resetZoom = () => { setZoom(1); setPan({ x: 0, y: 0 }); };

  const zoomBy = (delta: number, center?: { x: number; y: number }) => {
    setZoom(z => {
      const nz = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z + delta));
      if (nz === 1) setPan({ x: 0, y: 0 });
      return nz;
    });
  };

  // Scroll-wheel zoom
  const onWheel = (e: React.WheelEvent) => {
    if (!content) return;
    e.preventDefault();
    const delta = -e.deltaY * 0.0015;
    zoomBy(delta);
  };

  // Mouse drag-to-pan (only meaningful once zoomed in)
  const onMouseDownPan = (e: React.MouseEvent) => {
    if (zoom <= 1) return;
    dragState.current = {
      dragging: true,
      moved: false,
      startX: e.clientX,
      startY: e.clientY,
      startPan: pan,
    };
  };

  // Touch pinch-to-zoom + one-finger pan
  const dist = (t: React.TouchList | TouchList) => {
    const [a, b] = [t[0], t[1]];
    return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
  };

  const onTouchStart = (e: React.TouchEvent) => {
    if (e.touches.length === 2) {
      pinchState.current = { startDist: dist(e.touches), startZoom: zoom };
    } else if (e.touches.length === 1 && zoom > 1) {
      const t = e.touches[0];
      dragState.current = { dragging: true, moved: false, startX: t.clientX, startY: t.clientY, startPan: pan };
    }
  };

  const onTouchMove = (e: React.TouchEvent) => {
    if (e.touches.length === 2 && pinchState.current) {
      e.preventDefault();
      const scale = dist(e.touches) / pinchState.current.startDist;
      const nz = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, pinchState.current.startZoom * scale));
      setZoom(nz);
    } else if (e.touches.length === 1 && dragState.current.dragging) {
      const t = e.touches[0];
      const dx = t.clientX - dragState.current.startX;
      const dy = t.clientY - dragState.current.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragState.current.moved = true;
      const { x, y } = clampPan(dragState.current.startPan.x + dx, dragState.current.startPan.y + dy, zoom);
      setPan({ x, y });
    }
  };

  const onTouchEnd = () => {
    pinchState.current = null;
    dragState.current.dragging = false;
  };

  useEffect(() => {
    if (!content) {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
      if (iframeRef.current) iframeRef.current.src = "about:blank";
      return;
    }
    const isFullDoc = /^\s*<!DOCTYPE|^\s*<html/i.test(content);
    let html: string;
    if (isFullDoc) {
      html = content.replace(/<\/head>/i, `${WIDGET_OVERRIDE_CSS}${WIDGET_SCRIPT}</head>`);
    } else {
      html = `<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  ${WIDGET_OVERRIDE_CSS}
  ${WIDGET_SCRIPT}
</head>
<body style="position:relative;width:100%;height:100%;">${content}</body>
</html>`;
    }
    const blob = new Blob([html], { type: "text/html" });
    const url  = URL.createObjectURL(blob);
    if (blobUrlRef.current) URL.revokeObjectURL(blobUrlRef.current);
    blobUrlRef.current = url;
    if (iframeRef.current) iframeRef.current.src = url;
    resetZoom();
    setIsFullscreen(false);
    return () => {
      if (blobUrlRef.current) {
        URL.revokeObjectURL(blobUrlRef.current);
        blobUrlRef.current = null;
      }
    };
  }, [content]);

  const handleReplay = () => {
    if (!content) return;
    // Don't replay if the user just finished dragging/panning.
    if (dragState.current.moved) { dragState.current.moved = false; return; }
    iframeRef.current?.contentWindow?.postMessage({ type: "REPLAY" }, "*");
    setShowHint(true);
    if (hintTimer.current) clearTimeout(hintTimer.current);
    hintTimer.current = setTimeout(() => setShowHint(false), 1500);
  };

  return (
    <div
      onClick={content && !usesNativeInteraction ? handleReplay : undefined}
      onMouseEnter={() => content && setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onWheel={onWheel}
      onMouseDown={onMouseDownPan}
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
      style={{
        position: "relative",
        width: "100%",
        height: "100%",
        background: "#e7e8e9",
        backgroundImage: `
          linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)
        `,
        backgroundSize: "32px 32px",
        overflow: "hidden",
        cursor: content ? (usesNativeInteraction ? "default" : (zoom > 1 ? "grab" : "pointer")) : "default",
      }}
    >
      <AnimatePresence>
        {content && !usesNativeInteraction && (isHovered || showHint) && (
          <motion.div
            key="replay-hint"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.18 }}
            style={{
              position: "absolute",
              bottom: 14,
              left: "50%",
              transform: "translateX(-50%)",
              zIndex: 25,
              background: showHint ? "rgba(99,80,219,0.85)" : "rgba(0,0,0,0.5)",
              color: "#fff",
              fontSize: 11,
              fontFamily: "sans-serif",
              padding: "4px 13px",
              borderRadius: 20,
              pointerEvents: "none",
              whiteSpace: "nowrap",
              letterSpacing: "0.02em",
            }}
          >
            {showHint
              ? (lang === "ar" ? "▶ جارٍ الإعادة..." : "▶ Rejouer...")
              : (lang === "ar" ? "انقر للإعادة" : "Cliquer pour rejouer")}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Zoom controls — canvas/interactive_svg widgets only.
          Mindmaps AND non-canonical widgets (real pointer events, own
          buttons) skip these: manual +/- would just fight with the
          widget's native interaction once pointerEvents is auto. */}
      {content && !usesNativeInteraction && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute",
            top: 10,
            right: 10,
            zIndex: 30,
            display: "flex",
            flexDirection: "column",
            gap: 4,
          }}
        >
          <button
            onClick={() => zoomBy(0.4)}
            style={zoomBtnStyle}
            aria-label="zoom in"
          >
            +
          </button>
          <button
            onClick={() => zoomBy(-0.4)}
            style={zoomBtnStyle}
            aria-label="zoom out"
          >
            −
          </button>
          {zoom > 1 && (
            <button
              onClick={resetZoom}
              style={{ ...zoomBtnStyle, fontSize: 10 }}
              aria-label="reset zoom"
            >
              ⟲
            </button>
          )}
        </div>
      )}

      {/* Expand-to-fullscreen control — mindmap widgets only */}
      {content && isMindmap && (
        <div
          onClick={(e) => e.stopPropagation()}
          style={{
            position: "absolute",
            top: 10,
            right: 10,
            zIndex: 30,
          }}
        >
          <button
            onClick={() => setIsFullscreen(true)}
            style={darkGrayBtnStyle}
            aria-label="expand mindmap"
            title={lang === "ar" ? "تكبير الخريطة" : "Agrandir la carte"}
          >
            ⤢
          </button>
        </div>
      )}

      {/* Scalable / pannable wrapper around the iframe */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: "center center",
          transition: dragState.current.dragging ? "none" : "transform 0.12s ease-out",
          willChange: "transform",
        }}
      >
        <iframe
          ref={iframeRef}
          style={{
            position: "absolute",
            inset: 0,
            width: "100%",
            height: "100%",
            border: "none",
            display: "block",
            zIndex: 1,
            background: "transparent",
            // FIX: non-canonical widgets (missing the canonical
            // btnPlay/btnPrev/btnNext/btnReset ids) now get real pointer
            // events too, same as mindmaps, so their own on-page buttons
            // are actually clickable instead of silently inert.
            pointerEvents: usesNativeInteraction ? "auto" : "none",
          }}
          sandbox="allow-scripts"
          title="interactive geometry widget"
        />
      </div>

      {/* Fullscreen mindmap modal — reuses the same blob URL, rendered
          at a much larger size for readability. Only ever mounted for
          mindmap widgets. Click on the backdrop or the ✕ button closes it. */}
      {isMindmap && isFullscreen && (
        <div
          onClick={() => setIsFullscreen(false)}
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1000,
            background: "rgba(10,14,30,0.92)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "4vh 4vw",
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              position: "relative",
              width: "92vw",
              height: "92vh",
              background: "#1a2035",
              borderRadius: 16,
              overflow: "hidden",
              boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
            }}
          >
            <button
              onClick={() => setIsFullscreen(false)}
              aria-label="close"
              title={lang === "ar" ? "إغلاق" : "Fermer"}
              style={{
                position: "absolute",
                top: 10,
                right: 10,
                zIndex: 10,
                ...darkGrayBtnStyle,
              }}
            >
              ✕
            </button>
            <iframe
              src={blobUrlRef.current ?? undefined}
              style={{
                position: "absolute",
                inset: 0,
                width: "100%",
                height: "100%",
                border: "none",
                background: "transparent",
              }}
              sandbox="allow-scripts"
              title="mindmap fullscreen"
            />
          </div>
        </div>
      )}
    </div>
  );
}

const zoomBtnStyle: React.CSSProperties = {
  width: 28,
  height: 28,
  borderRadius: 8,
  border: "1px solid rgba(255,255,255,0.18)",
  background: "rgba(255,255,255,0.10)",
  color: "rgba(255,255,255,0.85)",
  fontSize: 16,
  lineHeight: 1,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

const darkGrayBtnStyle: React.CSSProperties = {
  width: 30,
  height: 30,
  borderRadius: 8,
  border: "1px solid rgba(255,255,255,0.16)",
  background: "#787f8c",
  color: "#ffffff",
  fontSize: 16,
  lineHeight: 1,
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  boxShadow: "0 2px 6px rgba(0,0,0,0.3)",
};

// ── DraggableAvatar ───────────────────────────────────────────────────────────
function DraggableAvatar({ isSpeaking }: { isSpeaking: boolean }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState({ x: 16, y: 16 });
  const dragging = useRef(false);
  const offset   = useRef({ x: 0, y: 0 });

  const onMouseDown = (e: React.MouseEvent) => {
    dragging.current = true;
    offset.current = {
      x: e.clientX - pos.x,
      y: e.clientY - pos.y,
    };
    e.preventDefault();
  };

  const onTouchStart = (e: React.TouchEvent) => {
    const touch = e.touches[0];
    dragging.current = true;
    offset.current = {
      x: touch.clientX - pos.x,
      y: touch.clientY - pos.y,
    };
  };

  useEffect(() => {
    const BOX_SIZE = 180;

    const onMove = (e: MouseEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const parent = containerRef.current.parentElement!;
      const rect   = parent.getBoundingClientRect();
      const nx = Math.max(0, Math.min(e.clientX - offset.current.x, rect.width  - BOX_SIZE));
      const ny = Math.max(0, Math.min(e.clientY - offset.current.y, rect.height - BOX_SIZE));
      setPos({ x: nx, y: ny });
    };

    const onTouchMove = (e: TouchEvent) => {
      if (!dragging.current || !containerRef.current) return;
      const touch = e.touches[0];
      const parent = containerRef.current.parentElement!;
      const rect   = parent.getBoundingClientRect();
      const nx = Math.max(0, Math.min(touch.clientX - offset.current.x, rect.width  - BOX_SIZE));
      const ny = Math.max(0, Math.min(touch.clientY - offset.current.y, rect.height - BOX_SIZE));
      setPos({ x: nx, y: ny });
      e.preventDefault();
    };

    const onUp = () => { dragging.current = false; };

    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup",   onUp);
    window.addEventListener("touchmove", onTouchMove, { passive: false });
    window.addEventListener("touchend",  onUp);

    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup",   onUp);
      window.removeEventListener("touchmove", onTouchMove);
      window.removeEventListener("touchend",  onUp);
    };
  }, []);

  return (
    <div
      ref={containerRef}
      onMouseDown={onMouseDown}
      onTouchStart={onTouchStart}
      style={{
        position:     "absolute",
        left:         pos.x,
        top:          pos.y,
        width:        180,
        height:       180,
        borderRadius: 22,
        overflow:     "hidden",
        cursor:       "grab",
        boxShadow:    "0 6px 24px rgba(0,0,0,0.15), 0 1px 4px rgba(0,0,0,0.10)",
        border:       "2.5px solid rgba(99,80,219,0.30)",
        userSelect:   "none",
        zIndex:       10,
        background:   "#fff",
        outline:      isSpeaking ? "3px solid rgba(99,80,219,0.55)" : "none",
        outlineOffset: isSpeaking ? "2px" : "0px",
        transition:   "outline 0.2s ease, outline-offset 0.2s ease",
      }}
    >
      <div style={{ width: "100%", height: "100%" }}>
        <AvatarPanel isSpeaking={isSpeaking} />
      </div>
    </div>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────
function Sidebar({
  open, conversations, activeId, lang,
  onSelect, onNew, onDelete, onClose,
}: {
  open: boolean;
  conversations: Conversation[];
  activeId: string;
  lang: "ar" | "fr";
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onClose: () => void;
}) {
  const isRTL = lang === "ar";
  const todayStart     = new Date().setHours(0, 0, 0, 0);
  const yesterdayStart = todayStart - 86_400_000;
  const weekStart      = todayStart - 6 * 86_400_000;

  const groups: { label: string; items: Conversation[] }[] = [
    { label: st("today",       lang), items: conversations.filter(c => c.updatedAt >= todayStart) },
    { label: st("yesterday",   lang), items: conversations.filter(c => c.updatedAt >= yesterdayStart && c.updatedAt < todayStart) },
    { label: st("last_7_days", lang), items: conversations.filter(c => c.updatedAt >= weekStart     && c.updatedAt < yesterdayStart) },
    { label: st("older",       lang), items: conversations.filter(c => c.updatedAt < weekStart) },
  ].filter(g => g.items.length > 0);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
            className="absolute inset-0 bg-foreground/20 backdrop-blur-sm z-30"
          />
          <motion.aside
            initial={{ x: "100%" }} animate={{ x: 0 }} exit={{ x: "100%" }}
            transition={{ type: "tween", duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            dir={isRTL ? "rtl" : "ltr"}
            className="absolute top-0 right-0 h-full w-72 bg-card border-l border-border z-40 flex flex-col shadow-xl"
          >
            <div className="h-14 flex items-center justify-between px-3 border-b border-border shrink-0">
              <span className="text-sm font-semibold text-foreground px-1">{st("history", lang)}</span>
              <div className="flex items-center gap-1">
                <button onClick={onNew} className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground" title={st("new_conversation", lang)}>
                  <Plus className="w-4 h-4" />
                </button>
                <button onClick={onClose} className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground lg:hidden" aria-label={st("close", lang)}>
                  <X className="w-4 h-4" />
                </button>
              </div>
            </div>

            <div className="px-3 pt-3 pb-1 shrink-0">
              <button onClick={onNew} className="w-full flex items-center gap-2 px-3 py-2.5 rounded-xl border border-dashed border-border hover:bg-muted transition-colors text-sm text-muted-foreground font-medium">
                <Plus className="w-4 h-4" />
                {st("new_conversation", lang)}
              </button>
            </div>

            <div className="flex-1 overflow-y-auto px-2 py-1 space-y-4">
              {conversations.length === 0 && (
                <p className="text-xs text-muted-foreground text-center pt-8 px-4">
                  {st("no_conversations", lang)}
                </p>
              )}
              {groups.map(group => (
                <div key={group.label}>
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground px-2 py-1">
                    {group.label}
                  </p>
                  <div className="space-y-0.5">
                    {group.items.map(conv => (
                      <div
                        key={conv.id}
                        onClick={() => onSelect(conv.id)}
                        className={`group flex items-center gap-2 px-2 py-2 rounded-lg cursor-pointer transition-colors ${
                          conv.id === activeId
                            ? "bg-muted text-foreground"
                            : "hover:bg-muted/60 text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        <MessageSquare className="w-3.5 h-3.5 shrink-0 opacity-60" />
                        <span className="flex-1 text-xs truncate leading-snug">{conv.title}</span>
                        <button
                          onClick={(e) => { e.stopPropagation(); onDelete(conv.id); }}
                          className="opacity-0 group-hover:opacity-100 p-1 rounded hover:bg-destructive/10 hover:text-destructive transition-all shrink-0"
                          title={st("delete", lang)}
                        >
                          <Trash2 className="w-3 h-3" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}

// ── Chat (main) ───────────────────────────────────────────────────────────────
export default function Chat() {
  const lang = useLang();
  const { user, signOut } = useAuth();
  const username = getUserPref(user?.id, "username", "Élève");

  const [accountMenuOpen, setAccountMenuOpen] = useState(false);
  const accountMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!accountMenuOpen) return;
    const handleClickOutside = (e: MouseEvent) => {
      if (accountMenuRef.current && !accountMenuRef.current.contains(e.target as Node)) {
        setAccountMenuOpen(false);
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAccountMenuOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    document.addEventListener("keydown", handleEscape);
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [accountMenuOpen]);

  const handleLogout = async () => {
    setAccountMenuOpen(false);
    try {
      await signOut();
      localStorage.removeItem(STORAGE_KEY);
      localStorage.removeItem(ACTIVE_KEY);
      window.location.href = "/";
    } catch (err) {
      console.error("Logout failed:", err);
    }
  };

  const [sidebarOpen, setSidebarOpen] = useState(false);

  const [conversations, setConversations] = useState<Conversation[]>(() => {
    const saved = loadConversationsFromStorage();
    const initLang = (localStorage.getItem("profia-lang") as "ar" | "fr") || "fr";
    return saved.length > 0 ? saved : [createNewConversation(initLang)];
  });

  const [activeId, setActiveId] = useState<string>(() => {
    const saved = localStorage.getItem(ACTIVE_KEY);
    const convs = loadConversationsFromStorage();
    return (saved && convs.find(c => c.id === saved)) ? saved : (convs[0]?.id ?? "");
  });

  const activeConv = conversations.find(c => c.id === activeId) ?? conversations[0];
  const messages   = activeConv?.messages ?? [];

  const [input, setInput]                   = useState("");
  const [isTyping, setIsTyping]             = useState(false);
  const [avatarOpen, setAvatarOpen]         = useState(false);
  const [avatarSpeaking, setAvatarSpeaking] = useState(false);
  const [canvasLabel, setCanvasLabel]       = useState("");

  const [pinnedWidgetId, setPinnedWidgetId] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef  = useRef<HTMLInputElement>(null);

  // FIX (save guard): the new "save after streaming" effect below must not
  // push local state to Supabase before the initial remote load/merge has
  // finished — otherwise a stale local copy could overwrite a newer remote
  // one. Flipped to true once the load/merge effect completes (success or
  // failure).
  const remoteLoadedRef = useRef(false);

  // ── Audio playback queue (NEW) ──────────────────────────────────────────
  // Backend (stream_handler.py / tts_chunker.py) streams one SSE
  // "audio_chunk" event per synthesized [SPEECH] sentence, ALREADY in
  // playback order (tts_chunker.py's _pending queue guarantees index N
  // is popped/sent before index N+1, even if N+1's ElevenLabs call
  // happened to finish synthesizing first). This queue's only job is
  // sequential client-side playback: play chunk, wait for it to end,
  // play the next one — no reordering logic needed here.
  //
  // avatarSpeaking is now driven by ACTUAL audio playback (audio.onplay/
  // onended below), not by isTyping (whether the LLM is still streaming
  // text) or by the old WebSocket+speechSynthesis pipeline that used to
  // set it (removed — see git history / previous version of this file;
  // it talked to a different endpoint, /ws/avatar, that has nothing to do
  // with the ElevenLabs audio_chunk events this queue now plays).
  const audioQueueRef   = useRef<{ index: number; url: string }[]>([]);
  const isPlayingRef    = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);

  const stopAndClearAudioQueue = () => {
    audioQueueRef.current = [];
    isPlayingRef.current = false;
    if (currentAudioRef.current) {
      currentAudioRef.current.onended = null;
      currentAudioRef.current.onerror = null;
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    setAvatarSpeaking(false);
  };

  const playNextInAudioQueue = () => {
    if (isPlayingRef.current) return;
    const next = audioQueueRef.current.shift();
    if (!next) {
      setAvatarSpeaking(false);
      return;
    }
    isPlayingRef.current = true;
    setAvatarSpeaking(true);
    const audio = new Audio(next.url);
    currentAudioRef.current = audio;
    const advance = () => {
      isPlayingRef.current = false;
      currentAudioRef.current = null;
      playNextInAudioQueue();
    };
    audio.onended = advance;
    // A single failed chunk (bad base64, decode error, ...) must never
    // wedge the rest of the turn's narration — skip it and keep going,
    // same fail-open philosophy as the backend's own TTS error handling.
    audio.onerror = advance;
    audio.play().catch(advance);
  };

  const enqueueAudioChunk = (chunk: AudioChunkPayload) => {
    const url = `data:audio/mpeg;base64,${chunk.audio_base64}`;
    // Defensive sort by index: the backend already guarantees ordered
    // delivery (see tts_chunker.py), so in practice this is a no-op —
    // kept as a cheap safety net rather than a load-bearing reorder step.
    audioQueueRef.current.push({ index: chunk.index, url });
    audioQueueRef.current.sort((a, b) => a.index - b.index);
    playNextInAudioQueue();
  };

  const latestWidgetMsg = [...messages].reverse().find(m => m.role === "ai" && m.hasWidget);
  const pinnedWidgetMsg = pinnedWidgetId
    ? messages.find(m => m.id === pinnedWidgetId && m.hasWidget)
    : undefined;
  const activeWidgetMsg = pinnedWidgetMsg ?? latestWidgetMsg;

  const canvasContent = (() => {
    if (!activeWidgetMsg) return "";
    const raw = activeWidgetMsg.content ?? "";
    return extractWidgetContent(raw);
  })();

  const getWidgetMessageForIndex = (i: number): Message | undefined => {
    const msg = messages[i];
    if (!msg) return undefined;
    if (msg.role === "ai") return msg.hasWidget ? msg : undefined;
    const next = messages[i + 1];
    return next?.role === "ai" && next.hasWidget ? next : undefined;
  };

  useEffect(() => {
    setPinnedWidgetId(null);
  }, [activeId]);

  // FIX: persist conversations to localStorage whenever they change.
  // Previously saveConversations() was defined but never called, so
  // localStorage stayed stale and refreshing the page always reloaded
  // an old/frozen snapshot instead of the current conversation list.
  useEffect(() => {
    saveConversations(conversations);
  }, [conversations]);

  // FIX: persist the active conversation id to localStorage whenever it
  // changes. Previously ACTIVE_KEY was only ever read, never written, so
  // on refresh it was always missing/stale and the app fell back to
  // conversations[0] every time — always landing on the same conversation.
  useEffect(() => {
    if (activeId) localStorage.setItem(ACTIVE_KEY, activeId);
  }, [activeId]);

  // FIX (conversation-loss race): this used to be an unconditional
  // setConversations(remote) whenever `user.id` became available. If the
  // person started chatting before Supabase auth finished resolving
  // (plausible: local state is usable immediately from localStorage, but
  // `user` here is still null/undefined for a beat after mount), any
  // message sent during that window was appended to LOCAL state only —
  // updateConversation()'s `if (user?.id && updatedConv)` guard skips the
  // Supabase upsert whenever user.id isn't ready yet. The next tick, this
  // effect fires, fetches whatever was already in Supabase from BEFORE
  // this session, and replaces state wholesale — silently deleting the
  // in-progress conversation from state and localStorage, and (since it
  // was never upserted) from Supabase too. activeId then pointed at
  // nothing in the new list and fell back to remote[0].id — exactly "my
  // conversation turned into a different/new one".
  //
  // Fix: merge instead of overwrite. A conversation that's local-only, or
  // newer locally than its remote copy, is kept — and queued below to be
  // pushed TO Supabase, instead of this effect only ever pulling FROM it.
  useEffect(() => {
    if (!user?.id) return;
    const userId = user.id;
    (async () => {
      try {
        const remote = await loadConversationsFromSupabase(userId);
        const remoteById = new Map(remote.map(c => [c.id, c]));

        const toUpsert: Conversation[] = [];
        let mergedResult: Conversation[] = [];

        setConversations(prev => {
          const merged = new Map<string, Conversation>(remoteById);
          for (const local of prev) {
            const remoteVersion = merged.get(local.id);
            if (!remoteVersion || local.updatedAt > remoteVersion.updatedAt) {
              merged.set(local.id, local);
            }
          }
          mergedResult = Array.from(merged.values()).sort((a, b) => b.updatedAt - a.updatedAt);
          return mergedResult;
        });

        // Side effects AFTER setConversations() returns — the updater
        // passed to setConversations must stay pure (no network calls
        // inside it).
        saveConversations(mergedResult);
        for (const c of mergedResult) {
          const remoteVersion = remoteById.get(c.id);
          if (!remoteVersion || c.updatedAt > remoteVersion.updatedAt) {
            toUpsert.push(c);
          }
        }
        toUpsert.forEach(c => upsertConversation(c, userId).catch(() => {}));

        const savedActive = localStorage.getItem(ACTIVE_KEY);
        if (savedActive && mergedResult.find(c => c.id === savedActive)) {
          setActiveId(savedActive);
        } else if (mergedResult.length > 0) {
          setActiveId(mergedResult[0].id);
        }
      } catch {}
      // FIX (save guard): remote load/merge is done (or failed) — the
      // save-after-streaming effect below is now allowed to write.
      remoteLoadedRef.current = true;
    })();
  }, [user?.id]);

  useEffect(() => {
    const handler = (e: MessageEvent) => {
      if (e.data?.type === "CANVAS_LABEL") {
        setCanvasLabel(e.data.text ?? "");
      }
    };
    window.addEventListener("message", handler);
    return () => window.removeEventListener("message", handler);
  }, []);

  useEffect(() => { setCanvasLabel(""); }, [canvasContent]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
    if (isNearBottom) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, [messages]);

  // Stop any in-flight audio playback if the component unmounts mid-turn
  // (e.g. navigating away) — otherwise a chunk could keep playing over a
  // screen that no longer shows the conversation it belongs to.
  useEffect(() => {
    return () => stopAndClearAudioQueue();
  }, []);

  // FIX (truncated conversations in Supabase — THE main bug): updateConversation
  // used to capture `updatedConv` inside the setConversations updater and then
  // check it right after setConversations() returned. React does not guarantee
  // the updater runs synchronously, so `updatedConv` was often still undefined
  // at that point and the upsert was silently skipped — including the final
  // onDone save, which left the partial streamed text in Supabase.
  //
  // updateConversation is now a pure state update (and bumps updatedAt on
  // every change so the load-time merge correctly prefers the newest copy).
  // Saving to Supabase is handled by the effect below, which always sees the
  // latest committed state and waits until streaming has stopped.
  const updateConversation = (id: string, updater: (c: Conversation) => Conversation) => {
    setConversations(prev =>
      prev.map(c => (c.id === id ? { ...updater(c), updatedAt: Date.now() } : c))
    );
  };

  // FIX: save the active conversation to Supabase once streaming has stopped
  // (debounced 600 ms), always from the latest committed state — never mid-
  // stream, never from a stale closure. Waits for the initial remote merge
  // (remoteLoadedRef) so a stale local copy can't overwrite a newer remote one.
  useEffect(() => {
    if (!user?.id || isTyping || !remoteLoadedRef.current) return;
    const conv = conversations.find(c => c.id === activeId);
    if (!conv || conv.messages.length === 0) return;
    const userId = user.id;
    const h = setTimeout(() => {
      upsertConversation(conv, userId).catch(err => console.error("save failed", err));
    }, 600);
    return () => clearTimeout(h);
  }, [conversations, activeId, isTyping, user?.id]);

  const handleNewConversation = () => {
    const nc = createNewConversation(lang);
    setConversations(prev => [nc, ...prev]);
    setActiveId(nc.id);
    setSidebarOpen(false);
    if (user?.id) upsertConversation(nc, user.id).catch(() => {});
  };

  const handleSelectConversation = (id: string) => {
    setActiveId(id);
    setSidebarOpen(false);
  };

  // FIX (same class of bug as updateConversation above): setActiveId() and
  // upsertConversation() used to be called from INSIDE the setConversations
  // updater. Now the updater only computes/returns the new array and
  // records what happened (new active id / newly-created conversation,
  // if any) into local variables; both side effects run once, after
  // setConversations() returns.
  const handleDeleteConversation = (id: string) => {
    if (user?.id) deleteConversationFromSupabase(id).catch(() => {});

    let newActiveId: string | undefined;
    let createdConv: Conversation | undefined;

    setConversations(prev => {
      const next = prev.filter(c => c.id !== id);
      if (next.length === 0) {
        const nc = createNewConversation(lang);
        createdConv = nc;
        newActiveId = nc.id;
        return [nc];
      }
      if (id === activeId) newActiveId = next[0].id;
      return next;
    });

    if (newActiveId) setActiveId(newActiveId);
    if (createdConv && user?.id) upsertConversation(createdConv, user.id).catch(() => {});
  };

  // FIX (generateTitle bypassing API_BASE/auth — silently broken in prod):
  // every other network call in this app goes through apiPost/apiGet
  // (prefixing API_BASE and attaching the Supabase auth header) or
  // manually replicates that pattern (askStreamSse). This function used a
  // raw fetch("/api/generate-title", ...) — a relative path (hits the
  // frontend's own origin instead of the backend whenever they're on
  // different origins, which API_BASE's existence implies they are) with
  // no auth header. That request would 404/fail, throw, and be silently
  // swallowed by the empty catch — so conversation titles never populated,
  // with zero error surfaced anywhere. Now routed through apiPost() like
  // the rest of the app.
  const generateTitle = async (firstUserMessage: string, convId: string) => {
    try {
      const { title } = await apiPost<{ title: string }>("/generate-title", {
        message: firstUserMessage,
      });
      if (title) updateConversation(convId, c => ({ ...c, title }));
    } catch {}
  };

  const sendMessage = async () => {
    if (!input.trim() || !activeConv) return;
    const question = input.trim();
    const isFirst  = activeConv.messages.length === 0;
    const convId   = activeId;

    const userMsg: Message = { id: Date.now().toString(), role: "user", content: question };
    const aiId             = (Date.now() + 1).toString();
    const aiMsg: Message   = { id: aiId, role: "ai", content: "" };

    const historySnapshot = activeConv.messages.map(m =>
      m.role === "ai" ? { ...m, content: m.teacher || "" } : m
    );

    updateConversation(activeId, c => ({
      ...c,
      messages:  [...c.messages, userMsg, aiMsg],
      title:     isFirst ? deriveTitleFromMessage(question) : c.title,
      updatedAt: Date.now(),
    }));

    setConversations(prev => {
      const idx = prev.findIndex(c => c.id === activeId);
      if (idx <= 0) return prev;
      const copy = [...prev];
      const [item] = copy.splice(idx, 1);
      return [item, ...copy];
    });

    setInput("");
    setIsTyping(true);
    setPinnedWidgetId(null);

    // New turn starting — drop any narration still queued/playing from the
    // previous turn instead of letting it bleed into this one.
    stopAndClearAudioQueue();

    // FIX (live streaming): accumulates teacher_chunk text as it arrives
    // (see stream_handler.py v3.2 / api.ts's onTeacherToken). This is raw,
    // not-yet-post-processed text — isolate_inline_math()/
    // normalize_exercise_layout() only run once, server-side, on the full
    // string in the "done" event — so onDone below still overwrites
    // `teacher` with the server's final, post-processed version once
    // streaming ends. This local variable only drives the live-typing
    // display in between.
    let teacherSoFar = "";

    try {
      await askStreamSse(question, convId, historySnapshot, {
        onToken: (_token) => {},
        onTeacherToken: (token) => {
          teacherSoFar += token;
          updateConversation(convId, c => ({
            ...c,
            messages: c.messages.map(m =>
              m.id === aiId ? { ...m, teacher: teacherSoFar } : m
            ),
          }));
        },
        // NEW: each [SPEECH] sentence's synthesized audio, as it's ready —
        // see api.ts's audio_chunk case. Enqueue-and-play; avatarSpeaking
        // now tracks real playback instead of isTyping.
        onAudioChunk: (chunk) => {
          enqueueAudioChunk(chunk);
        },
        // NEW: backend has no more audio_chunk events coming this turn.
        // Nothing to do here beyond bookkeeping — the queue keeps draining
        ///playing on its own; this does NOT mean playback has finished.
        onAudioDone: (_count) => {},
        onDone: (widget, teacher, representation) => {
          updateConversation(convId, c => ({
            ...c,
            messages: c.messages.map(m =>
              m.id === aiId
                ? {
                    ...m,
                    content:        (widget && widget.trim()) ? widget : m.content,
                    teacher:        teacher || undefined,
                    hasWidget:      Boolean(widget && widget.trim()),
                    representation: representation || undefined,
                  }
                : m
            ),
          }));
        },
        onError: (message) => {
          updateConversation(convId, c => ({
            ...c,
            messages: c.messages.map(m =>
              m.id === aiId
                ? { ...m, content: `❌ ${message}` }
                : m
            ),
          }));
        },
      });

      if (isFirst) generateTitle(question, convId);
    } catch (e: any) {
      updateConversation(convId, c => ({
        ...c,
        messages: c.messages.map(m =>
          m.id === aiId
            ? { ...m, content: `❌ Erreur: ${e?.message ?? "Erreur inconnue"}` }
            : m
        ),
      }));
    } finally {
      setIsTyping(false);
    }
  };

  const isRTL          = lang === "ar";
  const isLimitReached = messages.length >= 20;

  return (
    <div className="h-screen flex bg-background overflow-hidden" dir="ltr">

      {/* ── LEFT PANEL — canvas (5/12) ── */}
      <div className="hidden lg:flex lg:flex-col w-[45%] shrink-0 border-r border-border overflow-hidden">

        {/* Board — 72% */}
        <div style={{ flex: "0 0 72%", minHeight: 0, position: "relative" }}>
          <CanvasPanel
            content={canvasContent}
            isSpeaking={isTyping || avatarSpeaking}
            lang={lang}
          />
        </div>

        {/* White lower zone — 28% — avatar floats freely inside */}
        <div
          style={{
            flex:       "0 0 28%",
            minHeight:  0,
            position:   "relative",
            background: "#ffffff",
            overflow:   "hidden",
            borderTop:  "1px solid rgba(0,0,0,0.07)",
          }}
        >
          <DraggableAvatar isSpeaking={isTyping || avatarSpeaking} />
        </div>

      </div>

      {/* ── RIGHT PANEL — chat (7/12) ── */}
      {/* FIX: this wrapper's dir was previously tied to the UI language
          (dir={isRTL ? "rtl" : "ltr"}). Since it is the flex ancestor of
          the message rows below (which use justify-end/justify-start),
          flipping it to "rtl" silently reversed those logical alignment
          keywords — user and AI bubbles swapped physical sides whenever
          the interface language was Arabic. dir is now hardcoded to "ltr"
          so bubble alignment is always tied to role, never to locale.
          Nothing else in this wrapper changed; child elements (header,
          sidebar, input) already set their own dir independently and are
          unaffected. */}
      <div className="w-full lg:w-[55%] shrink-0 flex flex-col min-w-0 relative" dir="ltr">

        <Sidebar
          open={sidebarOpen}
          conversations={conversations}
          activeId={activeId}
          lang={lang}
          onSelect={handleSelectConversation}
          onNew={handleNewConversation}
          onDelete={handleDeleteConversation}
          onClose={() => setSidebarOpen(false)}
        />

        {/* Header */}
        <div className="h-14 border-b border-border flex flex-row items-center justify-between px-4 shrink-0 glass" dir="ltr">
          <div className="flex flex-row items-center gap-2 shrink-0">
            <button
              onClick={() => setAvatarOpen(true)}
              className="lg:hidden p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground"
              aria-label={t("show_avatar", lang)}
            >
              <User className="w-4 h-4" />
            </button>
            <div className="relative" ref={accountMenuRef}>
              <button
                onClick={() => setAccountMenuOpen(v => !v)}
                className="flex flex-row items-center gap-2 shrink-0 p-1 rounded-lg hover:bg-muted transition-colors"
              >
                <span className="hidden sm:block text-sm font-medium text-foreground">{username}</span>
                <div className="w-7 h-7 rounded-full gradient-primary flex items-center justify-center text-[10px] text-primary-foreground font-bold">
                  {username[0]?.toUpperCase()}
                </div>
              </button>

              <AnimatePresence>
                {accountMenuOpen && (
                  <motion.div
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                    className="absolute top-full left-0 mt-2 w-44 rounded-xl bg-popover border border-border shadow-md z-50 overflow-hidden"
                  >
                    <button
                      onClick={handleLogout}
                      className="w-full flex items-center gap-2 px-3 py-2.5 text-sm text-foreground hover:bg-muted transition-colors"
                    >
                      <LogOut className="w-4 h-4" />
                      {st("logout", lang)}
                    </button>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>

          <div className="flex flex-row items-center gap-1 shrink-0">
            <div className="relative group">
              <button onClick={handleNewConversation} className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground">
                <Plus className="w-4 h-4" />
              </button>
              <span className="pointer-events-none absolute top-full right-0 mt-2 px-2 py-1 rounded-md bg-popover border border-border text-xs text-foreground whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity shadow-md z-50">
                {st("new_conversation", lang)}
              </span>
            </div>
            <div className="relative group">
              <button onClick={() => setSidebarOpen(v => !v)} className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground">
                <PanelRight className="w-4 h-4" />
              </button>
              <span className="pointer-events-none absolute top-full right-0 mt-2 px-2 py-1 rounded-md bg-popover border border-border text-xs text-foreground whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity shadow-md z-50">
                {st("history", lang)}
              </span>
            </div>
          </div>
        </div>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center space-y-4 opacity-60">
              <div className="w-16 h-16 rounded-2xl gradient-primary flex items-center justify-center animate-breathe">
                <span className="text-2xl">🤖</span>
              </div>
              <p className="text-muted-foreground text-sm max-w-xs">
                {st("empty_prompt", lang)}
              </p>
            </div>
          )}

          {messages.map((msg, i) => {
            // Only AI (output) messages are clickable to replay their canvas.
            // User input bubbles are never wrapped in the clickable div.
            const widgetMsg = msg.role === "ai" ? getWidgetMessageForIndex(i) : undefined;
            const isActiveWidget = !!widgetMsg && activeWidgetMsg?.id === widgetMsg.id;

            return (
                <motion.div
                  key={msg.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3 }}
                  className={`flex w-full ${msg.role === "user" ? "justify-start" : "justify-end"}`}
                >
                {/* FIX: wrapper made a flex container with minWidth: 0 and
                    maxWidth: 100% so it never collapses the bubble down to a
                    single-character width (which caused short messages like
                    "oui" to render one letter per line). */}
                {msg.role === "user" ? (
                  // User bubbles: no wrapper, no click behavior, no extra layout logic.
                  <MessageBubble
                    msg={msg}
                    lang={lang}
                    isStreaming={false}
                  />
                ) : (
                  <>
                    <div
                      onClick={widgetMsg ? () => setPinnedWidgetId(widgetMsg.id) : undefined}
                      title={widgetMsg ? st("view_drawing", lang) : undefined}
                      style={{
                        display: "flex",
                        justifyContent: "flex-end",
                        minWidth: 0,
                        maxWidth: "100%",
                        ...(widgetMsg
                          ? {
                              cursor: "pointer",
                              borderRadius: "1rem",
                              outline: isActiveWidget ? "2px solid rgba(99,80,219,0.55)" : "2px solid transparent",
                              outlineOffset: "2px",
                              transition: "outline 0.2s ease",
                            }
                          : {}),
                      }}
                    >
                      <MessageBubble
                        msg={msg}
                        lang={lang}
                        isStreaming={i === messages.length - 1 && isTyping}
                      />
                    </div>
                    {/* FIX: avatar now renders AFTER the bubble (not before)
                        so that, with justify-end on the row, it hugs the
                        outer/right edge instead of sitting on the bubble's
                        inner-left side. Margin flipped from mr-2 to ml-2
                        to keep the same visual gap on its new side. */}
                    <div className="w-7 h-7 rounded-full gradient-primary flex items-center justify-center text-[10px] text-primary-foreground font-bold shrink-0 mt-1 ml-2">
                      AI
                    </div>
                  </>
                )}
              </motion.div>
            );
          })}

        </div>

        {/* Input area */}
        <div className="border-t border-border">
          {messages.length >= 20 && (
            <div className="px-4 pt-3 pb-0 max-w-3xl mx-auto">
              <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-xl bg-amber-50 dark:bg-amber-950/40 border border-amber-200 dark:border-amber-800">
                <p className="text-xs text-amber-700 dark:text-amber-400 font-medium">
                  {st("limit_reached", lang)}
                </p>
                <button
                  onClick={handleNewConversation}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-600 text-white text-xs font-semibold transition-colors shrink-0"
                >
                  <Plus className="w-3.5 h-3.5" />
                  {st("start_new", lang)}
                </button>
              </div>
            </div>
          )}

          <div className="p-4 max-w-3xl mx-auto flex items-center gap-2">
            <motion.button
              whileHover={{ scale: isLimitReached ? 1 : 1.05 }}
              whileTap={{ scale: isLimitReached ? 1 : 0.95 }}
              onClick={sendMessage}
              disabled={isLimitReached}
              className="p-2.5 rounded-xl gradient-primary text-primary-foreground shadow-soft disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Send className="w-5 h-5" />
            </motion.button>
            <input
              ref={inputRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && !isLimitReached && sendMessage()}
              placeholder={t("ask_question", lang)}
              dir={isRTL ? "rtl" : "ltr"}
              disabled={messages.length >= 20}
              className="flex-1 px-4 py-3 rounded-xl bg-muted/50 border border-border text-base focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            />
            <button className="p-2.5 rounded-xl hover:bg-muted transition-colors text-muted-foreground">
              <Mic className="w-5 h-5" />
            </button>
          </div>
        </div>

      </div>

      {/* Mobile avatar drawer */}
      <AnimatePresence>
        {avatarOpen && (
          <>
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              onClick={() => setAvatarOpen(false)}
              className="fixed inset-0 bg-foreground/40 backdrop-blur-sm z-40 lg:hidden"
            />
            <motion.div
              initial={{ x: "-100%" }} animate={{ x: 0 }} exit={{ x: "-100%" }}
              transition={{ type: "tween", duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
              className="fixed top-0 left-0 h-full w-[85%] max-w-sm bg-background border-r border-border z-50 lg:hidden flex flex-col"
            >
              <div className="h-14 flex items-center justify-between px-4 border-b border-border shrink-0">
                <span className="font-semibold text-foreground">Prof IA</span>
                <button onClick={() => setAvatarOpen(false)} className="p-2 rounded-lg hover:bg-muted transition-colors text-muted-foreground">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="flex-1 min-h-0">
                <AvatarPanel isSpeaking={isTyping || avatarSpeaking} />
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>

    </div>
  );
}