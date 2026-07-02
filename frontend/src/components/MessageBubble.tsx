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
// ─────────────────────────────────────────────────────────────────────────────

import { useRef, useEffect, memo } from "react";

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

function applyLayout(el: HTMLElement, role: Message["role"], lang: "ar" | "fr") {
  if (role === "user") {
    // Let the browser's bidi algorithm decide direction from content
    // instead of forcing rtl, which combined with shrink-to-fit sizing
    // was causing short strings to collapse to 1-character width.
    el.style.direction = "ltr";
    el.style.textAlign = lang === "ar" ? "right" : "left";
    el.style.alignSelf    = "flex-end";
    el.style.borderRadius = "18px 18px 4px 18px";
  } else {
    el.style.direction = lang === "ar" ? "rtl" : "ltr";
    el.style.textAlign = lang === "ar" ? "right" : "left";
    el.style.alignSelf    = "flex-start";
    el.style.borderRadius = "18px 18px 18px 4px";
  }
}

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
    if (closeIdx === -1) break;

    const code = raw.slice(codeStart, closeIdx).trim();
    segments.push({ type: kind, code });
    cursor = closeIdx + 3;
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
}: {
  seg: Segment;
  isStreaming: boolean;
  index: number;
  msgId: string;
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

  const { marked } = window as any;
  const renderedHtml = marked ? marked.parse(seg.html) : seg.html;

  return (
    <div
      style={{ whiteSpace: "normal" }}
      className="text-foreground tracking-wide
                 [&>p]:mb-4 [&>p]:last:mb-0 [&>p]:leading-relaxed
                 [&>h2]:text-base [&>h2]:font-bold [&>h2]:mt-6 [&>h2]:mb-3 [&>h2]:text-primary
                 [&>h3]:text-sm [&>h3]:font-semibold [&>h3]:mt-4 [&>h3]:mb-2
                 [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:space-y-2 [&>ul]:mb-4
                 [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:space-y-2 [&>ol]:mb-4
                 [&>hr]:my-6 [&>hr]:border-border/60
                 [&>blockquote]:border-l-4 [&>blockquote]:border-primary [&>blockquote]:pl-4 [&>blockquote]:my-4 [&>blockquote]:italic [&>blockquote]:text-muted-foreground
                 [&>table]:w-full [&>table]:my-5 [&>table]:border-collapse [&>table]:text-xs
                 [&>table_th]:border [&>table_th]:border-border [&>table_th]:p-2.5 [&>table_th]:bg-background/50 [&>table_th]:font-semibold
                 [&>table_td]:border [&>table_td]:border-border [&>table_td]:p-2.5"
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
    applyLayout(el, msg.role, lang);
  }, [msg.role, lang]);

  useEffect(() => {
    if (isStreaming || msg.role !== "ai" || msg.structured) return;
    const { renderMathInElement } = window as any;
    if (renderMathInElement && msgRef.current) {
      renderMathInElement(msgRef.current, {
        delimiters: [
          { left: "$$", right: "$$", display: true },
          { left: "$",  right: "$",  display: false },
        ],
      });
    }
  }, [isStreaming, msg.content, msg.role, msg.structured]);

  // ── Structured card ──────────────────────────────────────────────────────────
  if (msg.structured) {
    return (
      <div ref={msgRef} className="max-w-[75%] rounded-2xl px-5 py-4 text-sm leading-relaxed bg-muted text-foreground rounded-bl-md shadow-sm">
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
        className="max-w-[75%] rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed shadow-sm gradient-primary text-primary-foreground rounded-br-md break-words"
        style={{ width: "max-content" }}
      >
        <span style={{ whiteSpace: "pre-wrap" }}>{msg.content}</span>
      </div>
    );
  }

  // ── AI bubble ────────────────────────────────────────────────────────────────

  // ── CHANGED: when the message owns a widget, only show the teacher text.
  // The widget itself is displayed in the left CanvasPanel — no duplicate.
  if (msg.hasWidget) {
    return (
      <div
        ref={msgRef}
        className="max-w-[85%] rounded-2xl px-5 py-3.5 text-[17px] leading-relaxed shadow-sm bg-muted text-foreground rounded-bl-md"
        style={{ wordBreak: "break-word" }}
      >
        {isStreaming && <StreamingDots />}
        {msg.teacher && !isStreaming && (
          <div
            style={{ whiteSpace: "normal" }}
            className="text-foreground tracking-wide
                       [&>p]:mb-3 [&>p]:last:mb-0 [&>p]:leading-relaxed
                       [&>h2]:text-base [&>h2]:font-bold [&>h2]:mt-4 [&>h2]:mb-2 [&>h2]:text-primary
                       [&>h3]:text-sm [&>h3]:font-semibold [&>h3]:mt-3 [&>h3]:mb-1
                       [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:space-y-1 [&>ul]:mb-3
                       [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:space-y-1 [&>ol]:mb-3
                       [&>strong]:font-semibold"
            dangerouslySetInnerHTML={{
              __html: (window as any).marked
                ? (window as any).marked.parse(msg.teacher)
                : msg.teacher,
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
      className="max-w-[75%] rounded-2xl px-5 py-3.5 text-[15px] leading-relaxed shadow-sm bg-muted text-foreground rounded-bl-md"
      style={{ wordBreak: "break-word" }}
    >
      {isStreaming && <StreamingDots />}

      {msg.teacher && !isStreaming && (
        <div
          style={{ whiteSpace: "normal" }}
          className="text-foreground tracking-wide mb-3
                     [&>p]:mb-3 [&>p]:last:mb-0 [&>p]:leading-relaxed
                     [&>h2]:text-base [&>h2]:font-bold [&>h2]:mt-4 [&>h2]:mb-2 [&>h2]:text-primary
                     [&>h3]:text-sm [&>h3]:font-semibold [&>h3]:mt-3 [&>h3]:mb-1
                     [&>ul]:list-disc [&>ul]:pl-5 [&>ul]:space-y-1 [&>ul]:mb-3
                     [&>ol]:list-decimal [&>ol]:pl-5 [&>ol]:space-y-1 [&>ol]:mb-3
                     [&>strong]:font-semibold"
          dangerouslySetInnerHTML={{
            __html: (window as any).marked
              ? (window as any).marked.parse(msg.teacher)
              : msg.teacher,
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
        />
      ))}
    </div>
  );
});

export default MessageBubble;