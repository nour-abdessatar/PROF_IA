import { supabase } from "@/integrations/supabase/client";

export const API_BASE = (import.meta.env.VITE_API_BASE as string) || "http://localhost:8001";

async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function apiGet<T = any>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { headers: { ...(await authHeaders()) } });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export async function apiPost<T = any>(path: string, body?: any): Promise<T> {
  const isForm = body instanceof FormData;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      ...(await authHeaders()),
      ...(isForm ? {} : { "Content-Type": "application/json" }),
    },
    body: isForm ? body : body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

// ---------- High-level API helpers ----------

export interface AskResponse {
  answer: string;
  references?: any[];
  [k: string]: any;
}

export interface HistoryMessage {
  id: string;
  role: "user" | "ai";
  content: string;
  representation?: string;
}

export const ask = (question: string, session_id: string) =>
  apiPost<AskResponse>("/ask", { question, session_id });

// ── Original HTTP streaming — kept intact for any other callers ───────────────
export async function askStream(
  question: string,
  session_id: string,
  onToken: (token: string) => void,
  history: HistoryMessage[] = [],
): Promise<void> {
  const headers = {
    ...(await authHeaders()),
    "Content-Type": "application/json",
  };

  const mappedHistory = history
    .filter(m => m.content.trim() !== "")
    .map(m => ({
      role: m.role === "ai" ? "model" : "user",
      content: m.content,
      representation: m.representation,
    }));

  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, session_id, history: mappedHistory }),
  });

  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    onToken(decoder.decode(value));
  }
}

// ── SSE chat streaming — replaces askStreamWs ─────────────────────────────────
//
// Consumes the SSE events produced by /ask/stream (text/event-stream).
// Event types:
//   {"type": "widget_chunk", "text": "..."}   — incremental widget JSON fragment (progress signal only, not renderable)
//   {"type": "teacher_chunk", "text": "..."}  — incremental teacher text, safe to append to the visible message live
//   {"type": "audio_chunk", "index": N, "text": "...", "audio_base64": "...", "alignment": {...} | null}
//                                               — one synthesized [SPEECH] sentence, in submission order (see
//                                                 tts_chunker.py — the backend already guarantees these arrive
//                                                 index-ascending, no client-side reordering needed)
//   {"type": "audio_done", "count": N}         — every audio_chunk for this turn has been sent (queue drained
//                                                 server-side); NOT the same as "the last chunk finished playing"
//                                                 client-side — playback of the last chunk can still be in progress
//   {"type": "done", "widget": "...", "teacher": "..."}  — final split output, source of truth to reconcile against
//   {"type": "stream_end"}                     — generation complete
//   {"type": "error", "message": "..."}        — server error

export interface AudioChunkPayload {
  index: number;
  text: string;
  audio_base64: string;
  alignment?: unknown;
}

export interface AskStreamSseCallbacks {
  onToken       : (token: string) => void;
  onTeacherToken?: (token: string) => void;
  // NEW: audio_chunk/audio_done wiring — see stream_handler.py's
  // SpeechChunk branch (v2.0) and tts_chunker.py. Previously these two
  // SSE event types had no case in the switch below at all and fell
  // through to `default: break` — every audio_chunk the backend ever
  // sent was silently dropped, so no audio ever reached the client
  // regardless of what the backend did.
  onAudioChunk?  : (chunk: AudioChunkPayload) => void;
  onAudioDone?   : (count: number) => void;
  onDone         : (widget: string, teacher: string, representation?: string) => void;
  onError?       : (message: string) => void;
}

export async function askStreamSse(
  question  : string,
  session_id: string,
  history   : HistoryMessage[],
  callbacks : AskStreamSseCallbacks,
): Promise<void> {
  const headers = {
    ...(await authHeaders()),
    "Content-Type": "application/json",
  };

  const mappedHistory = history
    .filter(m => m.content.trim() !== "")
    .map(m => ({
      role          : m.role === "ai" ? "model" : "user",
      content       : m.content,
      representation: m.representation,
    }));

  const res = await fetch(`${API_BASE}/ask/stream`, {
    method: "POST",
    headers,
    body: JSON.stringify({ question, session_id, history: mappedHistory }),
  });

  if (!res.ok) {
    const text = await res.text();
    callbacks.onError?.(`${res.status} ${text}`);
    throw new Error(`${res.status} ${text}`);
  }

  const reader  = res.body!.getReader();
  const decoder = new TextDecoder();
  let   buffer  = "";
  // FIX (silent truncation): tracks whether a "done" SSE event was ever
  // actually received. Without this, if the HTTP response stream closes
  // early — backend crash mid-generation, proxy/timeout cutting the
  // connection, server restart, etc. — reader.read() eventually just
  // returns { done: true } on its own, identical to a normal successful
  // end of stream. The while loop below exits via `break`, no exception
  // is ever thrown, and askStreamSse resolves as if everything succeeded.
  // Chat.tsx's try/catch around askStreamSse never fires, so the message
  // is silently left with whatever partial teacher_chunk text had
  // accumulated so far — no error shown, no "❌" prefix, and that partial
  // fragment gets saved to Supabase as if it were the complete answer.
  // This is what produced old conversations with clean-looking but
  // truncated `teacher` text and no error trace anywhere. See the
  // `if (!sawDone)` check after the loop below for the actual fix.
  let   sawDone = false;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE lines are separated by double newline: "data: {...}\n\n"
    const parts = buffer.split("\n\n");
    // Keep the last incomplete chunk in the buffer
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      const line = part.trim();
      if (!line.startsWith("data:")) continue;

      const jsonStr = line.slice("data:".length).trim();
      let msg: Record<string, any>;
      try {
        msg = JSON.parse(jsonStr);
      } catch {
        // Malformed line — skip
        continue;
      }

      switch (msg.type) {
        case "widget_chunk":
          // Raw partial widget JSON — passed to onToken so TypingIndicator
          // stays active; Chat.tsx intentionally ignores the token value.
          if (typeof msg.text === "string") {
            callbacks.onToken(msg.text);
          }
          break;

        // FIX (live streaming): new event type — see stream_handler.py
        // v3.2. Previously any event type not explicitly handled here
        // silently fell through to `default: break`, so even once the
        // backend started emitting incremental teacher text there was
        // nothing on this side to pick it up. Routed to its own callback
        // (not onToken) so Chat.tsx can append it to the visible message
        // without it being confused with — or mixed together with —
        // raw widget_chunk JSON fragments.
        case "teacher_chunk":
          if (typeof msg.text === "string") {
            callbacks.onTeacherToken?.(msg.text);
          }
          break;

        // NEW: one synthesized [SPEECH] sentence. Forwarded as-is —
        // Chat.tsx owns the playback queue/ordering logic, this layer
        // only unwraps the SSE envelope.
        case "audio_chunk":
          if (typeof msg.audio_base64 === "string") {
            callbacks.onAudioChunk?.({
              index       : typeof msg.index === "number" ? msg.index : 0,
              text        : typeof msg.text  === "string" ? msg.text  : "",
              audio_base64: msg.audio_base64,
              alignment   : msg.alignment,
            });
          }
          break;

        // NEW: server-side "no more audio_chunk events coming this turn"
        // signal. Does not by itself mean playback has finished client-side
        // — Chat.tsx's queue may still have chunks buffered/playing.
        case "audio_done":
          callbacks.onAudioDone?.(typeof msg.count === "number" ? msg.count : 0);
          break;

        case "done":
          sawDone = true;
          callbacks.onDone(
            typeof msg.widget         === "string" ? msg.widget         : "",
            typeof msg.teacher        === "string" ? msg.teacher        : "",
            typeof msg.representation === "string" ? msg.representation : undefined,
          );
          break;

        case "stream_end":
          // Generation complete — nothing extra to do, Promise resolves below.
          break;

        case "error":
          callbacks.onError?.(
            typeof msg.message === "string" ? msg.message : "Unknown server error",
          );
          throw new Error(
            typeof msg.message === "string" ? msg.message : "Unknown server error",
          );

        default:
          break;
      }
    }
  }

  // FIX (silent truncation, continued): the stream ended (reader signaled
  // done) but we never saw a "done" SSE event from the backend — the
  // connection was cut short partway through generation. Previously this
  // fell through and the function resolved normally, leaving Chat.tsx's
  // try/catch none the wiser and the message stuck with only whatever
  // partial teacher_chunk text arrived before the cutoff. Surface it as a
  // real error instead, so the caller's catch block runs and the message
  // is visibly marked as failed/incomplete rather than silently saved as
  // if it were the final, complete answer.
  if (!sawDone) {
    const message = "Stream ended before completion";
    callbacks.onError?.(message);
    throw new Error(message);
  }
}

// ── toGeminiHistory ───────────────────────────────────────────────────────────
export function toGeminiHistory(
  messages: Array<{ role: "user" | "ai"; content: string }>,
): Array<{ role: "user" | "model"; content: string }> {
  return messages
    .filter(m => m.content.trim() !== "")
    .map(m => ({
      role   : m.role === "ai" ? "model" as const : "user" as const,
      content: m.content,
    }));
}

export const uploadCourse = (file: File, extra?: Record<string, string>) => {
  const fd = new FormData();
  fd.append("file", file);
  if (extra) Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  return apiPost("/upload-course", fd);
};

export const uploadTest = (file: File, extra?: Record<string, string>) => {
  const fd = new FormData();
  fd.append("file", file);
  if (extra) Object.entries(extra).forEach(([k, v]) => fd.append(k, v));
  return apiPost("/upload-test", fd);
};

export const submitAnswers = (answers: any, session_id: string) =>
  apiPost("/submit-answers", { answers, session_id });

export const getResults = (session_id: string) =>
  apiGet(`/results/${session_id}`);

export const fetchReferences = (sources: any[], course_id = "tunisian_college") =>
  apiPost<{ references: any[] }>("/references", { sources, course_id });

export const AVATAR_WS_URL =
  (import.meta.env.VITE_AVATAR_WS as string) ||
  API_BASE.replace(/^http/, "ws") + "/ws/avatar";