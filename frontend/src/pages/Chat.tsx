import { useState, useRef, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Send, Mic, User, X, Plus, MessageSquare, Trash2, PanelRight } from "lucide-react";
import { supabase } from "@/integrations/supabase/client";
import { MessageBubble } from "@/components/MessageBubble";
import AvatarPanel from "@/components/AvatarPanel";
import { t, useLang } from "@/lib/i18n";
import { askStreamSse, AVATAR_WS_URL } from "@/lib/api";
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
    close:             { fr: "Fermer",                  ar: "إغلاب" },
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

async function upsertConversation(conv: Conversation, userId: string): Promise<void> {
  await db.from("conversations").upsert({
    id:         conv.id,
    user_id:    userId,
    title:      conv.title,
    messages:   conv.messages,
    updated_at: new Date(conv.updatedAt).toISOString(),
  });
}

async function deleteConversationFromSupabase(id: string): Promise<void> {
  await db.from("conversations").delete().eq("id", id);
}

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
  }
  #btnPlay, #btnPrev, #btnNext, #btnReset,
  [id^="btn"], button { display: none !important; }
  #lbl { display: none !important; }
  body > div, .wrap, div[style*="font-family"] {
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
  canvas {
    flex: 1 1 auto !important;
    width: 100% !important;
    height: auto !important;
    min-height: 0 !important;
    display: block !important;
    border: none !important;
    border-radius: 0 !important;
    background: transparent !important;
  }
  svg {
    width: 100% !important;
    height: 100% !important;
    display: block !important;
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
</style>`;

const WIDGET_SCRIPT = `
<script id="__host_ctrl__">
(function () {
  function autoPlay() {
    var reset = document.getElementById('btnReset');
    var play  = document.getElementById('btnPlay');
    if (reset) reset.click();
    setTimeout(function () { if (play) play.click(); }, 80);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      setTimeout(autoPlay, 300);
      watchLabel();
    });
  } else {
    setTimeout(autoPlay, 300);
    watchLabel();
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

  // ── zoom / pan state ──────────────────────────────────────────────
  const [zoom, setZoom] = useState(1);
  const [pan, setPan]   = useState({ x: 0, y: 0 });
  const MIN_ZOOM = 1;
  const MAX_ZOOM = 3;

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

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragState.current;
      if (!d.dragging) return;
      const dx = e.clientX - d.startX;
      const dy = e.clientY - d.startY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) d.moved = true;
      const { x, y } = clampPan(d.startPan.x + dx, d.startPan.y + dy, zoom);
      setPan({ x, y });
    };
    const onUp = () => { dragState.current.dragging = false; };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [zoom]);

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
      onClick={content ? handleReplay : undefined}
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
        background: "#1a2035",
        backgroundImage: `
          linear-gradient(rgba(255,255,255,0.03) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255,255,255,0.03) 1px, transparent 1px)
        `,
        backgroundSize: "32px 32px",
        overflow: "hidden",
        cursor: content ? (zoom > 1 ? "grab" : "pointer") : "default",
      }}
    >
      <AnimatePresence>
        {content && (isHovered || showHint) && (
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

      {/* Zoom controls */}
      {content && (
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
            pointerEvents: "none",
          }}
          sandbox="allow-scripts"
          title="interactive geometry widget"
        />
      </div>
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
  const { user } = useAuth();
  const username = getUserPref(user?.id, "username", "Élève");

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
  const wsRef     = useRef<WebSocket | null>(null);

  const latestWidgetMsg = [...messages].reverse().find(m => m.role === "ai" && m.hasWidget);
  const pinnedWidgetMsg = pinnedWidgetId
    ? messages.find(m => m.id === pinnedWidgetId && m.hasWidget)
    : undefined;
  const activeWidgetMsg = pinnedWidgetMsg ?? latestWidgetMsg;

  const canvasContent = (() => {
    if (!activeWidgetMsg) return "";
    const raw = activeWidgetMsg.content ?? "";
    const match = raw.match(/```(?:interactive_svg|svg)\s*([\s\S]*?)```/);
    return match ? match[1].trim() : raw;
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

  useEffect(() => {
    if (!user?.id) return;
    (async () => {
      try {
        const remote = await loadConversationsFromSupabase(user.id);
        if (remote.length > 0) {
          setConversations(remote);
          saveConversations(remote);
          const savedActive = localStorage.getItem(ACTIVE_KEY);
          if (savedActive && remote.find(c => c.id === savedActive)) {
            setActiveId(savedActive);
          } else {
            setActiveId(remote[0].id);
          }
        }
      } catch {}
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

  useEffect(() => {
    let closed = false;
    let ws: WebSocket | null = null;
    try {
      ws = new WebSocket(AVATAR_WS_URL);
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        let token = "";
        try { const d = JSON.parse(ev.data); token = d.text ?? d.token ?? ""; }
        catch { token = String(ev.data); }
        if (!token) return;
        if (typeof window !== "undefined" && "speechSynthesis" in window) {
          const u = new SpeechSynthesisUtterance(token);
          u.lang    = lang === "ar" ? "ar-TN" : "fr-FR";
          u.onstart = () => setAvatarSpeaking(true);
          u.onend   = () => setAvatarSpeaking(false);
          window.speechSynthesis.speak(u);
        }
      };
      ws.onclose = () => { if (!closed) wsRef.current = null; };
      ws.onerror = () => {};
    } catch {}
    return () => {
      closed = true;
      try { ws?.close(); } catch {}
      if (typeof window !== "undefined" && "speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, [lang]);

  const updateConversation = (id: string, updater: (c: Conversation) => Conversation) => {
    setConversations(prev => {
      const next = prev.map(c => c.id === id ? updater(c) : c);
      if (user?.id) {
        const updated = next.find(c => c.id === id);
        if (updated) upsertConversation(updated, user.id).catch(() => {});
      }
      return next;
    });
  };

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

  const handleDeleteConversation = (id: string) => {
    if (user?.id) deleteConversationFromSupabase(id).catch(() => {});
    setConversations(prev => {
      const next = prev.filter(c => c.id !== id);
      if (next.length === 0) {
        const nc = createNewConversation(lang);
        setActiveId(nc.id);
        if (user?.id) upsertConversation(nc, user.id).catch(() => {});
        return [nc];
      }
      if (id === activeId) setActiveId(next[0].id);
      return next;
    });
  };

  const generateTitle = async (firstUserMessage: string, convId: string) => {
    try {
      const res = await fetch("/api/generate-title", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ message: firstUserMessage }),
      });
      if (!res.ok) throw new Error("title fetch failed");
      const { title } = await res.json();
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

    try {
      await askStreamSse(question, convId, historySnapshot, {
        onToken: (_token) => {},
        onDone: (widget, teacher) => {
          updateConversation(convId, c => ({
            ...c,
            messages: c.messages.map(m =>
              m.id === aiId
                ? {
                    ...m,
                    content:   widget || m.content,
                    teacher:   teacher || undefined,
                    hasWidget: Boolean(widget),
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
      <div className="w-full lg:w-[55%] shrink-0 flex flex-col min-w-0 relative" dir={isRTL ? "rtl" : "ltr"}>

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
            <span className="hidden sm:block text-sm font-medium text-foreground">{username}</span>
            <div className="w-7 h-7 rounded-full gradient-primary flex items-center justify-center text-[10px] text-primary-foreground font-bold">
              {username[0]?.toUpperCase()}
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
                  className={`flex w-full ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                >
                {msg.role === "ai" && (
                  <div className="w-7 h-7 rounded-full gradient-primary flex items-center justify-center text-[10px] text-primary-foreground font-bold shrink-0 mt-1 mr-2">
                    AI
                  </div>
                )}

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
                  <div
                    onClick={widgetMsg ? () => setPinnedWidgetId(widgetMsg.id) : undefined}
                    title={widgetMsg ? st("view_drawing", lang) : undefined}
                    style={{
                      display: "flex",
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
            <button className="p-2.5 rounded-xl hover:bg-muted transition-colors text-muted-foreground">
              <Mic className="w-5 h-5" />
            </button>
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
            <motion.button
              whileHover={{ scale: isLimitReached ? 1 : 1.05 }}
              whileTap={{ scale: isLimitReached ? 1 : 0.95 }}
              onClick={sendMessage}
              disabled={isLimitReached}
              className="p-2.5 rounded-xl gradient-primary text-primary-foreground shadow-soft disabled:opacity-40 disabled:cursor-not-allowed"
            >
              <Send className="w-5 h-5" />
            </motion.button>
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